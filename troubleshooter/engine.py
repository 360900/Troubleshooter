"""Turns a database entry plus evidence from this machine into a verdict and a percentage.

Confidence is a noisy-OR: each matching signal independently explains the problem with
probability `weight`, starting from the entry's prior. Counter signals scale it down.
What the user told us (they picked this entry, or a search matched it) nudges the prior
but can't make a problem look certain without evidence from the machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import patches
from .database import Database, Entry, Fix, Signal
from .probes import Evidence, probe, probe_window
from .system import System

RECOMMEND_AT = 50  # shown unprompted at or above this
MAX_CONFIDENCE = 99
HINT_ENTRY = 0.25  # the user picked exactly this problem
HINT_CATEGORY = 0.05  # the user picked a category holding it
HINT_SEARCH = 0.20  # scaled by how well a search matched

APPLIES = "applies"
FIXED = "fixed"  # the fix is already in place
NOT_APPLICABLE = "not_applicable"  # e.g. no such hardware
WRONG_SYSTEM = "wrong_system"  # the entry isn't meant for this distro


@dataclass
class Finding:
    signal: Signal
    evidence: Evidence


@dataclass
class Diagnosis:
    entry: Entry
    status: str
    confidence: int = 0
    found: list[Finding] = field(default_factory=list)
    unread: int = 0  # signals whose source couldn't be read
    fix: Fix | None = None

    @property
    def recommended(self) -> bool:
        return self.status == APPLIES and self.confidence >= RECOMMEND_AT

    @property
    def level(self) -> str:
        if self.confidence >= 75:
            return "High"
        return "Medium" if self.confidence >= RECOMMEND_AT else "Low"


def _choose_fix(entry: Entry, system: System) -> Fix | None:
    return next((f for f in entry.fixes if system.in_scopes(f.systems)), None)


def evaluate(entry: Entry, system: System, hint: float = 0.0) -> Diagnosis:
    if not system.in_scopes(entry.systems):
        return Diagnosis(entry, WRONG_SYSTEM)
    if not all(probe(system, spec).matched for spec in entry.when):
        return Diagnosis(entry, NOT_APPLICABLE)

    fix = _choose_fix(entry, system)
    if any(probe(system, spec).matched for spec in entry.fixed_when):
        return Diagnosis(entry, FIXED, fix=fix)

    doubt = 1 - min(entry.prior + hint, 0.6)
    damp = 1.0
    found, unread = [], 0
    for signal in entry.signals:
        evidence = probe(system, signal.spec)
        unread += not evidence.known
        weight = signal.weight
        if not signal.counter:
            # Appearing while the user made the problem happen counts far more than
            # a similar line from hours ago.
            window = probe_window(system, signal.spec)
            if window.matched:
                evidence = window
                weight = 1 - (1 - weight) ** 2
        if not evidence.matched:
            continue
        if signal.counter:
            damp *= 1 - weight
        else:
            doubt *= 1 - weight
        found.append(Finding(signal, evidence))

    confidence = round((1 - doubt) * damp * 100)
    return Diagnosis(entry, APPLIES, min(confidence, MAX_CONFIDENCE), found, unread, fix)


def rank(diagnoses: list[Diagnosis]) -> list[Diagnosis]:
    return sorted(diagnoses, key=lambda d: -d.confidence)


def scan(db: Database, system: System) -> list[Diagnosis]:
    """The check on launch: only entries marked safe to recommend unprompted."""
    results = [evaluate(e, system) for e in db.entries.values() if e.auto_detect]
    return rank([d for d in results if d.recommended and d.found])


def diagnose_entry(entry: Entry, system: System) -> Diagnosis:
    return evaluate(entry, system, HINT_ENTRY)


def diagnose_category(db: Database, category_id: str, system: System) -> list[Diagnosis]:
    return rank([evaluate(e, system, HINT_CATEGORY) for e in db.in_category(category_id)])


def diagnose_hits(hits: list[tuple[Entry, float]], system: System) -> list[Diagnosis]:
    return rank([evaluate(entry, system, HINT_SEARCH * score) for entry, score in hits])


def can_reproduce(diagnoses: list[Diagnosis]) -> bool:
    """Reproducing helps when nothing is conclusive and logs could still settle it."""
    live = [d for d in diagnoses if d.status == APPLIES]
    if not live or any(d.recommended and d.found for d in live):
        return False
    return any(s.spec.get("source") == "journal" for d in live for s in d.entry.signals)


def build_plan(diagnosis: Diagnosis, system: System) -> patches.Plan | None:
    if not diagnosis.fix:
        return None
    return patches.build_plan(diagnosis.entry.id, diagnosis.entry.title, diagnosis.fix.steps,
                              diagnosis.fix.reboot, system)
