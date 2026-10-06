from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from argon2 import PasswordHasher

from .adapters import TheMealDBAdapter, WikibooksAdapter
from .http import HttpClient
from .normalization import IngredientDictionary
from .pipeline import reprocess_records, run_collection
from .reporting import write_reports
from .settings import Settings
from .storage import MongoStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Cook All MongoDB data tooling")
    commands = root.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="collect and normalize a bounded official-API sample")
    collect.add_argument("--source", required=True, choices=("themealdb", "wikibooks-en", "wikibooks-tr"))
    collect.add_argument("--limit", type=int, default=50)
    collect.add_argument("--resume", action="store_true", help="continue Wikibooks pagination and count only unseen recipes")
    collect.add_argument("--reset-cursor", action="store_true", help="restart Wikibooks scan; use together with --resume")
    collect.add_argument("--user-agent", default=os.environ.get("COOKALL_USER_AGENT"), help="descriptive API User-Agent including your contact URL/email")
    report = commands.add_parser("report", help="write quality, dedup, license and manual-review reports")
    report.add_argument("--output", type=Path, default=Path("artifacts/reports"))
    commands.add_parser("reprocess", help="rerun current normalization rules from stored raw source records")
    commands.add_parser("hash-password", help="generate an Argon2 ADMIN_PASSWORD_HASH")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "hash-password":
        first = getpass.getpass("Admin password: ")
        second = getpass.getpass("Repeat password: ")
        if not first or first != second:
            raise SystemExit("Passwords are empty or do not match")
        print(PasswordHasher().hash(first))
        return 0
    settings = Settings.from_env(require_auth=False)
    dictionary = IngredientDictionary.load()
    with MongoStore(settings.mongodb_uri, settings.db_name) as store:
        if args.command == "collect":
            if args.limit < 1:
                raise SystemExit("--limit must be positive")
            if args.reset_cursor and not args.resume:
                raise SystemExit("--reset-cursor must be used together with --resume")
            client = HttpClient(user_agent=args.user_agent or "CookAllPhase0/0.2 (local educational project)")
            if args.source == "themealdb":
                if args.resume:
                    raise SystemExit("--resume is currently supported for Wikibooks sources")
                adapter = TheMealDBAdapter(client, dictionary)
            else:
                language = args.source.rsplit("-", 1)[1]
                checkpoint_key = f"wikibooks_mediawiki:{language}"
                if args.reset_cursor:
                    store.clear_checkpoint(checkpoint_key)
                checkpoint = store.checkpoint(checkpoint_key) if args.resume else None
                adapter = WikibooksAdapter(
                    language,
                    client,
                    dictionary,
                    start_continuation=checkpoint["continuation"] if checkpoint else None,
                    skip_source_recipe_ids=store.source_ids("wikibooks_mediawiki", f"{language}:") if args.resume else set(),
                    checkpoint_callback=(lambda continuation, exhausted: store.save_checkpoint(checkpoint_key, continuation, exhausted)) if args.resume else None,
                    exhausted=bool(checkpoint and checkpoint["exhausted"]),
                )
            print(json.dumps(run_collection(adapter, store, args.limit), ensure_ascii=False, indent=2))
        elif args.command == "report":
            paths = write_reports(store, args.output)
            print(json.dumps({key: str(path) for key, path in paths.items()}, ensure_ascii=False, indent=2))
        elif args.command == "reprocess":
            adapters = {
                "themealdb_api": TheMealDBAdapter(None, dictionary),
                "wikibooks_mediawiki:en": WikibooksAdapter("en", None, dictionary),
                "wikibooks_mediawiki:tr": WikibooksAdapter("tr", None, dictionary),
            }
            print(json.dumps(reprocess_records(store, adapters), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
