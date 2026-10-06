from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import json_util

from .storage import COLLECTIONS, MongoStore
from .util import utc_now


BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 250 * 1024 * 1024


class BackupError(ValueError):
    pass


class BackupService:
    def __init__(self, store: MongoStore, directory: Path) -> None:
        self.store = store
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if Path(name).name != name or not name.endswith(".cookall.zip"):
            raise BackupError("Geçersiz yedek adı")
        path = (self.directory / name).resolve()
        if self.directory not in path.parents:
            raise BackupError("Geçersiz yedek yolu")
        return path

    def create(self, prefix: str = "backup") -> dict[str, Any]:
        stamp = utc_now().replace(":", "-").replace(".", "-")
        name = f"{prefix}-{stamp}.cookall.zip"
        final_path = self._path(name)
        manifest: dict[str, Any] = {"version": BACKUP_VERSION, "createdAt": utc_now(), "collections": {}}
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            for collection_name in COLLECTIONS:
                payload = "\n".join(json_util.dumps(row, ensure_ascii=False) for row in self.store.db[collection_name].find())
                if payload:
                    payload += "\n"
                raw = payload.encode("utf-8")
                file_name = f"{collection_name}.ndjson"
                (temp / file_name).write_bytes(raw)
                manifest["collections"][collection_name] = {
                    "count": self.store.db[collection_name].count_documents({}),
                    "sha256": hashlib.sha256(raw).hexdigest(), "file": file_name,
                }
            (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in temp.iterdir():
                    archive.write(file, file.name)
        return {"name": name, "size": final_path.stat().st_size, "createdAt": manifest["createdAt"]}

    def list(self) -> list[dict[str, Any]]:
        return [{"name": path.name, "size": path.stat().st_size, "createdAt": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()} for path in sorted(self.directory.glob("*.cookall.zip"), reverse=True)]

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def validate(self, path: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        if not path.exists() or path.stat().st_size > MAX_BACKUP_BYTES or not zipfile.is_zipfile(path):
            raise BackupError("Yedek dosyası geçersiz veya çok büyük")
        result: dict[str, list[dict[str, Any]]] = {}
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "manifest.json" not in names:
                raise BackupError("Manifest bulunamadı")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("version") != BACKUP_VERSION or set(manifest.get("collections", {})) != set(COLLECTIONS):
                raise BackupError("Desteklenmeyen veya eksik yedek manifesti")
            for collection, metadata in manifest["collections"].items():
                file_name = metadata["file"]
                if file_name not in names:
                    raise BackupError(f"Eksik koleksiyon: {collection}")
                raw = archive.read(file_name)
                if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
                    raise BackupError(f"Checksum doğrulanamadı: {collection}")
                rows = [json_util.loads(line) for line in raw.decode("utf-8").splitlines() if line]
                if len(rows) != metadata["count"]:
                    raise BackupError(f"Kayıt sayısı uyuşmuyor: {collection}")
                result[collection] = rows
        return manifest, result

    def restore_upload(self, uploaded: Path) -> dict[str, Any]:
        manifest, collections = self.validate(uploaded)
        safety = self.create("pre-restore")
        previous = {name: list(self.store.db[name].find()) for name in COLLECTIONS}
        try:
            for name, rows in collections.items():
                self.store.db[name].delete_many({})
                if rows:
                    self.store.db[name].insert_many(rows, ordered=True)
            self.store.ensure_indexes()
        except Exception:
            for name, rows in previous.items():
                self.store.db[name].delete_many({})
                if rows:
                    self.store.db[name].insert_many(rows, ordered=False)
            self.store.ensure_indexes()
            raise
        return {"restoredFrom": manifest["createdAt"], "safetyBackup": safety["name"]}

    def save_upload(self, source: Any) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".cookall.zip")
        with handle:
            written = 0
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_BACKUP_BYTES:
                    Path(handle.name).unlink(missing_ok=True)
                    raise BackupError("Yedek dosyası çok büyük")
                handle.write(chunk)
        return Path(handle.name)
