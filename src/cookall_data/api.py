from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import jwt
import uvicorn
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .admin_service import Page, RecipeInput, RecipeService, page_collection
from .backup import BackupError, BackupService
from .jobs import JobService, CrawlerWorker
from .settings import Settings
from .storage import MongoStore, without_id
from .util import utc_now


COOKIE_NAME = "cookall_admin"
SESSION_SECONDS = 8 * 60 * 60
password_hasher = PasswordHasher()
login_attempts: dict[str, deque[float]] = defaultdict(deque)


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class JobInput(BaseModel):
    kind: Literal["themealdb", "wikibooks-en", "wikibooks-tr", "reprocess", "report"]
    limit: int = Field(default=50, ge=1, le=5000)
    resume: bool = False
    resetCursor: bool = False


class ReviewInput(BaseModel):
    status: Literal["pending", "approved", "rejected", "superseded"]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _store(request: Request) -> MongoStore:
    return request.app.state.store


def _decode_token(token: str | None, settings: Settings) -> dict[str, Any]:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Oturum gerekli")
    try:
        payload = jwt.decode(token, settings.auth_secret, algorithms=["HS256"], audience="cook-admin")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Oturum geçersiz veya süresi dolmuş") from exc
    if payload.get("sub") != settings.admin_username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Oturum geçersiz")
    return payload


def require_admin(request: Request, cookall_admin: Annotated[str | None, Cookie()] = None) -> dict[str, Any]:
    return _decode_token(cookall_admin, _settings(request))


def require_csrf(
    request: Request,
    payload: Annotated[dict[str, Any], Depends(require_admin)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    settings = _settings(request)
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != settings.admin_origin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin doğrulanamadı")
    if not x_csrf_token or not secrets.compare_digest(x_csrf_token, str(payload.get("csrf", ""))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF doğrulanamadı")
    return payload


def require_mutation(
    request: Request,
    payload: Annotated[dict[str, Any], Depends(require_csrf)],
):
    with request.app.state.maintenance_lock:
        yield payload


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    store = MongoStore(settings.mongodb_uri, settings.db_name)
    store.client.admin.command("ping")
    store.ensure_indexes()
    maintenance_lock = threading.Lock()
    worker = CrawlerWorker(store, os.getenv("COOKALL_USER_AGENT"), maintenance_lock)
    app.state.settings, app.state.store, app.state.worker = settings, store, worker
    app.state.maintenance_lock = maintenance_lock
    worker.start()
    try:
        yield
    finally:
        worker.stop()
        store.close()


app = FastAPI(title="Cook All Admin API", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/health")
def health(request: Request):
    _store(request).client.admin.command("ping")
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(data: LoginInput, request: Request, response: Response):
    settings = _settings(request)
    address = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = login_attempts[address]
    while attempts and attempts[0] < now - 15 * 60:
        attempts.popleft()
    if len(attempts) >= 8:
        raise HTTPException(status_code=429, detail="Çok fazla giriş denemesi; daha sonra tekrar deneyin")
    valid = data.username == settings.admin_username
    try:
        password_hasher.verify(settings.admin_password_hash, data.password)
    except (VerifyMismatchError, ValueError):
        valid = False
    if not valid:
        attempts.append(now)
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya parola hatalı")
    attempts.clear()
    csrf = secrets.token_urlsafe(32)
    token = jwt.encode(
        {"sub": settings.admin_username, "aud": "cook-admin", "csrf": csrf, "iat": int(now), "exp": int(now) + SESSION_SECONDS},
        settings.auth_secret, algorithm="HS256",
    )
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path="/",
    )
    return {"username": settings.admin_username, "csrfToken": csrf}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request, payload: Annotated[dict[str, Any], Depends(require_admin)]):
    return {"username": payload["sub"], "csrfToken": payload["csrf"]}


@app.get("/api/dashboard")
def dashboard(request: Request, _: Annotated[dict[str, Any], Depends(require_admin)]):
    db = _store(request).db
    language = list(db.recipes.aggregate([{"$match": {"archivedAt": None}}, {"$group": {"_id": "$payload.language", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]))
    source = list(db.recipes.aggregate([{"$match": {"archivedAt": None}}, {"$group": {"_id": "$sourceKey", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]))
    latest = [without_id(row) for row in db.crawlerJobs.find().sort("createdAt", -1).limit(5)]
    return {
        "counts": {"recipes": db.recipes.count_documents({"archivedAt": None}), "archived": db.recipes.count_documents({"archivedAt": {"$ne": None}}), "sources": db.sourceRecords.count_documents({}), "pendingReviews": db.reviewQueue.count_documents({"status": "pending"})},
        "languages": [{"name": row["_id"], "count": row["count"]} for row in language],
        "sources": [{"name": row["_id"], "count": row["count"]} for row in source], "latestJobs": latest,
    }


@app.get("/api/recipes", response_model=Page)
def recipes(
    request: Request, _: Annotated[dict[str, Any], Depends(require_admin)],
    page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    search: str | None = Query(None, max_length=180), language: str | None = Query(None, max_length=2),
    source: str | None = Query(None, max_length=80), archived: bool = False,
):
    language = language or None
    if language not in (None, "tr", "en"):
        raise HTTPException(status_code=422, detail="Dil filtresi 'tr' veya 'en' olmalıdır")
    return RecipeService(_store(request)).list(page, page_size, search, language, source, archived)


@app.post("/api/recipes", status_code=201)
def create_recipe(data: RecipeInput, request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    try:
        return RecipeService(_store(request)).create(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/recipes/{source_key}/{source_recipe_id}")
def recipe(source_key: str, source_recipe_id: str, request: Request, _: Annotated[dict[str, Any], Depends(require_admin)]):
    result = RecipeService(_store(request)).get(source_key, source_recipe_id)
    if not result:
        raise HTTPException(status_code=404, detail="Tarif bulunamadı")
    return result


@app.put("/api/recipes/{source_key}/{source_recipe_id}")
def update_recipe(source_key: str, source_recipe_id: str, data: RecipeInput, request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    try:
        result = RecipeService(_store(request)).update(source_key, source_recipe_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Tarif bulunamadı")
    return result


@app.get("/api/recipes/{source_key}/{source_recipe_id}/revisions")
def recipe_revisions(source_key: str, source_recipe_id: str, request: Request, _: Annotated[dict[str, Any], Depends(require_admin)]):
    versions = RecipeService(_store(request)).revisions(source_key, source_recipe_id)
    if versions is None:
        raise HTTPException(status_code=404, detail="Tarif bulunamadı")
    return {"items": versions, "changeCount": max(0, len(versions) - 1)}


@app.delete("/api/recipes/{source_key}/{source_recipe_id}")
def archive_recipe(source_key: str, source_recipe_id: str, request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    if not RecipeService(_store(request)).set_archived(source_key, source_recipe_id, True):
        raise HTTPException(status_code=404, detail="Tarif bulunamadı")
    return {"ok": True}


@app.post("/api/recipes/{source_key}/{source_recipe_id}/restore")
def restore_recipe(source_key: str, source_recipe_id: str, request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    if not RecipeService(_store(request)).set_archived(source_key, source_recipe_id, False):
        raise HTTPException(status_code=404, detail="Tarif bulunamadı")
    return {"ok": True}


@app.get("/api/source-records", response_model=Page)
def source_records(request: Request, _: Annotated[dict[str, Any], Depends(require_admin)], page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100), source: str | None = None):
    return page_collection(_store(request).db.sourceRecords, {"sourceKey": source} if source else {}, page, page_size, "lastSeenAt")


@app.get("/api/source-records/{source_key}/{source_recipe_id}/revisions", response_model=Page)
def source_revisions(source_key: str, source_recipe_id: str, request: Request, _: Annotated[dict[str, Any], Depends(require_admin)], page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100)):
    return page_collection(_store(request).db.sourceRevisions, {"sourceKey": source_key, "sourceRecipeId": source_recipe_id}, page, page_size, "replacedAt")


@app.get("/api/reviews", response_model=Page)
def reviews(request: Request, _: Annotated[dict[str, Any], Depends(require_admin)], page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100), review_status: str | None = Query(None, alias="status")):
    return page_collection(_store(request).db.reviewQueue, {"status": review_status} if review_status else {}, page, page_size, "createdAt")


@app.patch("/api/reviews/{fingerprint}")
def update_review(fingerprint: str, data: ReviewInput, request: Request, payload: Annotated[dict[str, Any], Depends(require_mutation)]):
    result = _store(request).db.reviewQueue.update_one({"fingerprint": fingerprint}, {"$set": {"status": data.status, "reviewedAt": utc_now(), "reviewedBy": payload["sub"]}})
    if not result.matched_count:
        raise HTTPException(status_code=404, detail="İnceleme kaydı bulunamadı")
    return {"ok": True}


@app.get("/api/etl-runs", response_model=Page)
def etl_runs(request: Request, _: Annotated[dict[str, Any], Depends(require_admin)], page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100)):
    return page_collection(_store(request).db.etlRuns, {}, page, page_size, "startedAt")


@app.get("/api/jobs", response_model=Page)
def jobs(request: Request, _: Annotated[dict[str, Any], Depends(require_admin)], page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100)):
    return page_collection(_store(request).db.crawlerJobs, {}, page, page_size, "createdAt")


@app.post("/api/jobs", status_code=201)
def create_job(data: JobInput, request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    try:
        return JobService(_store(request)).create(data.kind, data.limit, data.resume, data.resetCursor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    if not JobService(_store(request)).cancel(job_id):
        raise HTTPException(status_code=409, detail="İş iptal edilemiyor")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, _: Annotated[dict[str, Any], Depends(require_admin)]):
    store = _store(request)
    if not store.db.crawlerJobs.find_one({"jobId": job_id}):
        raise HTTPException(status_code=404, detail="İş bulunamadı")

    async def stream():
        sequence = 0
        while not await request.is_disconnected():
            rows = list(store.db.crawlerJobEvents.find({"jobId": job_id, "sequence": {"$gt": sequence}}).sort("sequence", 1))
            for row in rows:
                sequence = row["sequence"]
                yield f"id: {sequence}\ndata: {json.dumps(without_id(row), ensure_ascii=False)}\n\n"
            job = store.db.crawlerJobs.find_one({"jobId": job_id}, {"status": 1})
            if job and job["status"] in {"completed", "failed", "cancelled"} and not rows:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _backups(request: Request) -> BackupService:
    return BackupService(_store(request), _settings(request).backup_dir)


@app.get("/api/backups")
def backups(request: Request, _: Annotated[dict[str, Any], Depends(require_admin)]):
    return {"items": _backups(request).list()}


@app.post("/api/backups", status_code=201)
def create_backup(request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    if _store(request).db.crawlerJobs.count_documents({"status": "running"}):
        raise HTTPException(status_code=409, detail="Çalışan crawler işi varken yedek alınamaz")
    return _backups(request).create()


@app.get("/api/backups/{name}")
def download_backup(name: str, request: Request, _: Annotated[dict[str, Any], Depends(require_admin)]):
    try:
        path = _backups(request)._path(name)
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Yedek bulunamadı")
    return FileResponse(path, filename=name, media_type="application/zip")


@app.delete("/api/backups/{name}")
def delete_backup(name: str, request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)]):
    try:
        if not _backups(request).delete(name):
            raise HTTPException(status_code=404, detail="Yedek bulunamadı")
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/backups/restore")
def restore_backup(request: Request, _: Annotated[dict[str, Any], Depends(require_mutation)], file: UploadFile = File(...)):
    if _store(request).db.crawlerJobs.count_documents({"status": {"$in": ["queued", "running"]}}):
        raise HTTPException(status_code=409, detail="Kuyruk boş değilken geri yükleme yapılamaz")
    service = _backups(request)
    path: Path | None = None
    try:
        path = service.save_upload(file.file)
        return service.restore_upload(path)
    except BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if path:
            path.unlink(missing_ok=True)


def run() -> None:
    uvicorn.run("cookall_data.api:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
