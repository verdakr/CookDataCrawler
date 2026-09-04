import unittest

from cookall_data.normalization import IngredientDictionary, normalize_ingredient


class NormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = IngredientDictionary.load()

    def test_turkish_variant_and_unit_are_retained(self):
        item = normalize_ingredient("2 su bardağı cherry domates, doğranmış", "tr", self.dictionary)
        self.assertEqual(item["quantity"], {"min": 2.0, "max": 2.0})
        self.assertEqual(item["unit"], "cup")
        self.assertEqual(item["unitRaw"], "su bardağı")
        self.assertEqual(item["ingredientId"], "tomato")
        self.assertEqual(item["variant"], "cherry")
        self.assertEqual(item["preparationNote"], "doğranmış")
        self.assertFalse(item["reviewRequired"])

    def test_mixed_fraction_and_range(self):
        mixed = normalize_ingredient("1 1/2 cups chopped tomatoes", "en", self.dictionary)
        ranged = normalize_ingredient("3-4 cloves garlic, minced", "en", self.dictionary)
        self.assertEqual(mixed["quantity"], {"min": 1.5, "max": 1.5})
        self.assertEqual(ranged["quantity"], {"min": 3.0, "max": 4.0})

    def test_unknown_ingredient_is_never_auto_created(self):
        item = normalize_ingredient("2 cups mystery powder", "en", self.dictionary)
        self.assertIsNone(item["ingredientId"])
        self.assertEqual(item["normalizationStatus"], "unmatched")
        self.assertTrue(item["reviewRequired"])

    def test_unknown_unit_is_reviewed(self):
        item = normalize_ingredient("2 scoops flour", "en", self.dictionary)
        self.assertEqual(item["ingredientId"], "flour")
        self.assertEqual(item["unitStatus"], "unknown")
        self.assertTrue(item["reviewRequired"])

    def test_compact_units_fraction_slash_and_decimal_are_parsed(self):
        compact = normalize_ingredient("175g/6oz butter", "en", self.dictionary)
        fraction = normalize_ingredient("1⁄2 cup onion", "en", self.dictionary)
        decimal = normalize_ingredient("1.25kg baby potatoes", "en", self.dictionary)
        self.assertEqual((compact["quantity"]["min"], compact["unit"]), (175.0, "g"))
        self.assertEqual((fraction["quantity"]["min"], fraction["unit"]), (0.5, "cup"))
        self.assertEqual((decimal["quantity"]["min"], decimal["unit"]), (1.25, "kg"))

    def test_longest_compound_alias_wins(self):
        eggplant = normalize_ingredient("4 large egg plants", "en", self.dictionary)
        peanut = normalize_ingredient("3 tbs peanut butter", "en", self.dictionary)
        coconut = normalize_ingredient("1 can coconut milk", "en", self.dictionary)
        self.assertEqual(eggplant["ingredientId"], "eggplant")
        self.assertEqual(peanut["ingredientId"], "peanut-butter")
        self.assertEqual(coconut["ingredientId"], "coconut-milk")


if __name__ == "__main__":
    unittest.main()
