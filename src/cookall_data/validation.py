from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    pass


def require_fields(value: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if value.get(field) in (None, "", [])]
    if missing:
        raise ValidationError(f"{label} missing required fields: {', '.join(missing)}")


def validate_source_record(record: dict[str, Any]) -> None:
    require_fields(
        record,
        ("schemaVersion", "sourceKey", "sourceRecipeId", "sourceUrl", "language", "fetchedAt", "rawPayload", "contentHash", "license", "attribution"),
        "sourceRecord",
    )
    if not str(record["contentHash"]).startswith("sha256:"):
        raise ValidationError("sourceRecord contentHash must use sha256")


def validate_recipe(recipe: dict[str, Any]) -> None:
    require_fields(
        recipe,
        ("schemaVersion", "sourceRef", "language", "title", "instructions", "ingredients", "contentHash", "normalizationVersion"),
        "recipe",
    )
    if not all(step.get("position") and step.get("text") for step in recipe["instructions"]):
        raise ValidationError("recipe instructions must be ordered, non-empty steps")
    if not all(item.get("originalText") for item in recipe["ingredients"]):
        raise ValidationError("recipe ingredients must retain originalText")

