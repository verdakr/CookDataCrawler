from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .util import canonical_json, utc_now


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS source_records (
  source_key TEXT NOT NULL,
  source_recipe_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  revision TEXT,
  record_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY (source_key, source_recipe_id)
);
CREATE TABLE IF NOT EXISTS source_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL,
  source_recipe_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  revision TEXT,
  record_json TEXT NOT NULL,
  replaced_at TEXT NOT NULL,
  UNIQUE (source_key, source_recipe_id, content_hash)
);
CREATE TABLE IF NOT EXISTS recipes (
  source_key TEXT NOT NULL,
  source_recipe_id TEXT NOT NULL,
  language TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  recipe_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_key, source_recipe_id),
  FOREIGN KEY (source_key, source_recipe_id) REFERENCES source_records(source_key, source_recipe_id)
);
CREATE INDEX IF NOT EXISTS recipes_content_hash_idx ON recipes(content_hash);
CREATE TABLE IF NOT EXISTS review_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_type TEXT NOT NULL,
  source_key TEXT NOT NULL,
  source_recipe_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  details_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS etl_runs (
  run_id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  normalization_version TEXT NOT NULL,
  read_count INTEGER NOT NULL DEFAULT 0,
  inserted_count INTEGER NOT NULL DEFAULT 0,
  updated_count INTEGER NOT NULL DEFAULT 0,
  unchanged_count INTEGER NOT NULL DEFAULT 0,
  rejected_count INTEGER NOT NULL DEFAULT 0,
  error_summary_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS collection_checkpoints (
  checkpoint_key TEXT PRIMARY KEY,
  continuation TEXT,
  exhausted INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
"""


class Phase0Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Phase0Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def begin_run(self, source_key: str, normalization_version: str) -> str:
        run_id = str(uuid.uuid4())
        self.connection.execute(
            "INSERT INTO etl_runs(run_id, source_key, started_at, normalization_version) VALUES (?, ?, ?, ?)",
            (run_id, source_key, utc_now(), normalization_version),
        )
        self.connection.commit()
        return run_id

    def finish_run(self, run_id: str, counts: dict[str, int], errors: list[str]) -> None:
        self.connection.execute(
            """UPDATE etl_runs SET finished_at=?, read_count=?, inserted_count=?, updated_count=?,
            unchanged_count=?, rejected_count=?, error_summary_json=? WHERE run_id=?""",
            (utc_now(), counts["read"], counts["inserted"], counts["updated"], counts["unchanged"], counts["rejected"], canonical_json(errors), run_id),
        )
        self.connection.commit()

    def upsert_source(self, record: dict[str, Any]) -> str:
        key = (record["sourceKey"], record["sourceRecipeId"])
        existing = self.connection.execute(
            "SELECT * FROM source_records WHERE source_key=? AND source_recipe_id=?", key
        ).fetchone()
        now = utc_now()
        if existing is None:
            self.connection.execute(
                """INSERT INTO source_records(source_key,source_recipe_id,content_hash,revision,record_json,first_seen_at,last_seen_at)
                VALUES(?,?,?,?,?,?,?)""",
                (*key, record["contentHash"], record.get("revision"), canonical_json(record), now, now),
            )
            return "inserted"
        if existing["content_hash"] == record["contentHash"]:
            self.connection.execute(
                "UPDATE source_records SET last_seen_at=? WHERE source_key=? AND source_recipe_id=?",
                (now, *key),
            )
            return "unchanged"
        self.connection.execute(
            """INSERT OR IGNORE INTO source_revisions(source_key,source_recipe_id,content_hash,revision,record_json,replaced_at)
            VALUES(?,?,?,?,?,?)""",
            (*key, existing["content_hash"], existing["revision"], existing["record_json"], now),
        )
        self.connection.execute(
            """UPDATE source_records SET content_hash=?,revision=?,record_json=?,last_seen_at=?
            WHERE source_key=? AND source_recipe_id=?""",
            (record["contentHash"], record.get("revision"), canonical_json(record), now, *key),
        )
        return "updated"

    def upsert_recipe(self, recipe: dict[str, Any]) -> None:
        ref = recipe["sourceRef"]
        self.connection.execute(
            """INSERT INTO recipes(source_key,source_recipe_id,language,content_hash,recipe_json,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(source_key,source_recipe_id) DO UPDATE SET
            language=excluded.language,content_hash=excluded.content_hash,recipe_json=excluded.recipe_json,updated_at=excluded.updated_at""",
            (ref["sourceKey"], ref["sourceRecipeId"], recipe["language"], recipe["contentHash"], canonical_json(recipe), utc_now()),
        )

    def enqueue_review(self, review_type: str, source_ref: dict[str, str], fingerprint: str, details: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO review_queue(review_type,source_key,source_recipe_id,fingerprint,details_json,created_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
            details_json=excluded.details_json,status='pending'""",
            (review_type, source_ref["sourceKey"], source_ref["sourceRecipeId"], fingerprint, canonical_json(details), utc_now()),
        )

    def commit(self) -> None:
        self.connection.commit()

    def source_ids(self, source_key: str, id_prefix: str | None = None) -> set[str]:
        if id_prefix is None:
            rows = self.connection.execute(
                "SELECT source_recipe_id FROM source_records WHERE source_key=?", (source_key,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT source_recipe_id FROM source_records WHERE source_key=? AND source_recipe_id LIKE ?",
                (source_key, f"{id_prefix}%"),
            ).fetchall()
        return {row[0] for row in rows}

    def checkpoint(self, checkpoint_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT continuation, exhausted, updated_at FROM collection_checkpoints WHERE checkpoint_key=?",
            (checkpoint_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_checkpoint(self, checkpoint_key: str, continuation: str | None, exhausted: bool) -> None:
        self.connection.execute(
            """INSERT INTO collection_checkpoints(checkpoint_key,continuation,exhausted,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(checkpoint_key) DO UPDATE SET
            continuation=excluded.continuation,exhausted=excluded.exhausted,updated_at=excluded.updated_at""",
            (checkpoint_key, continuation, int(exhausted), utc_now()),
        )
        self.connection.commit()

    def clear_checkpoint(self, checkpoint_key: str) -> None:
        self.connection.execute("DELETE FROM collection_checkpoints WHERE checkpoint_key=?", (checkpoint_key,))
        self.connection.commit()

    def recipes(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT recipe_json FROM recipes ORDER BY language, source_key, source_recipe_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def source_records(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT record_json FROM source_records ORDER BY source_key, source_recipe_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def runs(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM etl_runs ORDER BY started_at").fetchall()
        return [dict(row) for row in rows]

    def reviews(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM review_queue ORDER BY id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result
