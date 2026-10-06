import copy
import unittest
import uuid

from cookall_data.adapters.themealdb import TheMealDBAdapter
from cookall_data.dedup import find_duplicate_candidates
from cookall_data.normalization import IngredientDictionary
from cookall_data.pipeline import run_collection
from cookall_data.settings import Settings
from cookall_data.storage import MongoStore


class FakeAdapter:
    source_key = "themealdb_api"

    def __init__(self, source, recipe, dictionary):
        self.source = source
        self.recipe = recipe
        self.dictionary = dictionary

    def collect(self, limit):
        yield copy.deepcopy(self.source), copy.deepcopy(self.recipe)


class StoragePipelineTests(unittest.TestCase):
    def setUp(self):
        settings = Settings.from_env(require_auth=False)
        self.db_name = f"ca_test_{uuid.uuid4().hex[:20]}"
        self.store = MongoStore(settings.mongodb_uri, self.db_name)
        self.store.ensure_indexes()
        self.dictionary = IngredientDictionary.load()
        adapter = TheMealDBAdapter(None, self.dictionary)
        meal = {"idMeal":"1","strMeal":"Tomato Soup","strInstructions":"Cook tomatoes.","strIngredient1":"tomatoes","strMeasure1":"2 cups","strMealThumb":"","strCategory":"Soup","strArea":"Turkish"}
        self.source = adapter.to_source_record(meal)
        self.recipe = adapter.to_recipe(self.source)

    def tearDown(self):
        self.store.client.drop_database(self.db_name)
        self.store.close()

    def test_second_run_is_idempotent(self):
        adapter = FakeAdapter(self.source, self.recipe, self.dictionary)
        first = run_collection(adapter, self.store, 1)
        second = run_collection(adapter, self.store, 1)
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(len(self.store.recipes()), 1)

    def test_changed_source_preserves_previous_revision(self):
        run_collection(FakeAdapter(self.source, self.recipe, self.dictionary), self.store, 1)
        changed = copy.deepcopy(self.source)
        changed["rawPayload"]["strMeal"] = "Changed Soup"
        from cookall_data.util import content_hash
        changed["contentHash"] = content_hash(changed["rawPayload"])
        changed["revision"] = "2"
        changed_recipe = copy.deepcopy(self.recipe)
        changed_recipe["title"] = "Changed Soup"
        result = run_collection(FakeAdapter(changed, changed_recipe, self.dictionary), self.store, 1)
        revision_count = self.store.db.sourceRevisions.count_documents({})
        self.assertEqual(result["updated"], 1)
        self.assertEqual(revision_count, 1)

    def test_near_duplicate_is_review_only(self):
        left = copy.deepcopy(self.recipe)
        right = copy.deepcopy(self.recipe)
        right["sourceRef"]["sourceRecipeId"] = "2"
        right["title"] = "Tomato Soups"
        candidates = find_duplicate_candidates([left, right])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["automaticAction"], "none")

    def test_reopened_review_updates_status_and_details(self):
        ref = self.recipe["sourceRef"]
        self.store.enqueue_review("ingredient_normalization", ref, "same", {"value": "old"})
        self.store.db.reviewQueue.update_one({"fingerprint": "same"}, {"$set": {"status": "superseded"}})
        self.store.enqueue_review("ingredient_normalization", ref, "same", {"value": "new"})
        self.store.commit()
        row = self.store.db.reviewQueue.find_one({"fingerprint": "same"})
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["details"]["value"], "new")

    def test_collection_checkpoint_round_trip(self):
        self.assertIsNone(self.store.checkpoint("wikibooks_mediawiki:en"))
        self.store.save_checkpoint("wikibooks_mediawiki:en", "next-page", False)
        checkpoint = self.store.checkpoint("wikibooks_mediawiki:en")
        self.assertEqual(checkpoint["continuation"], "next-page")
        self.assertEqual(checkpoint["exhausted"], 0)
        self.store.clear_checkpoint("wikibooks_mediawiki:en")
        self.assertIsNone(self.store.checkpoint("wikibooks_mediawiki:en"))


if __name__ == "__main__":
    unittest.main()
