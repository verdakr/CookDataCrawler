from __future__ import annotations

from typing import Any, Callable, Protocol

from .dedup import find_duplicate_candidates
from .storage import Phase0Store
from .validation import ValidationError, validate_recipe, validate_source_record


class Adapter(Protocol):
    source_key: str
    dictionary: Any
    def collect(self, limit: int): ...


class CollectionCancelled(RuntimeError):
    pass


def run_collection(
    adapter: Adapter,
    store: Phase0Store,
    limit: int,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    counts = {"read": 0, "inserted": 0, "updated": 0, "unchanged": 0, "rejected": 0}
    errors: list[str] = []
    run_id = store.begin_run(adapter.source_key, adapter.dictionary.version)
    try:
        for source, recipe in adapter.collect(limit):
            if should_cancel and should_cancel():
                raise CollectionCancelled("Crawler işi kullanıcı tarafından durduruldu")
            counts["read"] += 1
            try:
                validate_source_record(source)
                validate_recipe(recipe)
                outcome = store.upsert_source(source)
                counts[outcome] += 1
                store.upsert_recipe(recipe)
                for position, item in enumerate(recipe["ingredients"], 1):
                    if item["reviewRequired"]:
                        fingerprint = f"ingredient:{source['sourceKey']}:{source['sourceRecipeId']}:{position}:{item['ruleVersion']}"
                        store.enqueue_review("ingredient_normalization", recipe["sourceRef"], fingerprint, {"position": position, "ingredient": item})
                store.commit()
                if on_event:
                    on_event("progress", {"sourceRecipeId": source["sourceRecipeId"], "counts": dict(counts)})
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                counts["rejected"] += 1
                errors.append(f"{source.get('sourceRecipeId', 'unknown')}: {exc}")
        candidates = find_duplicate_candidates(store.recipes())
        for candidate in candidates:
            store.enqueue_review("duplicate_candidate", candidate["left"], candidate["fingerprint"], candidate)
        store.commit()
    except Exception as exc:
        errors.append(f"collection aborted: {exc}")
        raise
    finally:
        store.finish_run(run_id, counts, errors)
    result = {"runId": run_id, "sourceKey": adapter.source_key, **counts, "errors": errors}
    if hasattr(adapter, "source_exhausted"):
        result["sourceExhausted"] = bool(adapter.source_exhausted)
    return result


def reprocess_records(store: Phase0Store, adapters: dict[str, Any]) -> dict[str, int]:
    counts = {"read": 0, "updated": 0, "rejected": 0}
    store.db.reviewQueue.update_many(
        {"reviewType": "ingredient_normalization"}, {"$set": {"status": "superseded"}}
    )
    for source in store.source_records():
        adapter_key = source["sourceKey"] if source["sourceKey"] != "wikibooks_mediawiki" else f"wikibooks_mediawiki:{source['language']}"
        adapter = adapters.get(adapter_key)
        if adapter is None:
            counts["rejected"] += 1
            continue
        counts["read"] += 1
        try:
            recipe = adapter.to_recipe(source)
            validate_recipe(recipe)
            store.upsert_recipe(recipe)
            for position, item in enumerate(recipe["ingredients"], 1):
                if item["reviewRequired"]:
                    fingerprint = f"ingredient:{source['sourceKey']}:{source['sourceRecipeId']}:{position}:{item['ruleVersion']}"
                    store.enqueue_review("ingredient_normalization", recipe["sourceRef"], fingerprint, {"position": position, "ingredient": item})
            counts["updated"] += 1
        except (ValidationError, KeyError, TypeError, ValueError):
            counts["rejected"] += 1
    store.commit()
    return counts
