from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from .adapters import TheMealDBAdapter, WikibooksAdapter
from .http import HttpClient
from .normalization import IngredientDictionary
from .pipeline import CollectionCancelled, reprocess_records, run_collection
from .reporting import write_reports
from .storage import MongoStore, without_id
from .util import utc_now


JOB_KINDS = {"themealdb", "wikibooks-en", "wikibooks-tr", "reprocess", "report"}
TERMINAL = {"completed", "failed", "cancelled"}


class JobService:
    def __init__(self, store: MongoStore) -> None:
        self.store = store

    def create(self, kind: str, limit: int = 50, resume: bool = False, reset_cursor: bool = False) -> dict[str, Any]:
        if kind not in JOB_KINDS:
            raise ValueError("Desteklenmeyen iş türü")
        if not 1 <= limit <= 5000:
            raise ValueError("Limit 1 ile 5000 arasında olmalı")
        if resume and not kind.startswith("wikibooks-"):
            raise ValueError("Resume yalnız Wikibooks için kullanılabilir")
        if reset_cursor and not resume:
            raise ValueError("Cursor sıfırlama resume ile kullanılmalıdır")
        job = {
            "jobId": str(uuid.uuid4()), "kind": kind, "status": "queued", "createdAt": utc_now(),
            "startedAt": None, "finishedAt": None, "options": {"limit": limit, "resume": resume, "resetCursor": reset_cursor},
            "counts": {}, "result": None, "error": None, "cancelRequested": False,
        }
        self.store.db.crawlerJobs.insert_one(job)
        self.event(job["jobId"], "info", "İş kuyruğa alındı", {"kind": kind})
        return without_id(job) or {}

    def event(self, job_id: str, level: str, message: str, data: dict[str, Any] | None = None) -> None:
        last = self.store.db.crawlerJobEvents.find_one({"jobId": job_id}, sort=[("sequence", -1)])
        sequence = (last or {}).get("sequence", 0) + 1
        self.store.db.crawlerJobEvents.insert_one({
            "jobId": job_id, "sequence": sequence, "level": level, "message": message,
            "data": data or {}, "createdAt": utc_now(),
        })
        old = list(self.store.db.crawlerJobEvents.find({"jobId": job_id}, {"_id": 1}).sort("sequence", -1).skip(500))
        if old:
            self.store.db.crawlerJobEvents.delete_many({"_id": {"$in": [row["_id"] for row in old]}})

    def cancel(self, job_id: str) -> bool:
        job = self.store.db.crawlerJobs.find_one({"jobId": job_id})
        if not job or job["status"] in TERMINAL:
            return False
        if job["status"] == "queued":
            self.store.db.crawlerJobs.update_one({"jobId": job_id}, {"$set": {"status": "cancelled", "finishedAt": utc_now()}})
            self.event(job_id, "warning", "Sıradaki iş iptal edildi")
        else:
            self.store.db.crawlerJobs.update_one({"jobId": job_id}, {"$set": {"cancelRequested": True}})
            self.event(job_id, "warning", "Durdurma isteği alındı")
        return True


class CrawlerWorker:
    def __init__(self, store: MongoStore, user_agent: str | None = None, maintenance_lock: Any | None = None) -> None:
        self.store = store
        self.jobs = JobService(store)
        self.user_agent = user_agent or "CookAllAdmin/0.2 (local admin)"
        self.worker_id = str(uuid.uuid4())
        self.maintenance_lock = maintenance_lock or threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.store.db.crawlerJobs.update_many(
            {"status": "running"},
            {"$set": {"status": "queued", "startedAt": None, "workerId": None, "error": "Worker yeniden başlatıldı"}},
        )
        self.thread = threading.Thread(target=self._loop, name="cookall-crawler-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            with self.maintenance_lock:
                job = self.store.claim_next_job(self.worker_id)
            if not job:
                self.stop_event.wait(0.75)
                continue
            self._run(job)

    def _cancelled(self, job_id: str) -> bool:
        row = self.store.db.crawlerJobs.find_one({"jobId": job_id}, {"cancelRequested": 1})
        return bool(row and row.get("cancelRequested"))

    def _run(self, job: dict[str, Any]) -> None:
        job_id, kind, options = job["jobId"], job["kind"], job["options"]
        self.jobs.event(job_id, "info", "İş çalışmaya başladı")
        try:
            dictionary = IngredientDictionary.load()
            if kind == "report":
                result = {name: str(path) for name, path in write_reports(self.store, "artifacts/reports").items()}
            elif kind == "reprocess":
                adapters = {
                    "themealdb_api": TheMealDBAdapter(None, dictionary),
                    "wikibooks_mediawiki:en": WikibooksAdapter("en", None, dictionary),
                    "wikibooks_mediawiki:tr": WikibooksAdapter("tr", None, dictionary),
                }
                result = reprocess_records(self.store, adapters)
            else:
                client = HttpClient(user_agent=self.user_agent)
                if kind == "themealdb":
                    adapter = TheMealDBAdapter(client, dictionary)
                else:
                    language = kind.rsplit("-", 1)[1]
                    checkpoint_key = f"wikibooks_mediawiki:{language}"
                    if options["resetCursor"]:
                        self.store.clear_checkpoint(checkpoint_key)
                    checkpoint = self.store.checkpoint(checkpoint_key) if options["resume"] else None
                    adapter = WikibooksAdapter(
                        language, client, dictionary,
                        start_continuation=checkpoint["continuation"] if checkpoint else None,
                        skip_source_recipe_ids=self.store.source_ids("wikibooks_mediawiki", f"{language}:") if options["resume"] else set(),
                        checkpoint_callback=(lambda continuation, exhausted: self.store.save_checkpoint(checkpoint_key, continuation, exhausted)) if options["resume"] else None,
                        exhausted=bool(checkpoint and checkpoint["exhausted"]),
                    )
                result = run_collection(
                    adapter, self.store, options["limit"],
                    on_event=lambda event, data: self._progress(job_id, event, data),
                    should_cancel=lambda: self._cancelled(job_id),
                )
            self.store.db.crawlerJobs.update_one(
                {"jobId": job_id}, {"$set": {"status": "completed", "result": result, "finishedAt": utc_now(), "leaseUntil": None}}
            )
            self.jobs.event(job_id, "success", "İş tamamlandı", result)
        except CollectionCancelled as exc:
            self.store.db.crawlerJobs.update_one({"jobId": job_id}, {"$set": {"status": "cancelled", "error": str(exc), "finishedAt": utc_now()}})
            self.jobs.event(job_id, "warning", str(exc))
        except Exception as exc:
            self.store.db.crawlerJobs.update_one({"jobId": job_id}, {"$set": {"status": "failed", "error": str(exc), "finishedAt": utc_now()}})
            self.jobs.event(job_id, "error", "İş başarısız", {"error": str(exc)})

    def _progress(self, job_id: str, _: str, data: dict[str, Any]) -> None:
        self.store.db.crawlerJobs.update_one({"jobId": job_id}, {"$set": {"counts": data["counts"], "leaseUntil": time.time() + 60}})
        read = data["counts"]["read"]
        if read == 1 or read % 10 == 0:
            self.jobs.event(job_id, "info", f"{read} kayıt işlendi", data)
