import unittest

from cookall_data.adapters.wikibooks import WikibooksAdapter
from cookall_data.normalization import IngredientDictionary


def page(page_id):
    return {
        "pageid": page_id,
        "title": f"Cookbook:Recipe {page_id}",
        "canonicalurl": f"https://en.wikibooks.org/wiki/Cookbook:Recipe_{page_id}",
        "revisions": [{
            "revid": page_id * 10,
            "slots": {"main": {"content": "== Ingredients ==\n* 1 cup water\n== Procedure ==\n# Cook it."}},
        }],
    }


class FakePagedClient:
    def __init__(self):
        self.calls = 0

    def get_json(self, _url, params):
        self.calls += 1
        if self.calls == 1:
            self.first_params = params
            return {"query": {"pages": [page(1), page(2)]}, "continue": {"gcmcontinue": "cursor-2"}}
        self.second_params = params
        return {"query": {"pages": [page(3), page(4)]}}


class WikibooksResumeTests(unittest.TestCase):
    def test_existing_pages_are_skipped_until_new_limit_is_reached(self):
        client = FakePagedClient()
        checkpoints = []
        adapter = WikibooksAdapter(
            "en",
            client,
            IngredientDictionary.load(),
            skip_source_recipe_ids={"en:1", "en:2"},
            checkpoint_callback=lambda continuation, exhausted: checkpoints.append((continuation, exhausted)),
        )
        collected = list(adapter.collect(2))
        self.assertEqual([source["sourceRecipeId"] for source, _ in collected], ["en:3", "en:4"])
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.second_params["gcmcontinue"], "cursor-2")
        self.assertEqual(checkpoints, [("cursor-2", False), (None, True)])
        self.assertEqual(client.first_params["maxlag"], 5)
        self.assertTrue(adapter.source_exhausted)


if __name__ == "__main__":
    unittest.main()
