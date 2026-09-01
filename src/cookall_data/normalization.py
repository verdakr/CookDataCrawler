from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .util import clean_text, slugify

NORMALIZATION_VERSION = "ingredient-rules@1.0.0"

UNICODE_FRACTIONS = {
    "¼": 0.25, "½": 0.5, "¾": 0.75, "⅐": 1 / 7, "⅑": 1 / 9,
    "⅒": 0.1, "⅓": 1 / 3, "⅔": 2 / 3, "⅕": 0.2, "⅖": 0.4,
    "⅗": 0.6, "⅘": 0.8, "⅙": 1 / 6, "⅚": 5 / 6, "⅛": 0.125,
    "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}

UNIT_ALIASES = {
    "g": ("g", "mass"), "gr": ("g", "mass"), "gram": ("g", "mass"), "gramme": ("g", "mass"),
    "kg": ("kg", "mass"), "kğ": ("kg", "mass"), "kilogram": ("kg", "mass"), "kilo": ("kg", "mass"),
    "ml": ("ml", "volume"), "milliliter": ("ml", "volume"), "millilitre": ("ml", "volume"),
    "l": ("l", "volume"), "litre": ("l", "volume"), "liter": ("l", "volume"),
    "tsp": ("tsp", "volume"), "teaspoon": ("tsp", "volume"), "teaspoons": ("tsp", "volume"),
    "çay kaşığı": ("tsp", "volume"), "tatlı kaşığı": ("dessertspoon", "volume"),
    "tbsp": ("tbsp", "volume"), "tbs": ("tbsp", "volume"), "tblsp": ("tbsp", "volume"), "tablespoon": ("tbsp", "volume"), "tablespoons": ("tbsp", "volume"),
    "yemek kaşığı": ("tbsp", "volume"),
    "cup": ("cup", "volume"), "cups": ("cup", "volume"), "su bardağı": ("cup", "volume"), "çay bardağı": ("tea-glass", "volume"), "bardak": ("cup", "volume"), "bardaktan": ("cup", "volume"), "fincan": ("cup", "volume"),
    "adet": ("piece", "count"), "piece": ("piece", "count"), "pieces": ("piece", "count"),
    "clove": ("clove", "count"), "cloves": ("clove", "count"), "diş": ("clove", "count"),
    "pinch": ("pinch", "informal"), "tutam": ("pinch", "informal"),
    "bunch": ("bunch", "informal"), "demet": ("bunch", "informal"),
    "can": ("can", "container"), "cans": ("can", "container"), "kutu": ("can", "container"),
}

PREPARATIONS = {
    "en": ("chopped", "diced", "sliced", "minced", "grated", "peeled", "crushed", "melted", "softened", "divided"),
    "tr": ("doğranmış", "kıyılmış", "rendelenmiş", "soyulmuş", "ezilmiş", "eritilmiş", "yumuşatılmış", "dilimlenmiş"),
}
VARIANTS = {
    "en": ("cherry", "baby", "red", "green", "yellow", "fresh", "dried", "smoked", "boneless", "skinless"),
    "tr": ("cherry", "çeri", "bebek", "kırmızı", "yeşil", "sarı", "taze", "kuru", "füme", "kemiksiz", "derisiz"),
}


@dataclass(frozen=True)
class IngredientDictionary:
    version: str
    aliases: dict[tuple[str, str], set[str]]

    @classmethod
    def load(cls, path: Path | None = None) -> "IngredientDictionary":
        if path is None:
            path = Path(__file__).resolve().parents[2] / "data" / "ingredient_dictionary.v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        aliases: dict[tuple[str, str], set[str]] = {}
        for ingredient in payload["ingredients"]:
            for label in ingredient["labels"]:
                names = {label["name"], *label.get("aliases", [])}
                aliases[(label["language"], ingredient["canonicalKey"])] = {
                    clean_text(name).casefold() for name in names
                }
        return cls(payload["version"], aliases)


def _number(token: str) -> float | None:
    token = token.strip().replace(",", ".").replace("⁄", "/")
    if " " in token:
        parts = token.split()
        if len(parts) == 2:
            left, right = _number(parts[0]), _number(parts[1])
            return left + right if left is not None and right is not None else None
    if token in UNICODE_FRACTIONS:
        return UNICODE_FRACTIONS[token]
    if token and token[-1:] in UNICODE_FRACTIONS:
        base = float(token[:-1]) if token[:-1] else 0.0
        return base + UNICODE_FRACTIONS[token[-1]]
    try:
        if "/" in token:
            return float(Fraction(token))
        return float(token)
    except (ValueError, ZeroDivisionError):
        return None


def _parse_quantity(text: str) -> tuple[dict[str, float | None], str]:
    parse_text = text.replace("⁄", "/")
    fraction_chars = "¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
    number = rf"(?:\d+\s+\d+/\d+|\d+[{fraction_chars}]|[{fraction_chars}]|\d+(?:[.,]\d+)?(?:/\d+)?)"
    pattern = rf"^\s*({number})(?:\s*(?:-|–|—|to|ile)\s*({number}))?(?![\d.,/{fraction_chars}])"
    match = re.match(pattern, parse_text, flags=re.IGNORECASE)
    if not match:
        word = re.match(r"^\s*(yarım|half)\b", text, flags=re.IGNORECASE)
        if word:
            return {"min": 0.5, "max": 0.5}, text[word.end():].strip()
        return {"min": None, "max": None}, text
    first = _number(match.group(1))
    maximum = _number(match.group(2)) if match.group(2) else first
    return {"min": first, "max": maximum}, text[match.end():].strip()


def _parse_unit(text: str) -> tuple[str | None, str | None, str | None, str]:
    join_tolerant = {"adet", "su bardağı", "çay bardağı", "yemek kaşığı"}
    for raw in sorted(UNIT_ALIASES, key=len, reverse=True):
        suffix = "" if raw in join_tolerant else r"\b"
        match = re.match(rf"^{re.escape(raw)}{suffix}\.?\s*", text, flags=re.IGNORECASE)
        if match:
            normalized, kind = UNIT_ALIASES[raw]
            remainder = text[match.end():].strip()
            remainder = re.sub(r"^/\s*\d+(?:[.,]\d+)?(?:/\d+)?\s*(?:oz|ounces?|lb|lbs|pounds?)\.?\s*", "", remainder, flags=re.IGNORECASE)
            return normalized, text[:match.end()].strip(" ."), kind, remainder
    return None, None, None, text


def normalize_ingredient(
    original_text: str,
    language: str,
    dictionary: IngredientDictionary,
) -> dict[str, Any]:
    text = clean_text(original_text)
    quantity, remainder = _parse_quantity(text)
    unit, unit_raw, unit_kind, remainder = _parse_unit(remainder)
    lower = remainder.casefold().strip(" ,;()")
    preparation = [item for item in PREPARATIONS.get(language, ()) if re.search(rf"\b{re.escape(item)}\b", lower)]
    variants = [item for item in VARIANTS.get(language, ()) if re.search(rf"\b{re.escape(item)}\b", lower)]
    candidate = lower
    for qualifier in (*preparation, *variants):
        candidate = re.sub(rf"\b{re.escape(qualifier)}\b", " ", candidate)
    candidate = clean_text(re.sub(r"[(),;]", " ", candidate)).casefold()

    exact: list[str] = []
    contained: list[tuple[int, str, str]] = []
    for (alias_language, canonical_key), aliases in dictionary.aliases.items():
        if alias_language != language:
            continue
        if candidate in aliases:
            exact.append(canonical_key)
        for alias in aliases:
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", candidate):
                contained.append((len(alias), canonical_key, alias))

    matches = sorted(set(exact))
    confidence = 0.0
    status = "unmatched"
    ingredient_id: str | None = None
    if len(matches) == 1:
        ingredient_id, confidence, status = matches[0], 1.0, "matched"
    elif len(matches) > 1:
        status, confidence = "ambiguous", 0.3
    elif contained:
        longest = max(length for length, _, _ in contained)
        best = sorted({key for length, key, _ in contained if length == longest})
        if len(best) == 1:
            ingredient_id, confidence, status = best[0], 0.88, "matched"
        else:
            status, confidence = "ambiguous", 0.3

    unit_status = "recognized" if unit else "not_present"
    if quantity["min"] is not None and unit is None and status == "matched" and confidence < 1.0:
        unit_status = "unknown"

    return {
        "originalText": text,
        "quantity": quantity,
        "unit": unit,
        "unitRaw": unit_raw,
        "unitKind": unit_kind,
        "unitStatus": unit_status,
        "ingredientText": remainder,
        "preparationNote": ", ".join(preparation) or None,
        "variant": ", ".join(variants) or None,
        "ingredientId": ingredient_id,
        "normalizationStatus": status,
        "confidence": confidence,
        "ruleVersion": dictionary.version,
        "reviewRequired": status != "matched" or confidence < 0.85 or unit_status == "unknown" or (confidence < 1.0 and bool(re.search(r"\b(?:and|ve|veya)\b", remainder, flags=re.IGNORECASE))),
        "candidateSlug": slugify(candidate),
    }
