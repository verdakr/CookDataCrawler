from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .dedup import find_duplicate_candidates
from .storage import Phase0Store
from .util import utc_now


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def build_quality_report(recipes: list[dict[str, Any]]) -> dict[str, Any]:
    by_source_language: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for recipe in recipes:
        ref = recipe["sourceRef"]
        by_source_language[f"{ref['sourceKey']}:{recipe['language']}"] .append(recipe)
    groups: dict[str, Any] = {}
    for key, values in sorted(by_source_language.items()):
        count = len(values)
        total_ingredients = sum(len(recipe["ingredients"]) for recipe in values)
        status_counts = Counter(item["normalizationStatus"] for recipe in values for item in recipe["ingredients"])
        fields = ("title", "description", "category", "cuisine", "servings", "instructions", "ingredients", "image", "attribution")
        groups[key] = {
            "recipeCount": count,
            "fieldFillRates": {field: round(sum(_present(recipe.get(field)) for recipe in values) / count, 4) for field in fields},
            "ingredientLineCount": total_ingredients,
            "ingredientNormalization": dict(status_counts),
            "parseRates": {
                "servings": round(sum(_present(recipe.get("servings")) for recipe in values) / count, 4),
                "anyTime": round(sum(any(value is not None for value in recipe["times"].values()) for recipe in values) / count, 4),
                "category": round(sum(_present(recipe.get("category")) for recipe in values) / count, 4),
            },
            "attributionCoverageRate": round(sum(_present(recipe.get("attribution")) for recipe in values) / count, 4),
            "licensedDisplayableImageRate": round(sum(bool((recipe.get("image") or {}).get("displayAllowed")) for recipe in values) / count, 4),
            "languageMatchesExpectedRate": round(sum(recipe["language"] in {"tr", "en"} for recipe in values) / count, 4),
        }
    return {"generatedAt": utc_now(), "groups": groups, "totalRecipes": len(recipes)}


def write_reports(store: Phase0Store, output_dir: str | Path) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    recipes = store.recipes()
    sources = store.source_records()
    quality = build_quality_report(recipes)
    duplicates = find_duplicate_candidates(recipes)
    license_manifest = [
        {
            "sourceKey": record["sourceKey"], "sourceRecipeId": record["sourceRecipeId"],
            "sourceUrl": record["sourceUrl"], "revision": record.get("revision"),
            "license": record["license"], "attribution": record["attribution"],
        }
        for record in sources
    ]
    paths = {
        "quality": output / "sample-quality.json",
        "duplicates": output / "dedup-results.json",
        "licenses": output / "license-attribution-manifest.json",
        "runs": output / "etl-runs.json",
        "manual": output / "ingredient-manual-review.csv",
        "summary": output / "phase0-summary.md",
    }
    paths["quality"].write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["duplicates"].write_text(json.dumps(duplicates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["licenses"].write_text(json.dumps(license_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["runs"].write_text(json.dumps(store.runs(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with paths["manual"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_key", "source_recipe_id", "language", "title", "original_text", "quantity_min", "quantity_max", "unit", "unit_status", "ingredient_id", "variant", "status", "confidence", "review_required", "reviewer_result", "reviewer_notes"])
        by_language: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for recipe in recipes:
            by_language[recipe["language"]].extend((recipe, item) for item in recipe["ingredients"])
        languages = sorted(by_language)
        positions = {language: 0 for language in languages}
        written = 0
        while written < 200 and any(positions[language] < len(by_language[language]) for language in languages):
            for language in languages:
                position = positions[language]
                if position >= len(by_language[language]) or written >= 200:
                    continue
                recipe, item = by_language[language][position]
                positions[language] += 1
                writer.writerow([recipe["sourceRef"]["sourceKey"], recipe["sourceRef"]["sourceRecipeId"], recipe["language"], recipe["title"], item["originalText"], item["quantity"]["min"], item["quantity"]["max"], item["unit"], item["unitStatus"], item["ingredientId"], item["variant"], item["normalizationStatus"], item["confidence"], item["reviewRequired"], "", ""])
                written += 1
    revision_count = store.connection.execute("SELECT count(*) FROM source_revisions").fetchone()[0]
    review_count = store.connection.execute("SELECT count(*) FROM review_queue WHERE status='pending'").fetchone()[0]
    lines = [
        "# Phase 0 örneklem özeti", "", f"Üretim: `{quality['generatedAt']}`", "",
        f"Toplam normalize tarif / sourceRecord: **{quality['totalRecipes']} / {len(sources)}**",
        f"Korunan eski kaynak sürümü: **{revision_count}**", f"Bekleyen insan incelemesi: **{review_count}**", "",
        "| Kaynak/dil | Tarif | >=50 kriteri | Ingredient satırı | Eşleşen | Belirsiz/eşleşmeyen |",
        "|---|---:|:---:|---:|---:|---:|",
    ]
    for key, group in quality["groups"].items():
        normalized = group["ingredientNormalization"]
        lines.append(f"| {key} | {group['recipeCount']} | {'geçti' if group['recipeCount'] >= 50 else 'kaldı'} | {group['ingredientLineCount']} | {normalized.get('matched', 0)} | {normalized.get('ambiguous', 0) + normalized.get('unmatched', 0)} |")
    lines.extend(["", "Manuel inceleme çalışma sayfası: **200 satır (100 en + 100 tr)**", f"Yakın/kesin kopya inceleme adayı: **{len(duplicates)}**", "", "Otomatik silme veya birleştirme yapılmadı. Ayrıntılar JSON ve CSV raporlarındadır."])
    paths["summary"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths
