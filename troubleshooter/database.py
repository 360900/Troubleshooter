"""The troubleshooting database: categories plus one TOML file per problem.

A broken entry fails loudly here (and in the test suite) instead of misbehaving on a
user's machine, so unknown keys are errors.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .patches import OPS, validate_step
from .probes import GROUPS, MATCH_KEYS, SOURCE_KEYS
from .system import SCOPES

DATA_DIR = Path(__file__).parent / "data"
ENTRY_KEYS = {"id", "title", "summary", "categories", "systems", "keywords", "symptoms", "auto_detect",
              "prior", "when", "fixed_when", "signals", "reproduce", "fix", "advice"}
FIX_KEYS = {"systems", "reboot", "steps"}


class DatabaseError(Exception):
    pass


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    icon: str
    blurb: str
    keywords: tuple[str, ...]


@dataclass
class Signal:
    says: str
    weight: float
    spec: dict
    counter: bool = False


@dataclass
class Fix:
    systems: list[str]
    reboot: bool
    steps: list[dict]


@dataclass
class Entry:
    id: str
    title: str
    summary: str
    categories: list[str]
    systems: list[str]
    keywords: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    auto_detect: bool = False
    prior: float = 0.05
    when: list[dict] = field(default_factory=list)
    fixed_when: list[dict] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    reproduce: str | None = None
    fixes: list[Fix] = field(default_factory=list)
    advice: str | None = None


@dataclass
class Database:
    categories: dict[str, Category]
    entries: dict[str, Entry]

    def in_category(self, category_id: str) -> list[Entry]:
        return [e for e in self.entries.values() if category_id in e.categories]


def _check_spec(where: str, spec: dict, extra: set[str] = frozenset()) -> None:
    for group in GROUPS:
        if group in spec:
            for part in spec[group]:
                _check_spec(where, part)
            unknown = set(spec) - {group} - extra
            if unknown:
                raise DatabaseError(f"{where}: unknown keys {sorted(unknown)}")
            return
    source = spec.get("source")
    if source not in SOURCE_KEYS:
        raise DatabaseError(f"{where}: unknown source {source!r}")
    unknown = set(spec) - {"source"} - SOURCE_KEYS[source] - MATCH_KEYS - extra
    if unknown:
        raise DatabaseError(f"{where}: unknown keys {sorted(unknown)} for source {source}")
    if "regex" in spec:
        try:
            re.compile(spec["regex"])
        except re.error as err:
            raise DatabaseError(f"{where}: bad regex: {err}") from err
    if source == "file" and "path" not in spec:
        raise DatabaseError(f"{where}: file source needs a path")
    if source == "command" and not spec.get("argv"):
        raise DatabaseError(f"{where}: command source needs argv")


def _check_scopes(where: str, scopes: list[str]) -> None:
    if not scopes:
        raise DatabaseError(f"{where}: systems must not be empty")
    for scope in scopes:
        if scope not in SCOPES:
            raise DatabaseError(f"{where}: unknown system {scope!r}, use one of {sorted(SCOPES)}")


def parse_entry(raw: dict, where: str, categories: dict[str, Category]) -> Entry:
    unknown = set(raw) - ENTRY_KEYS
    if unknown:
        raise DatabaseError(f"{where}: unknown keys {sorted(unknown)}")
    for key in ("id", "title", "summary", "categories", "systems"):
        if not raw.get(key):
            raise DatabaseError(f"{where}: missing {key}")
    _check_scopes(where, raw["systems"])
    for category in raw["categories"]:
        if category not in categories:
            raise DatabaseError(f"{where}: unknown category {category!r}")

    for key in ("when", "fixed_when"):
        for spec in raw.get(key, []):
            _check_spec(f"{where} {key}", spec)
    signals = []
    for item in raw.get("signals", []):
        spec = {k: v for k, v in item.items() if k not in ("says", "weight", "counter")}
        _check_spec(f"{where} signal", spec)
        if not item.get("says") or not 0 < item.get("weight", 0) < 1:
            raise DatabaseError(f"{where}: a signal needs says and a weight between 0 and 1")
        signals.append(Signal(item["says"], item["weight"], spec, item.get("counter", False)))

    fixes = []
    for item in raw.get("fix", []):
        unknown = set(item) - FIX_KEYS
        if unknown:
            raise DatabaseError(f"{where} fix: unknown keys {sorted(unknown)}")
        scopes = item.get("systems", raw["systems"])
        _check_scopes(f"{where} fix", scopes)
        if not item.get("steps"):
            raise DatabaseError(f"{where} fix: no steps")
        for step in item["steps"]:
            if step.get("op") not in OPS:
                raise DatabaseError(f"{where} fix: unknown op {step.get('op')!r}")
            problem = validate_step(step)
            if problem:
                raise DatabaseError(f"{where} fix: {problem}")
        fixes.append(Fix(scopes, item.get("reboot", False), item["steps"]))
    if not fixes and not raw.get("advice"):
        raise DatabaseError(f"{where}: needs a fix or advice")

    return Entry(
        id=raw["id"], title=raw["title"], summary=raw["summary"], categories=raw["categories"],
        systems=raw["systems"], keywords=raw.get("keywords", []), symptoms=raw.get("symptoms", []),
        auto_detect=raw.get("auto_detect", False), prior=raw.get("prior", 0.05),
        when=raw.get("when", []), fixed_when=raw.get("fixed_when", []), signals=signals,
        reproduce=raw.get("reproduce"), fixes=fixes, advice=raw.get("advice"),
    )


def load_database(data_dir: Path = DATA_DIR) -> Database:
    with open(data_dir / "categories.toml", "rb") as f:
        raw = tomllib.load(f)
    categories = {
        c["id"]: Category(c["id"], c["title"], c["icon"], c["blurb"], tuple(c.get("keywords", [])))
        for c in raw["category"]
    }
    entries: dict[str, Entry] = {}
    for path in sorted((data_dir / "entries").glob("*.toml")):
        try:
            with open(path, "rb") as f:
                entry = parse_entry(tomllib.load(f), path.name, categories)
        except tomllib.TOMLDecodeError as err:
            raise DatabaseError(f"{path.name}: {err}") from err
        if entry.id in entries:
            raise DatabaseError(f"{path.name}: duplicate id {entry.id}")
        entries[entry.id] = entry
    return Database(categories, entries)
