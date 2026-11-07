"""Finds database entries from loose, plain-language descriptions.

There's no exact-match requirement: "my game flashes on the screen" should land on
flickering entries that never use the word "flashes". It runs BM25 over two kinds of term:
the words themselves, and the concepts from data/lexicon.toml they belong to. Words the
database has never seen fall back to fuzzy spelling matches.
"""
from __future__ import annotations

import difflib
import math
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field

from .database import DATA_DIR, Category, Database, Entry

K1, B = 1.4, 0.6
CONCEPT_WEIGHT = 0.8
FUZZY_WEIGHT = 0.6
MIN_SCORE = 0.12
STRONG_MATCH = 0.45  # what to show: hits far weaker than the best one are noise
FIELD_WEIGHTS = {"title": 3, "keywords": 3, "symptoms": 2, "summary": 1, "categories": 2}
STOPWORDS = set("""
a about after again all also am an and any are as at be because been before being but by can could did do does
doing during every few for from get gets getting got had has have having help how i i'm i've if in into is it it's
its just keep keeps like me mine more most my myself need needs no not now of off on once only or other our out over
own please really same she should so some something somehow sometimes still such than that the their them then there
these they thing things this those to too try trying under until up us very want was we were what when where which
while who why will with would you your computer pc machine system linux
""".split())
_WORD = re.compile(r"[a-z0-9][a-z0-9.+\-']*")


def _stem(word: str) -> str:
    for suffix, keep in (("ing", 4), ("ed", 4), ("es", 4), ("s", 4)):
        if word.endswith(suffix) and len(word) - len(suffix) >= keep - 1:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> list[str]:
    out = []
    for raw in _WORD.findall(text.lower()):
        raw = raw.strip(".-'")
        if not raw or raw in STOPWORDS:
            continue
        out.append(_stem(raw))
        if "-" in raw:  # nvidia-open is also "nvidia" and "open"
            out += [_stem(part) for part in raw.split("-") if part and part not in STOPWORDS]
    return out


class Lexicon:
    def __init__(self, groups: list[list[str]]):
        self.words: dict[str, set[int]] = {}
        self.phrases: dict[str, set[int]] = {}
        for index, group in enumerate(groups):
            for member in group:
                member = member.lower()
                if " " in member or "-" in member or "'" in member:
                    self.phrases.setdefault(member, set()).add(index)
                else:
                    self.words.setdefault(_stem(member), set()).add(index)

    def concepts(self, text: str) -> set[int]:
        text = " " + re.sub(r"\s+", " ", text.lower().replace("’", "'")) + " "
        found: set[int] = set()
        for phrase, ids in self.phrases.items():
            if f" {phrase} " in text or f" {phrase}." in text or f" {phrase}," in text:
                found |= ids
        for word in _words(text):
            found |= self.words.get(word, set())
        return found


@dataclass
class Document:
    target: Entry | Category
    terms: Counter = field(default_factory=Counter)
    length: float = 0.0


@dataclass
class Hit:
    target: Entry | Category
    score: float

    @property
    def is_category(self) -> bool:
        return isinstance(self.target, Category)


class SearchIndex:
    def __init__(self, db: Database):
        with open(DATA_DIR / "lexicon.toml", "rb") as f:
            self.lexicon = Lexicon(tomllib.load(f)["groups"])
        self.docs: list[Document] = []
        for entry in db.entries.values():
            self.docs.append(self._index(entry, {
                "title": entry.title, "summary": entry.summary, "keywords": " ".join(entry.keywords),
                "symptoms": " . ".join(entry.symptoms),
                "categories": " ".join(db.categories[c].title for c in entry.categories),
            }))
        for category in db.categories.values():
            self.docs.append(self._index(category, {
                "title": category.title, "summary": category.blurb, "keywords": " ".join(category.keywords),
            }))
        self.avg_length = sum(d.length for d in self.docs) / max(len(self.docs), 1)
        self.df: Counter = Counter(term for d in self.docs for term in d.terms)
        self.vocab = sorted({t for d in self.docs for t in d.terms if isinstance(t, str)})

    def _index(self, target, fields: dict[str, str]) -> Document:
        doc = Document(target)
        for name, text in fields.items():
            weight = FIELD_WEIGHTS.get(name, 1)
            for word in _words(text):
                doc.terms[word] += weight
            for concept in self.lexicon.concepts(text):
                doc.terms[concept] += weight
            doc.length += len(text.split()) * weight
        return doc

    def _idf(self, term) -> float:
        n = self.df.get(term, 0)
        return math.log(1 + (len(self.docs) - n + 0.5) / (n + 0.5))

    def _query_terms(self, query: str) -> dict:
        terms: dict = {}
        for word in _words(query):
            if word in self.df:
                terms[word] = 1.0
            elif word not in self.lexicon.words:
                for close in difflib.get_close_matches(word, self.vocab, n=2, cutoff=0.8):
                    terms[close] = max(terms.get(close, 0), FUZZY_WEIGHT)
                    for concept in self.lexicon.concepts(close):
                        terms[concept] = max(terms.get(concept, 0), FUZZY_WEIGHT * CONCEPT_WEIGHT)
        for concept in self.lexicon.concepts(query):
            terms[concept] = max(terms.get(concept, 0), CONCEPT_WEIGHT)
        return terms

    def search(self, query: str, limit: int = 10) -> list[Hit]:
        terms = self._query_terms(query)
        if not terms:
            return []
        # Scores are scaled so that matching every query term well lands near 1.
        ideal = sum(self._idf(t) * w * (K1 + 1) * 0.75 for t, w in terms.items()) or 1
        hits = []
        for doc in self.docs:
            score = 0.0
            for term, weight in terms.items():
                tf = doc.terms.get(term, 0)
                if tf:
                    norm = K1 * (1 - B + B * doc.length / self.avg_length)
                    score += self._idf(term) * weight * tf * (K1 + 1) / (tf + norm)
            if score / ideal >= MIN_SCORE:
                hits.append((score, Hit(doc.target, min(score / ideal, 1.0))))
        # Rank on the raw score: many strong matches reach the 1.0 cap, and they still differ.
        hits.sort(key=lambda pair: -pair[0])
        return [hit for _, hit in hits[:limit]]
