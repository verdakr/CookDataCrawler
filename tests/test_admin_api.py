import os
import tempfile
import unittest
import uuid
from pathlib import Path

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from cookall_data.api import app
from cookall_data.admin_service import RecipeInput, _build_payload
from cookall_data.settings import Settings
from cookall_data.storage import MongoStore


class AdminApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = Settings.from_env(require_auth=False)
        cls.db_name = f"ca_api_{uuid.uuid4().hex[:20]}"
        cls.backups = tempfile.TemporaryDirectory()
        os.environ.update({
            "DB_NAME": cls.db_name,
            "ADMIN_USERNAME": "test-admin",
            "ADMIN_PASSWORD_HASH": PasswordHasher().hash("correct-horse"),
            "AUTH_SECRET": "test-secret-that-is-long-enough-for-tests",
            "ADMIN_ORIGIN": "http://testserver",
            "BACKUP_DIR": cls.backups.name,
        })
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        base = Settings.from_env(require_auth=False)
        store = MongoStore(base.mongodb_uri, cls.db_name)
        store.client.drop_database(cls.db_name)
        store.close()
        cls.backups.cleanup()

    def setUp(self):
        self.client.cookies.clear()

    def login(self):
        response = self.client.post("/api/auth/login", json={"username": "test-admin", "password": "correct-horse"})
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    def test_auth_recipe_archive_and_backup_flow(self):
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        csrf = self.login()
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        recipe = {
            "title": "Test Çorbası", "language": "tr", "description": "Test tarifi",
            "category": "Çorba", "cuisine": "Türk", "tags": ["test"], "servings": 2,
            "prepMinutes": 5, "cookMinutes": 15,
            "ingredients": [{"text": "1 su bardağı mercimek"}], "instructions": ["Malzemeleri pişirin."],
        }
        created = self.client.post("/api/recipes", json=recipe, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        ref = created.json()["sourceRef"]
        path = f"/api/recipes/{ref['sourceKey']}/{ref['sourceRecipeId']}"
        self.assertEqual(self.client.delete(path, headers=headers).status_code, 200)
        self.assertEqual(self.client.post(path + "/restore", headers=headers).status_code, 200)
        backup = self.client.post("/api/backups", headers=headers)
        self.assertEqual(backup.status_code, 201, backup.text)
        backup_path = Path(self.backups.name) / backup.json()["name"]
        self.assertTrue(backup_path.exists())
        self.assertEqual(self.client.delete(path, headers=headers).status_code, 200)
        with backup_path.open("rb") as handle:
            restored = self.client.post(
                "/api/backups/restore", headers=headers,
                files={"file": (backup_path.name, handle, "application/zip")},
            )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIsNone(self.client.get(path).json()["archivedAt"])

    def test_mutation_requires_csrf(self):
        self.login()
        response = self.client.post("/api/jobs", json={"kind": "report"}, headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 403)

    def test_empty_recipe_filters_are_accepted(self):
        self.login()
        response = self.client.get("/api/recipes?page=1&pageSize=20&search=&language=&archived=false")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("items", response.json())

    def test_encoded_wikibooks_id_can_be_opened_and_updated(self):
        csrf = self.login()
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        recipe_input = RecipeInput(
            title="Wikibooks Test", language="en",
            ingredients=[{"text": "1 cup flour"}], instructions=["Mix ingredients."],
        )
        ref = {
            "sourceKey": "wikibooks_mediawiki", "sourceRecipeId": "en:109343",
            "sourceContentHash": "sha256:" + "0" * 64,
        }
        app.state.store.upsert_recipe(_build_payload(recipe_input, ref))
        path = "/api/recipes/wikibooks_mediawiki/en%3A109343"
        opened = self.client.get(path)
        self.assertEqual(opened.status_code, 200, opened.text)
        changed = recipe_input.model_dump(mode="json")
        changed["title"] = "Updated Wikibooks Test"
        updated = self.client.put(path, json=changed, headers=headers)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["title"], "Updated Wikibooks Test")
        self.assertEqual(
            app.state.store.db.recipeRevisions.count_documents(
                {"sourceKey": "wikibooks_mediawiki", "sourceRecipeId": "en:109343"}
            ),
            1,
        )

    def test_admin_read_endpoints_are_available(self):
        self.login()
        paths = (
            "/api/dashboard", "/api/source-records?page=1&pageSize=20",
            "/api/reviews?page=1&pageSize=20", "/api/etl-runs?page=1&pageSize=20",
            "/api/jobs?page=1&pageSize=20", "/api/backups",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
