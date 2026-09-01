from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from .util import clean_text, content_hash


def ingredient_keys(recipe: dict[str, Any]) -> set[str]:
    return {
        item.get("ingredientId") or item.get("candidateSlug", "")
        for item in recipe.get("ingredients", [])
        if item.get("ingredientId") or item.get("candidateSlug")
    }


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, clean_text(left).casefold(), clean_text(right).casefold()).ratio()


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def find_duplicate_candidates(recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(recipes):
        for right in recipes[index + 1:]:
            if left["language"] != right["language"]:
                continue
            exact = left["contentHash"] == right["contentHash"]
            title_score = title_similarity(left["title"], right["title"])
            ingredient_score = jaccard(ingredient_keys(left), ingredient_keys(right))
            if exact or (title_score >= 0.92 and ingredient_score >= 0.85):
                candidate = {
                    "kind": "exact" if exact else "near",
                    "left": left["sourceRef"], "right": right["sourceRef"],
                    "language": left["language"], "titleSimilarity": round(title_score, 4),
                    "ingredientJaccard": round(ingredient_score, 4),
                    "automaticAction": "none", "reviewRequired": True,
                }
                candidate["fingerprint"] = content_hash(candidate)
                candidates.append(candidate)
    return candidates

