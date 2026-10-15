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
        revisions = self.client.get(path + "/revisions")
        self.assertEqual(revisions.status_code, 200, revisions.text)
        self.assertEqual(revisions.json()["changeCount"], 1)
        self.assertEqual(revisions.json()["items"][0]["kind"], "current")
        self.assertEqual(revisions.json()["items"][0]["payload"]["title"], "Updated Wikibooks Test")
        self.assertEqual(revisions.json()["items"][1]["payload"]["title"], "Wikibooks Test")

    def test_recipe_publication_lifecycle_and_legacy_defaults(self):
        csrf = self.login()
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        data = {"title": "Yayın Testi", "language": "tr", "ingredients": [{"text": "1 su bardağı mercimek"}], "instructions": ["Pişirin."]}
        created = self.client.post("/api/recipes", json=data, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["publicationStatus"], "draft")
        ref = created.json()["sourceRef"]
        key = {"sourceKey": ref["sourceKey"], "sourceRecipeId": ref["sourceRecipeId"]}
        path = f"/api/recipes/{ref['sourceKey']}/{ref['sourceRecipeId']}"
        publication = path + "/publication"

        # Old records remain drafts without a database migration.
        app.state.store.db.recipes.update_one(key, {"$unset": {"publicationStatus": ""}})
        self.assertEqual(self.client.get(path).json()["publicationStatus"], "draft")
        def ids(status):
            response = self.client.get("/api/recipes", params={"publicationStatus": status, "pageSize": 100})
            self.assertEqual(response.status_code, 200, response.text)
            return [item["sourceRef"]["sourceRecipeId"] for item in response.json()["items"]]
        self.assertIn(ref["sourceRecipeId"], ids("draft"))
        self.assertNotIn(ref["sourceRecipeId"], ids("published"))

        self.assertEqual(self.client.patch(publication, json={"status": "published"}).status_code, 403)
        published = self.client.patch(publication, json={"status": "published"}, headers=headers)
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["publicationStatus"], "published")
        self.assertIn(ref["sourceRecipeId"], ids("published"))
        self.assertNotIn(ref["sourceRecipeId"], ids("draft"))
        self.assertEqual(self.client.patch(publication, json={"status": "published"}, headers=headers).status_code, 200)
        # Content edits and crawler reprocessing preserve the editorial decision.
        updated = self.client.put(path, json={**data, "title": "Güncel Yayın Testi"}, headers=headers)
        self.assertEqual(updated.json()["publicationStatus"], "published")
        payload = app.state.store.db.recipes.find_one(key)["payload"]
        app.state.store.upsert_recipe(payload)
        self.assertEqual(self.client.get(path).json()["publicationStatus"], "published")
        draft = self.client.patch(publication, json={"status": "draft"}, headers=headers)
        self.assertEqual(draft.json()["publicationStatus"], "draft")
        self.client.patch(publication, json={"status": "published"}, headers=headers)
        self.assertEqual(self.client.delete(path, headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path).json()["publicationStatus"], "archived")
        self.assertIn(ref["sourceRecipeId"], ids("archived"))
        self.assertNotIn(ref["sourceRecipeId"], ids("published"))
        self.assertEqual(self.client.patch(publication, json={"status": "published"}, headers=headers).status_code, 409)
        self.assertEqual(self.client.post(path + "/restore", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path).json()["publicationStatus"], "draft")
        self.assertEqual(self.client.patch(publication, json={"status": "invalid"}, headers=headers).status_code, 422)
        self.assertEqual(self.client.patch(publication, json={"status": "archived"}, headers=headers).status_code, 422)
        self.assertEqual(self.client.get("/api/recipes?publicationStatus=invalid").status_code, 422)
        self.assertEqual(self.client.patch("/api/recipes/missing/missing/publication", json={"status": "draft"}, headers=headers).status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.patch(publication, json={"status": "published"}, headers=headers).status_code, 401)

    def test_bulk_publication_handles_duplicates_archived_missing_and_auth(self):
        endpoint = "/api/recipes/bulk-publication"
        self.assertEqual(self.client.post(endpoint, json={"status": "published", "recipes": []}).status_code, 401)
        csrf = self.login()
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        refs = []
        for title in ("Bulk A", "Bulk B", "Bulk Archived"):
            created = self.client.post("/api/recipes", json={
                "title": title, "ingredients": [{"text": "1 su bardağı mercimek"}], "instructions": ["Pişirin."],
            }, headers=headers)
            self.assertEqual(created.status_code, 201, created.text)
            ref = created.json()["sourceRef"]
            refs.append({"sourceKey": ref["sourceKey"], "sourceRecipeId": ref["sourceRecipeId"]})
        paths = [f"/api/recipes/{ref['sourceKey']}/{ref['sourceRecipeId']}" for ref in refs]
        self.client.delete(paths[2], headers=headers)
        missing = {"sourceKey": "missing", "sourceRecipeId": "missing"}
        body = {"status": "published", "recipes": [*refs, refs[0], missing]}
        self.assertEqual(self.client.post(endpoint, json=body).status_code, 403)
        response = self.client.post(endpoint, json=body, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"updatedCount": 2, "errors": [
            {**refs[2], "reason": "archived"}, {**missing, "reason": "not_found"},
        ]})
        for path in paths[:2]:
            self.assertEqual(self.client.get(path).json()["publicationStatus"], "published")
        self.assertEqual(self.client.get(paths[2]).json()["publicationStatus"], "archived")
        response = self.client.post(endpoint, json={"status": "draft", "recipes": refs[:2]}, headers=headers)
        self.assertEqual(response.json(), {"updatedCount": 2, "errors": []})
        for path in paths[:2]:
            self.assertEqual(self.client.get(path).json()["publicationStatus"], "draft")
        for body in ({"status": "published", "recipes": []}, {"status": "archived", "recipes": refs},
                     {"status": "published", "recipes": [refs[0]] * 501}):
            self.assertEqual(self.client.post(endpoint, json=body, headers=headers).status_code, 422)

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

    def test_ingredient_review_adds_reusable_alias(self):
        csrf = self.login()
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        recipe_input = RecipeInput(
            title="Alias Test", language="tr",
            ingredients=[{"text": "1 kaşık gizli mercimek"}], instructions=["Pişirin."],
        )
        ref = {"sourceKey": "themealdb_api", "sourceRecipeId": "alias-test", "sourceContentHash": "sha256:" + "1" * 64}
        app.state.store.upsert_recipe(_build_payload(recipe_input, ref))
        fingerprint = "ingredient:alias-test"
        app.state.store.enqueue_review("ingredient_normalization", ref, fingerprint, {
            "position": 1, "ingredient": {"originalText": "1 kaşık gizli mercimek", "ingredientText": "gizli mercimek"},
        })
        options = self.client.get("/api/ingredients?language=tr")
        self.assertEqual(options.status_code, 200, options.text)
        canonical_key = options.json()["items"][0]["canonicalKey"]
        resolved = self.client.post(
            f"/api/reviews/{fingerprint}/resolve-ingredient",
            json={"canonicalKey": canonical_key, "alias": "gizli mercimek"}, headers=headers,
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        normalized = app.state.store.ingredient_dictionary()
        self.assertIn("gizli mercimek", normalized.aliases[("tr", canonical_key)])
        review = app.state.store.db.reviewQueue.find_one({"fingerprint": fingerprint})
        self.assertEqual(review["status"], "approved")


if __name__ == "__main__":
    unittest.main()
