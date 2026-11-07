"""Command line access to the engine. Read-only: fixes are applied from the app."""
from __future__ import annotations

import argparse
import sys

from . import engine, history
from .database import load_database
from .patches import PlanError
from .search import SearchIndex
from .system import System


def _print_diagnosis(d: engine.Diagnosis, note: str = "") -> None:
    labels = {engine.FIXED: "already fine", engine.NOT_APPLICABLE: "doesn't apply here",
              engine.WRONG_SYSTEM: "not for this system"}
    state = labels.get(d.status, f"{d.confidence}% ({d.level.lower()})")
    print(f"{d.entry.id}: {state}{note}")
    for finding in d.found:
        print(f"  - {finding.signal.says}")
        for line in finding.evidence.lines:
            print(f"      {line}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="troubleshooter", description="Finds and fixes common Linux problems.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="check this machine for known problems")
    search = commands.add_parser("search", help="search the database with a description")
    search.add_argument("query", nargs="+")
    diagnose = commands.add_parser("diagnose", help="check for one problem")
    diagnose.add_argument("entry")
    plan = commands.add_parser("plan", help="print the root script a fix would run, without running it")
    plan.add_argument("entry")
    args = parser.parse_args(argv)

    db = load_database()
    system = System()
    if args.command == "scan":
        results = engine.scan(db, system)
        dismissed = history.Dismissed().ids()
        print(f"{system.os_name}: {len(results)} problem(s) found")
        for d in results:
            _print_diagnosis(d, ", dismissed in the app" if d.entry.id in dismissed else "")
        return 0
    if args.command == "search":
        for hit in SearchIndex(db).search(" ".join(args.query)):
            print(f"{hit.score:.2f}  {'category' if hit.is_category else 'entry   '}  {hit.target.id}")
        return 0

    entry = db.entries.get(args.entry)
    if not entry:
        print(f"No entry called {args.entry}", file=sys.stderr)
        return 2
    diagnosis = engine.diagnose_entry(entry, system)
    if args.command == "diagnose":
        _print_diagnosis(diagnosis)
        return 0
    try:
        built = engine.build_plan(diagnosis, system)
    except PlanError as err:
        print(err, file=sys.stderr)
        return 1
    if not built:
        print(entry.advice or "No fix for this system.", file=sys.stderr)
        return 1
    print(built.script)
    return 0
