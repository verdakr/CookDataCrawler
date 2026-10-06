from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.database import Database

from .util import utc_now


COLLECTIONS = (
    "sourceRecords", "sourceRevisions", "recipes", "recipeRevisions",
    "reviewQueue", "etlRuns", "collectionCheckpoints", "crawlerJobs", "crawlerJobEvents",
)


def without_id(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    result = dict(value)
    result.pop("_id", None)
    return result


class MongoStore:
    """Persistence boundary shared by CLI, worker and HTTP use-cases."""

    def __init__(self, uri: str, db_name: str) -> None:
        self.client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.db: Database = self.client[db_name]

    def __enter__(self) -> "MongoStore":
        self.client.admin.command("ping")
        self.ensure_indexes()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def ensure_indexes(self) -> None:
        self.db.sourceRecords.create_index([("sourceKey", 1), ("sourceRecipeId", 1)], unique=True)
        self.db.sourceRevisions.create_index(
            [("sourceKey", 1), ("sourceRecipeId", 1), ("contentHash", 1)], unique=True
        )
        self.db.recipes.create_index([("sourceKey", 1), ("sourceRecipeId", 1)], unique=True)
        self.db.recipes.create_index("payload.contentHash")
        self.db.recipes.create_index([("payload.language", 1), ("payload.title", 1)])
        self.db.recipes.create_index("archivedAt")
        self.db.recipeRevisions.create_index([("sourceKey", 1), ("sourceRecipeId", 1), ("createdAt", -1)])
        self.db.reviewQueue.create_index("fingerprint", unique=True)
        self.db.reviewQueue.create_index([("status", 1), ("createdAt", -1)])
        self.db.etlRuns.create_index("runId", unique=True)
        self.db.collectionCheckpoints.create_index("checkpointKey", unique=True)
        self.db.crawlerJobs.create_index("jobId", unique=True)
        self.db.crawlerJobs.create_index([("status", 1), ("createdAt", 1)])
        self.db.crawlerJobEvents.create_index([("jobId", 1), ("sequence", 1)], unique=True)

    def begin_run(self, source_key: str, normalization_version: str) -> str:
        run_id = str(uuid.uuid4())
        self.db.etlRuns.insert_one({
            "runId": run_id, "sourceKey": source_key, "startedAt": utc_now(), "finishedAt": None,
            "normalizationVersion": normalization_version,
            "counts": {"read": 0, "inserted": 0, "updated": 0, "unchanged": 0, "rejected": 0},
            "errors": [],
        })
        return run_id

    def finish_run(self, run_id: str, counts: dict[str, int], errors: list[str]) -> None:
        self.db.etlRuns.update_one(
            {"runId": run_id}, {"$set": {"finishedAt": utc_now(), "counts": counts, "errors": errors}}
        )

    def upsert_source(self, record: dict[str, Any]) -> str:
        key = {"sourceKey": record["sourceKey"], "sourceRecipeId": record["sourceRecipeId"]}
        existing = self.db.sourceRecords.find_one(key)
        now = utc_now()
        if existing is None:
            self.db.sourceRecords.insert_one({**record, "firstSeenAt": now, "lastSeenAt": now})
            return "inserted"
        if existing["contentHash"] == record["contentHash"]:
            self.db.sourceRecords.update_one(key, {"$set": {"lastSeenAt": now}})
            return "unchanged"
        revision = without_id(existing) or {}
        revision["replacedAt"] = now
        revision.pop("firstSeenAt", None)
        revision.pop("lastSeenAt", None)
        self.db.sourceRevisions.update_one(
            {**key, "contentHash": existing["contentHash"]}, {"$setOnInsert": revision}, upsert=True
        )
        self.db.sourceRecords.update_one(
            key, {"$set": {**record, "firstSeenAt": existing["firstSeenAt"], "lastSeenAt": now}}
        )
        return "updated"

    def upsert_recipe(self, recipe: dict[str, Any]) -> None:
        ref = recipe["sourceRef"]
        key = {"sourceKey": ref["sourceKey"], "sourceRecipeId": ref["sourceRecipeId"]}
        now = utc_now()
        self.db.recipes.update_one(
            key,
            {"$set": {"payload": recipe, "updatedAt": now}, "$setOnInsert": {"createdAt": now, "archivedAt": None}},
            upsert=True,
        )

    def enqueue_review(self, review_type: str, source_ref: dict[str, str], fingerprint: str, details: dict[str, Any]) -> None:
        now = utc_now()
        self.db.reviewQueue.update_one(
            {"fingerprint": fingerprint},
            {"$set": {"details": details, "status": "pending", "updatedAt": now}, "$setOnInsert": {
                "reviewType": review_type, "sourceKey": source_ref["sourceKey"],
                "sourceRecipeId": source_ref["sourceRecipeId"], "createdAt": now,
            }},
            upsert=True,
        )

    def commit(self) -> None:
        return None

    def source_ids(self, source_key: str, id_prefix: str | None = None) -> set[str]:
        query: dict[str, Any] = {"sourceKey": source_key}
        if id_prefix:
            query["sourceRecipeId"] = {"$regex": f"^{id_prefix}"}
        return {row["sourceRecipeId"] for row in self.db.sourceRecords.find(query, {"sourceRecipeId": 1})}

    def checkpoint(self, checkpoint_key: str) -> dict[str, Any] | None:
        row = self.db.collectionCheckpoints.find_one({"checkpointKey": checkpoint_key})
        if not row:
            return None
        return {"continuation": row.get("continuation"), "exhausted": row.get("exhausted", False), "updated_at": row.get("updatedAt")}

    def save_checkpoint(self, checkpoint_key: str, continuation: str | None, exhausted: bool) -> None:
        self.db.collectionCheckpoints.update_one(
            {"checkpointKey": checkpoint_key},
            {"$set": {"continuation": continuation, "exhausted": exhausted, "updatedAt": utc_now()}}, upsert=True,
        )

    def clear_checkpoint(self, checkpoint_key: str) -> None:
        self.db.collectionCheckpoints.delete_one({"checkpointKey": checkpoint_key})

    def recipes(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        query = {} if include_archived else {"archivedAt": None}
        return [row["payload"] for row in self.db.recipes.find(query).sort([("payload.language", 1), ("sourceRecipeId", 1)])]

    def source_records(self) -> list[dict[str, Any]]:
        return [without_id(row) or {} for row in self.db.sourceRecords.find().sort([("sourceKey", 1), ("sourceRecipeId", 1)])]

    def runs(self) -> list[dict[str, Any]]:
        return [without_id(row) or {} for row in self.db.etlRuns.find().sort("startedAt", 1)]

    def reviews(self) -> list[dict[str, Any]]:
        return [without_id(row) or {} for row in self.db.reviewQueue.find().sort("createdAt", 1)]

    def claim_next_job(self, worker_id: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        return self.db.crawlerJobs.find_one_and_update(
            {"status": "queued"},
            {"$set": {"status": "running", "workerId": worker_id, "startedAt": utc_now(), "leaseUntil": now.timestamp() + lease_seconds}},
            sort=[("createdAt", ASCENDING)], return_document=ReturnDocument.AFTER,
        )


Phase0Store = MongoStore
