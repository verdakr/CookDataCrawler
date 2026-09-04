import unittest

from cookall_data.adapters.themealdb import TheMealDBAdapter
from cookall_data.adapters.wikibooks import WikibooksAdapter
from cookall_data.normalization import IngredientDictionary
from cookall_data.validation import ValidationError, validate_recipe, validate_source_record


class NoNetworkClient:
    def get_json(self, *_args, **_kwargs):
        raise AssertionError("network not expected")


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = IngredientDictionary.load()

    def test_themealdb_mapping_and_image_license_gate(self):
        adapter = TheMealDBAdapter(NoNetworkClient(), self.dictionary)
        meal = {
            "idMeal": "42", "strMeal": "Tomato Soup", "strCategory": "Soup", "strArea": "Test",
            "strInstructions": "Chop tomatoes. Cook them.", "strIngredient1": "Tomatoes",
            "strMeasure1": "2 cups", "strMealThumb": "https://example.test/image.jpg",
            "strImageSource": "", "strCreativeCommonsConfirmed": "No", "dateModified": None,
        }
        source = adapter.to_source_record(meal)
        recipe = adapter.to_recipe(source)
        validate_source_record(source)
        validate_recipe(recipe)
        self.assertEqual(recipe["ingredients"][0]["ingredientId"], "tomato")
        self.assertFalse(recipe["image"]["displayAllowed"])
        self.assertEqual(len(recipe["instructions"]), 2)

    def test_wikibooks_revision_and_attribution_are_preserved(self):
        adapter = WikibooksAdapter("tr", NoNetworkClient(), self.dictionary)
        page = {
            "pageid": 7, "title": "Yemek:Domates Çorbası",
            "canonicalurl": "https://tr.wikibooks.org/wiki/Yemek:Domates_Çorbası",
            "revisions": [{"revid": 99, "timestamp": "2025-01-01T00:00:00Z", "slots": {"main": {"content": "== Malzemeler ==\n* 2 adet domates\n* 1 çay kaşığı tuz\n== Hazırlanışı ==\n# Domatesleri doğrayın.\n# Pişirin."}}}],
            "images": [{"title": "Dosya:Soup.jpg"}],
        }
        source = adapter.to_source_record(page)
        recipe = adapter.to_recipe(source)
        validate_source_record(source)
        validate_recipe(recipe)
        self.assertEqual(source["revision"], "99")
        self.assertIn("oldid=99", source["attribution"]["url"])
        self.assertEqual(len(recipe["ingredients"]), 2)
        self.assertFalse(recipe["image"]["displayAllowed"])

    def test_broken_payload_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_source_record({"sourceKey": "broken"})

    def test_turkish_legacy_bold_and_yapilis_headings_are_recognized(self):
        adapter = WikibooksAdapter("tr", NoNetworkClient(), self.dictionary)
        bold = "'''MALZEME'''\n* 1 adet domates\n'''HAZIRLANIŞI'''\nDomatesi pişirin."
        sections = adapter._sections(bold)
        self.assertIn("malzeme", sections)
        self.assertIn("hazirlanisi", sections)
        modern = adapter._sections("== Malzemeler ==\n* Su\n== Yapılış aşamaları ==\n# Pişirin")
        self.assertTrue(adapter._section(modern, ("yapılış",)))


if __name__ == "__main__":
    unittest.main()
