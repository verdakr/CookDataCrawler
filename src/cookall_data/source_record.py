from __future__ import annotations

from typing import Any

from .util import content_hash, utc_now


def make_source_record(
    *,
    source_key: str,
    source_recipe_id: str,
    source_url: str,
    language: str,
    raw_payload: dict[str, Any],
    revision: str | None,
    license_data: dict[str, Any],
    attribution: dict[str, Any],
    fetched_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "sourceKey": source_key,
        "sourceRecipeId": str(source_recipe_id),
        "sourceUrl": source_url,
        "language": language,
        "fetchedAt": fetched_at or utc_now(),
        "revision": revision,
        "rawPayload": raw_payload,
        "contentHash": content_hash(raw_payload),
        "license": license_data,
        "attribution": attribution,
    }

