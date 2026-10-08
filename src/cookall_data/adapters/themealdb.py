from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Callable

from ..http import HttpClient
from ..normalization import IngredientDictionary, normalize_ingredient
from ..source_record import make_source_record
from ..util import clean_text, content_hash


class TheMealDBAdapter:
    source_key = "themealdb_api"
    base_url = "https://www.themealdb.com/api/json/v1/1"

    letters = "abcdefghijklmnopqrstuvwxyz"

    def __init__(
        self,
        client: HttpClient,
        dictionary: IngredientDictionary,
        *,
        start_letter: str | None = None,
        skip_source_recipe_ids: set[str] | None = None,
        checkpoint_callback: Callable[[str | None, bool], None] | None = None,
    ) -> None:
        self.client = client
        self.dictionary = dictionary
        self.start_letter = start_letter
        self.skip_source_recipe_ids = skip_source_recipe_ids or set()
        self.checkpoint_callback = checkpoint_callback
        self.source_exhausted = False

    @property
    def checkpoint_key(self) -> str:
        return self.source_key

    def collect(self, limit: int) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        seen: set[str] = set()
        emitted = 0
        start_index = self.letters.index(self.start_letter) if self.start_letter and self.start_letter in self.letters else 0
        for index in range(start_index, len(self.letters)):
            letter = self.letters[index]
            payload = self.client.get_json(f"{self.base_url}/search.php", {"f": letter})
            for meal in payload.get("meals") or []:
                meal_id = str(meal.get("idMeal", ""))
                if not meal_id or meal_id in seen:
                    continue
                seen.add(meal_id)
                if meal_id in self.skip_source_recipe_ids:
                    continue
                source = self.to_source_record(meal)
                yield source, self.to_recipe(source)
                emitted += 1
                if emitted >= limit:
                    if self.checkpoint_callback:
                        self.checkpoint_callback(letter, False)
                    return
            next_letter = self.letters[index + 1] if index + 1 < len(self.letters) else None
            self.source_exhausted = next_letter is None
            if self.checkpoint_callback:
                self.checkpoint_callback(next_letter, self.source_exhausted)

    def to_source_record(self, meal: dict[str, Any]) -> dict[str, Any]:
        meal_id = str(meal["idMeal"])
        return make_source_record(
            source_key=self.source_key,
            source_recipe_id=meal_id,
            source_url=f"https://www.themealdb.com/meal/{meal_id}",
            language="en",
            revision=clean_text(meal.get("dateModified")) or None,
            raw_payload=meal,
            license_data={
                "name": "TheMealDB Terms of Use",
                "url": "https://www.themealdb.com/terms_of_use.php",
                "scope": "API response; development/personal use gate",
                "verified": True,
                "verifiedAt": "2026-08-28",
            },
            attribution={
                "text": "Recipe data sourced from TheMealDB",
                "url": "https://www.themealdb.com/",
                "required": True,
            },
        )

    def to_recipe(self, source: dict[str, Any]) -> dict[str, Any]:
        meal = source["rawPayload"]
        ingredients = []
        for index in range(1, 21):
            name = clean_text(meal.get(f"strIngredient{index}"))
            measure = clean_text(meal.get(f"strMeasure{index}"))
            if not name:
                continue
            original = clean_text(f"{measure} {name}")
            ingredients.append(normalize_ingredient(original, "en", self.dictionary))

        instruction_text = clean_text(meal.get("strInstructions"))
        raw_steps = [clean_text(step) for step in re.split(r"(?:\r?\n)+|(?<=\.)\s+(?=[A-Z])", instruction_text)]
        instructions = [
            {"position": index, "text": step}
            for index, step in enumerate((step for step in raw_steps if step), 1)
        ]
        if not instructions and instruction_text:
            instructions = [{"position": 1, "text": instruction_text}]

        cc_value = clean_text(meal.get("strCreativeCommonsConfirmed")).casefold()
        image_url = clean_text(meal.get("strMealThumb")) or None
        image_source = clean_text(meal.get("strImageSource")) or source["sourceUrl"]
        image_cc_verified = cc_value in {"yes", "true", "1"}
        image = {
            "url": image_url,
            "sourceUrl": image_source,
            "author": None,
            "license": "Creative Commons (exact license unspecified)" if image_cc_verified else None,
            "licenseUrl": None,
            "displayAllowed": False,
            "reason": "Exact image license and author are not record-level verifiable",
        } if image_url else None

        identity = {
            "title": clean_text(meal.get("strMeal")).casefold(),
            "language": "en",
            "ingredients": sorted({item["ingredientId"] or item["candidateSlug"] for item in ingredients}),
            "instructions": [step["text"].casefold() for step in instructions],
        }
        recipe = {
            "schemaVersion": "1.0.0",
            "sourceRef": {"sourceKey": source["sourceKey"], "sourceRecipeId": source["sourceRecipeId"], "sourceContentHash": source["contentHash"]},
            "language": "en",
            "title": clean_text(meal.get("strMeal")),
            "description": None,
            "category": clean_text(meal.get("strCategory")) or None,
            "cuisine": clean_text(meal.get("strArea")) or None,
            "tags": [clean_text(tag) for tag in (meal.get("strTags") or "").split(",") if clean_text(tag)],
            "servings": None,
            "times": {"prepMinutes": None, "cookMinutes": None, "totalMinutes": None},
            "instructions": instructions,
            "ingredients": ingredients,
            "image": image,
            "recipeGroupId": None,
            "normalizationVersion": self.dictionary.version,
            "contentHash": content_hash(identity),
            "attribution": source["attribution"],
        }
        return recipe
