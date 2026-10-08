from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, HttpUrl
from pymongo import DESCENDING
from referencing import Registry, Resource

from .normalization import IngredientDictionary, normalize_ingredient
from .source_record import make_source_record
from .storage import MongoStore, without_id
from .util import clean_text, content_hash, utc_now


class IngredientInput(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class RecipeInput(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    language: Literal["tr", "en"] = "tr"
    description: str | None = Field(default=None, max_length=3000)
    category: str | None = Field(default=None, max_length=120)
    cuisine: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=30)
    servings: int | None = Field(default=None, ge=1, le=1000)
    prepMinutes: int | None = Field(default=None, ge=0, le=10080)
    cookMinutes: int | None = Field(default=None, ge=0, le=10080)
    ingredients: list[IngredientInput] = Field(min_length=1, max_length=100)
    instructions: list[str] = Field(min_length=1, max_length=100)
    imageUrl: HttpUrl | None = None
    imageSourceUrl: HttpUrl | None = None
    imageAuthor: str | None = Field(default=None, max_length=180)
    imageLicense: str | None = Field(default=None, max_length=120)
    imageLicenseUrl: HttpUrl | None = None


class Page(BaseModel):
    items: list[dict[str, Any]]
    page: int
    pageSize: int
    total: int
    pages: int


def _validator(name: str) -> Draft202012Validator:
    root = Path(__file__).resolve().parents[2] / "contracts"
    json_module = __import__("json")
    schemas = [json_module.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.schema.json")]
    registry = Registry()
    for schema in schemas:
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    target = next(schema for schema in schemas if schema["$id"].endswith(f"/{name}"))
    return Draft202012Validator(target, registry=registry)


RECIPE_VALIDATOR = _validator("recipe.schema.json")


def _validate_recipe(recipe: dict[str, Any]) -> None:
    errors = sorted(RECIPE_VALIDATOR.iter_errors(recipe), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path) or "recipe"
        raise ValueError(f"{location}: {error.message}")


def _build_payload(data: RecipeInput, source_ref: dict[str, str]) -> dict[str, Any]:
    dictionary = IngredientDictionary.load()
    ingredients = [normalize_ingredient(clean_text(item.text), data.language, dictionary) for item in data.ingredients]
    steps = [clean_text(step) for step in data.instructions if clean_text(step)]
    if not steps:
        raise ValueError("En az bir dolu tarif adımı gereklidir")
    image_complete = all((data.imageUrl, data.imageAuthor, data.imageLicense, data.imageLicenseUrl))
    image = None
    if data.imageUrl:
        image = {
            "url": str(data.imageUrl),
            "sourceUrl": str(data.imageSourceUrl or data.imageUrl),
            "author": data.imageAuthor,
            "license": data.imageLicense,
            "licenseUrl": str(data.imageLicenseUrl) if data.imageLicenseUrl else None,
            "displayAllowed": image_complete,
            "reason": None if image_complete else "Görsel lisansı, lisans URL'si ve üreticisi eksik",
        }
    identity = {
        "title": clean_text(data.title).casefold(), "language": data.language,
        "ingredients": [item["originalText"].casefold() for item in ingredients],
        "instructions": [step.casefold() for step in steps],
    }
    total = None
    if data.prepMinutes is not None or data.cookMinutes is not None:
        total = (data.prepMinutes or 0) + (data.cookMinutes or 0)
    payload = {
        "schemaVersion": "1.0.0", "sourceRef": source_ref, "language": data.language,
        "title": clean_text(data.title), "description": clean_text(data.description),
        "category": clean_text(data.category), "cuisine": clean_text(data.cuisine),
        "tags": sorted({clean_text(tag) for tag in data.tags if clean_text(tag)}),
        "servings": {"count": data.servings} if data.servings else None,
        "times": {"prepMinutes": data.prepMinutes, "cookMinutes": data.cookMinutes, "totalMinutes": total},
        "instructions": [{"position": index, "text": text} for index, text in enumerate(steps, 1)],
        "ingredients": ingredients, "image": image, "recipeGroupId": None,
        "normalizationVersion": "admin@1.0.0", "contentHash": content_hash(identity),
        "attribution": {"text": "Cook All editoryal içeriği", "url": "https://cookall.local/", "required": False},
    }
    _validate_recipe(payload)
    return payload


class RecipeService:
    def __init__(self, store: MongoStore) -> None:
        self.store = store

    def list(self, page: int, page_size: int, search: str | None, language: str | None, source: str | None, archived: bool) -> Page:
        query: dict[str, Any] = {"archivedAt": {"$ne": None} if archived else None}
        if search:
            query["payload.title"] = {"$regex": re.escape(search), "$options": "i"}
        if language:
            query["payload.language"] = language
        if source:
            query["sourceKey"] = source
        total = self.store.db.recipes.count_documents(query)
        cursor = self.store.db.recipes.find(query).sort("updatedAt", DESCENDING).skip((page - 1) * page_size).limit(page_size)
        items = []
        for row in cursor:
            items.append({**row["payload"], "archivedAt": row.get("archivedAt"), "createdAt": row.get("createdAt"), "updatedAt": row.get("updatedAt")})
        return Page(items=items, page=page, pageSize=page_size, total=total, pages=(total + page_size - 1) // page_size)

    def get(self, source_key: str, source_recipe_id: str) -> dict[str, Any] | None:
        row = self.store.db.recipes.find_one({"sourceKey": source_key, "sourceRecipeId": source_recipe_id})
        if not row:
            return None
        return {**row["payload"], "archivedAt": row.get("archivedAt"), "createdAt": row.get("createdAt"), "updatedAt": row.get("updatedAt")}

    def revisions(self, source_key: str, source_recipe_id: str) -> list[dict[str, Any]] | None:
        key = {"sourceKey": source_key, "sourceRecipeId": source_recipe_id}
        current = self.store.db.recipes.find_one(key)
        if not current:
            return None
        history = list(self.store.db.recipeRevisions.find(key).sort("createdAt", DESCENDING))
        versions = [{
            "id": "current",
            "kind": "current",
            "createdAt": current.get("updatedAt") or current.get("createdAt"),
            "payload": current["payload"],
        }]
        versions.extend({
            "id": str(row["_id"]),
            "kind": "revision",
            "createdAt": row.get("createdAt"),
            "reason": row.get("reason"),
            "payload": row["payload"],
        } for row in history)
        return versions

    def create(self, data: RecipeInput) -> dict[str, Any]:
        recipe_id = str(uuid.uuid4())
        raw = data.model_dump(mode="json")
        source = make_source_record(
            source_key="admin_manual", source_recipe_id=recipe_id,
            source_url=f"https://cookall.local/admin/recipes/{recipe_id}", language=data.language,
            revision="1", raw_payload=raw,
            license_data={"name": "Cook All editorial", "url": "https://cookall.local/terms", "scope": "manual recipe", "verified": True, "verifiedAt": utc_now()[:10]},
            attribution={"text": "Cook All editoryal içeriği", "url": "https://cookall.local/", "required": False},
        )
        payload = _build_payload(data, {"sourceKey": "admin_manual", "sourceRecipeId": recipe_id, "sourceContentHash": source["contentHash"]})
        self.store.upsert_source(source)
        self.store.upsert_recipe(payload)
        return payload

    def update(self, source_key: str, source_recipe_id: str, data: RecipeInput) -> dict[str, Any] | None:
        key = {"sourceKey": source_key, "sourceRecipeId": source_recipe_id}
        existing = self.store.db.recipes.find_one(key)
        if not existing:
            return None
        payload = _build_payload(data, existing["payload"]["sourceRef"])
        self.store.db.recipeRevisions.insert_one({
            **key, "payload": existing["payload"], "createdAt": utc_now(), "reason": "admin_update"
        })
        self.store.db.recipes.update_one(key, {"$set": {"payload": payload, "updatedAt": utc_now()}})
        return payload

    def set_archived(self, source_key: str, source_recipe_id: str, archived: bool) -> bool:
        result = self.store.db.recipes.update_one(
            {"sourceKey": source_key, "sourceRecipeId": source_recipe_id},
            {"$set": {"archivedAt": utc_now() if archived else None, "updatedAt": utc_now()}},
        )
        return result.matched_count == 1


def page_collection(collection: Any, query: dict[str, Any], page: int, page_size: int, sort: str) -> Page:
    total = collection.count_documents(query)
    rows = collection.find(query).sort(sort, DESCENDING).skip((page - 1) * page_size).limit(page_size)
    return Page(
        items=[without_id(row) or {} for row in rows], page=page, pageSize=page_size,
        total=total, pages=(total + page_size - 1) // page_size,
    )
