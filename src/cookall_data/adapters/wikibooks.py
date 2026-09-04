from __future__ import annotations

import html
import re
from collections.abc import Iterator
from typing import Any, Callable

from ..http import HttpClient
from ..normalization import IngredientDictionary, normalize_ingredient
from ..source_record import make_source_record
from ..util import clean_text, content_hash


SITE_CONFIG = {
    "en": {
        "api": "https://en.wikibooks.org/w/api.php",
        "category": "Category:Recipes",
        "generator": "categorymembers",
        "page_prefix": "Cookbook:",
        "license_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    },
    "tr": {
        "api": "https://tr.wikibooks.org/w/api.php",
        "namespace": 100,
        "generator": "allpages",
        "page_prefix": "Yemek:",
        "license_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/deed.tr",
    },
}

INGREDIENT_HEADINGS = {
    "en": ("ingredients",),
    "tr": ("malzemeler", "gerekli malzemeler", "malzeme"),
}
INSTRUCTION_HEADINGS = {
    "en": ("procedure", "directions", "instructions", "method", "preparation"),
    "tr": (
        "hazırlanış", "hazırlanması", "hazırlama", "yapılış", "yapılacaklar",
        "pişirilmesi", "tarif",
    ),
}


class WikibooksAdapter:
    source_key = "wikibooks_mediawiki"

    def __init__(
        self,
        language: str,
        client: HttpClient,
        dictionary: IngredientDictionary,
        *,
        start_continuation: str | None = None,
        skip_source_recipe_ids: set[str] | None = None,
        checkpoint_callback: Callable[[str | None, bool], None] | None = None,
        exhausted: bool = False,
    ) -> None:
        if language not in SITE_CONFIG:
            raise ValueError(f"Unsupported Wikibooks language: {language}")
        self.language = language
        self.config = SITE_CONFIG[language]
        self.client = client
        self.dictionary = dictionary
        self.start_continuation = start_continuation
        self.skip_source_recipe_ids = skip_source_recipe_ids or set()
        self.checkpoint_callback = checkpoint_callback
        self.exhausted = exhausted
        self.source_exhausted = exhausted

    @property
    def checkpoint_key(self) -> str:
        return f"{self.source_key}:{self.language}"

    def collect(self, limit: int) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        if self.exhausted:
            return
        continuation = self.start_continuation
        emitted = 0
        while emitted < limit:
            params: dict[str, Any] = {
                "action": "query", "format": "json", "formatversion": 2,
                "prop": "info|revisions", "inprop": "url",
                "rvprop": "ids|timestamp|content", "rvslots": "main",
                "maxlag": 5,
            }
            if self.config["generator"] == "categorymembers":
                params.update({
                    "generator": "categorymembers", "gcmtitle": self.config["category"],
                    "gcmtype": "page", "gcmlimit": min(50, limit - emitted), "redirects": 1,
                })
                continuation_key = "gcmcontinue"
            else:
                params.update({
                    "generator": "allpages", "gapnamespace": self.config["namespace"],
                    "gapfilterredir": "nonredirects", "gaplimit": min(50, limit - emitted),
                })
                continuation_key = "gapcontinue"
            if continuation:
                params[continuation_key] = continuation
            payload = self.client.get_json(self.config["api"], params)
            pages = payload.get("query", {}).get("pages", [])
            for page in pages:
                if emitted >= limit:
                    break
                source_recipe_id = f"{self.language}:{page.get('pageid')}"
                if source_recipe_id in self.skip_source_recipe_ids:
                    continue
                source = self.to_source_record(page)
                recipe = self.to_recipe(source)
                # Category pages can include indexes/non-recipes; require both core sections.
                if not recipe["ingredients"] or not recipe["instructions"]:
                    continue
                emitted += 1
                yield source, recipe
            continuation = payload.get("continue", {}).get(continuation_key)
            self.source_exhausted = continuation is None
            if self.checkpoint_callback:
                self.checkpoint_callback(continuation, continuation is None)
            if not continuation:
                break

    def to_source_record(self, page: dict[str, Any]) -> dict[str, Any]:
        revision = (page.get("revisions") or [{}])[0]
        revision_id = revision.get("revid")
        page_id = str(page["pageid"])
        permanent_url = f"https://{self.language}.wikibooks.org/w/index.php?curid={page_id}&oldid={revision_id}"
        return make_source_record(
            source_key=self.source_key,
            source_recipe_id=f"{self.language}:{page_id}",
            source_url=page.get("canonicalurl") or permanent_url,
            language=self.language,
            revision=str(revision_id) if revision_id is not None else None,
            raw_payload=page,
            license_data={
                "name": self.config["license_name"],
                "url": self.config["license_url"],
                "scope": "page text at recorded revision; additional terms may apply",
                "verified": revision_id is not None,
                "verifiedAt": "2026-08-28",
            },
            attribution={
                "text": f"{page.get('title', '')} contributors, Wikibooks",
                "url": permanent_url,
                "required": True,
            },
        )

    @staticmethod
    def _wikitext(page: dict[str, Any]) -> str:
        revision = (page.get("revisions") or [{}])[0]
        slot = revision.get("slots", {}).get("main", {})
        return slot.get("content") or revision.get("content") or revision.get("*") or ""

    @staticmethod
    def _sections(wikitext: str) -> dict[str, str]:
        # Older Turkish Cookbook pages often use bold lines instead of section headings.
        recognized = (*INGREDIENT_HEADINGS["tr"], *INSTRUCTION_HEADINGS["tr"])

        def bold_to_heading(match: re.Match[str]) -> str:
            heading = clean_text(match.group(1))
            key = WikibooksAdapter._heading_key(heading)
            if any(WikibooksAdapter._heading_key(choice) in key for choice in recognized):
                return f"=={heading}=="
            return match.group(0)

        wikitext = re.sub(r"^\s*'{3}\s*(.*?)\s*'{3}\s*$", bold_to_heading, wikitext, flags=re.MULTILINE)
        parts = re.split(r"^\s*={2,6}\s*(.*?)\s*={2,6}\s*$", wikitext, flags=re.MULTILINE)
        sections: dict[str, str] = {"": parts[0] if parts else ""}
        for index in range(1, len(parts) - 1, 2):
            sections[WikibooksAdapter._heading_key(parts[index])] = parts[index + 1]
        return sections

    @staticmethod
    def _heading_key(value: str) -> str:
        return clean_text(value).casefold().translate(str.maketrans("çğıöşü", "cgiosu"))

    @staticmethod
    def _plain(value: str) -> str:
        value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
        value = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", "", value, flags=re.DOTALL | re.IGNORECASE)
        value = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", value)
        value = re.sub(r"\[(?:https?://\S+)\s+([^\]]+)\]", r"\1", value)
        value = re.sub(r"\{\{(?:convert|cvt)\|([^|}]+)\|([^|}]+).*?\}\}", r"\1 \2", value, flags=re.IGNORECASE)
        value = re.sub(r"\{\{[^{}]*\}\}", "", value)
        value = re.sub(r"'{2,}", "", value)
        value = re.sub(r"<[^>]+>", "", value)
        return clean_text(html.unescape(value))

    def _section(self, sections: dict[str, str], choices: tuple[str, ...]) -> str:
        for heading, body in sections.items():
            if any(self._heading_key(choice) in heading for choice in choices):
                return body
        return ""

    def to_recipe(self, source: dict[str, Any]) -> dict[str, Any]:
        page = source["rawPayload"]
        wikitext = self._wikitext(page)
        sections = self._sections(wikitext)
        ingredient_body = self._section(sections, INGREDIENT_HEADINGS[self.language])
        instruction_body = self._section(sections, INSTRUCTION_HEADINGS[self.language])
        ingredient_lines = []
        for line in ingredient_body.splitlines():
            if re.match(r"^\s*[*#;:]", line):
                plain = self._plain(re.sub(r"^\s*[*#;:]+\s*", "", line))
                if plain:
                    ingredient_lines.append(plain)
        ingredients = [normalize_ingredient(line, self.language, self.dictionary) for line in ingredient_lines]

        instruction_lines = []
        for line in instruction_body.splitlines():
            if re.match(r"^\s*[#*]", line):
                plain = self._plain(re.sub(r"^\s*[#*;:]+\s*", "", line))
                if plain:
                    instruction_lines.append(plain)
        if not instruction_lines:
            paragraphs = [self._plain(part) for part in re.split(r"\n\s*\n", instruction_body)]
            instruction_lines = [part for part in paragraphs if part]
        instructions = [{"position": index, "text": text} for index, text in enumerate(instruction_lines, 1)]

        full_title = clean_text(page.get("title"))
        title = full_title.split(":", 1)[-1]
        servings = self._servings(wikitext)
        times = self._times(wikitext)
        categories = [self._plain(value) for value in re.findall(r"\[\[(?:Category|Kategori):([^\]|]+)", wikitext, flags=re.IGNORECASE)]
        intro = self._plain(sections.get("", ""))
        identity = {
            "title": title.casefold(), "language": self.language,
            "ingredients": sorted({item["ingredientId"] or item["candidateSlug"] for item in ingredients}),
            "instructions": [step["text"].casefold() for step in instructions],
        }
        return {
            "schemaVersion": "1.0.0",
            "sourceRef": {"sourceKey": source["sourceKey"], "sourceRecipeId": source["sourceRecipeId"], "sourceContentHash": source["contentHash"]},
            "language": self.language,
            "title": title,
            "description": intro or None,
            "category": categories[0] if categories else None,
            "cuisine": None,
            "tags": [],
            "servings": servings,
            "times": times,
            "instructions": instructions,
            "ingredients": ingredients,
            "image": self._image_gate(page),
            "recipeGroupId": None,
            "normalizationVersion": self.dictionary.version,
            "contentHash": content_hash(identity),
            "attribution": source["attribution"],
        }

    @staticmethod
    def _image_gate(page: dict[str, Any]) -> dict[str, Any] | None:
        images = [item.get("title") for item in page.get("images", []) if item.get("title")]
        if not images:
            return None
        return {
            "url": None, "sourceUrl": None, "author": None, "license": None, "licenseUrl": None,
            "displayAllowed": False,
            "reason": "Embedded file requires separate Commons imageinfo/extmetadata verification",
            "sourceFileTitle": images[0],
        }

    def _template_value(self, wikitext: str, names: tuple[str, ...]) -> str | None:
        for name in names:
            match = re.search(rf"\|\s*{re.escape(name)}\s*=\s*([^\n|}}]+)", wikitext, flags=re.IGNORECASE)
            if match:
                return self._plain(match.group(1))
        return None

    def _servings(self, wikitext: str) -> dict[str, Any] | None:
        raw = self._template_value(wikitext, ("Porsiyon", "Servings", "Yield"))
        if not raw:
            raw_match = re.search(r"\b(\d+)\s*(?:kişilik|servings?|portions?)\b", wikitext, flags=re.IGNORECASE)
            raw = raw_match.group(0) if raw_match else None
        if not raw:
            return None
        numbers = [int(value) for value in re.findall(r"\d+", raw)]
        return {"min": numbers[0] if numbers else None, "max": numbers[-1] if numbers else None, "raw": clean_text(raw)}

    def _times(self, wikitext: str) -> dict[str, int | None]:
        raw = self._template_value(wikitext, ("Zaman", "Time", "Total time"))
        minutes: int | None = None
        if raw:
            hour = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:saat|hours?|hrs?)", raw, flags=re.IGNORECASE)
            minute = re.search(r"(\d+)\s*(?:dakika|minutes?|mins?)", raw, flags=re.IGNORECASE)
            minutes = round(float(hour.group(1).replace(",", ".")) * 60) if hour else 0
            minutes += int(minute.group(1)) if minute else 0
            if not hour and not minute:
                minutes = None
        return {"prepMinutes": None, "cookMinutes": None, "totalMinutes": minutes}
