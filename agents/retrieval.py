"""Scripture retrieval over the complete Bible.

THE BIBLE IS THE ONLY CONTENT SOURCE. Heard does not retrieve from
commentary sites, forums, devotional blogs, or any other human opinion corpus.
Every verse the counsel agent is allowed to cite comes from the local
public-domain biblical text loaded here, and every citation is verified against
that text before it reaches a user (see agents/verifier.py).

Retrieval is lexical BM25, not a model call. Two reasons:

  1. Cost. Retrieval runs on every request; an LLM-based search over 31,000
     verses would dominate the token bill for no quality gain.
  2. Grounding. A lexical index can only ever return verses that actually
     exist, which makes fabricated references structurally impossible at the
     retrieval step.

Translations are selected by the user's tradition. Catholic users get the
Douay-Rheims, whose canon includes the deuterocanonical books; everyone else
defaults to the Berean Standard Bible for modern readability, with the King
James available for those who prefer it.
"""
import json
import math
import pickle
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import config

BIBLE_DIR = config.DATA_DIR / "bible"
CACHE_DIR = config.DATA_DIR / "_index_cache"

DEFAULT_TRANSLATION = "BSB"
TRADITION_TRANSLATION = {
    "catholic": "DRC",
    "orthodox": "DRC",   # closest available public-domain canon with deuterocanon
}

STOP = set("""a an the and or but if of to in on at for with without is are was were be been being
this that these those i me my we our you your he him his she her it its they them their as from by
not no so than then there here what which who whom how when where why do does did done have has had
will would shall should can could may might must about into over under again more most other some
such only own same too very just now unto thou thee thy ye shall hath saith upon""".split())

TOKEN_RE = re.compile(r"[a-z']+")

# BM25 parameters.
K1 = 1.4
B = 0.72


def tokenize(text):
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP and len(t) > 2]


@lru_cache(maxsize=1)
def manifest():
    with open(BIBLE_DIR / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


def available_translations():
    m = manifest()["translations"]
    return [{"code": c, "label": v["label"], "canon": v["canon"],
             "verses": v["verses"], "books": v["books"]} for c, v in m.items()]


def translation_for(tradition, requested=None):
    codes = set(manifest()["translations"])
    if requested in codes:
        return requested
    # A profile may hold several traditions. If any of them uses the larger
    # canon, use the translation that contains it, so no book they consider
    # scripture is missing from search.
    trads = tradition if isinstance(tradition, (list, tuple, set)) else [tradition]
    for t in trads:
        if t in TRADITION_TRANSLATION:
            return TRADITION_TRANSLATION[t]
    return DEFAULT_TRANSLATION


@lru_cache(maxsize=4)
def load(code=DEFAULT_TRANSLATION):
    """Return the verse list for a translation."""
    path = BIBLE_DIR / f"{code}.jsonl"
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


@lru_cache(maxsize=4)
def index(code=DEFAULT_TRANSLATION):
    """Inverted BM25 index. Built once, then cached to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{code}.idx"
    src = BIBLE_DIR / f"{code}.jsonl"
    if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
        try:
            with cache.open("rb") as f:
                return pickle.load(f)
        except Exception:
            pass

    rows = load(code)
    postings = defaultdict(list)   # term -> [(doc_id, tf), ...]
    lengths = []
    for i, row in enumerate(rows):
        toks = tokenize(row["text"])
        lengths.append(len(toks) or 1)
        tf = defaultdict(int)
        for t in toks:
            tf[t] += 1
        for t, n in tf.items():
            postings[t].append((i, n))
    avg_len = sum(lengths) / max(1, len(lengths))
    n_docs = len(rows)
    idf = {t: math.log(1 + (n_docs - len(p) + 0.5) / (len(p) + 0.5))
           for t, p in postings.items()}
    built = {"postings": dict(postings), "idf": idf,
             "lengths": lengths, "avg_len": avg_len, "n": n_docs}
    try:
        with cache.open("wb") as f:
            pickle.dump(built, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass
    return built


@lru_cache(maxsize=1)
def theme_anchors():
    """Curated struggle -> verse-reference map.

    These are hand-picked anchor verses for common struggles. They are a
    relevance PRIOR, not the corpus: search still runs over the whole Bible,
    and anchors simply get a scoring boost when their theme is active.
    """
    path = config.DATA_DIR / "verses.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = defaultdict(set)
    for v in data.get("verses", []):
        for t in v.get("themes", []):
            out[t].add(v["ref"].lower())
    return {k: frozenset(v) for k, v in out.items()}


@lru_cache(maxsize=1)
def themes():
    anchors = theme_anchors()
    if anchors:
        return sorted(anchors)
    return ["temptation"]


@lru_cache(maxsize=8)
def _ref_map(code):
    m = {}
    for i, r in enumerate(load(code)):
        m[_normalize_ref(r["ref"])] = i
    return m


def by_ref(ref, code=DEFAULT_TRANSLATION):
    """Exact reference lookup, e.g. 'John 3:16'. Returns None if not present."""
    rows = load(code)
    i = _ref_map(code).get(_normalize_ref(ref))
    return dict(rows[i], translation=code) if i is not None else None


# Models routinely decorate references: "John 3:16 (BSB)", "John 3:16, KJV",
# "John 3:16-18". Normalise all of that down to a lookup key rather than
# discarding a citation that is actually valid.
_PAREN = re.compile(r"\s*[\(\[][^)\]]*[\)\]]\s*")
_TRAILING_CODE = re.compile(r"[,;]?\s*\b(?:KJV|BSB|DRC|WEB|ASV|ESV|NIV|NASB|NKJV)\b\.?\s*$", re.I)
_RANGE = re.compile(r"^(.*?\d+:\d+)\s*[-–]\s*\d+[a-z]?$")

BOOK_ALIASES = {
    "psalms": "psalm", "song of songs": "song of solomon",
    "canticles": "song of solomon", "apocalypse": "revelation of john",
    "revelation": "revelation of john", "revelations": "revelation of john",
    "ecclesiasticus": "sirach",
}


def _normalize_ref(ref):
    s = " ".join(str(ref).strip().split())
    s = _PAREN.sub(" ", s)
    s = _TRAILING_CODE.sub("", s)
    s = " ".join(s.split()).lower().rstrip(".,;")
    m = _RANGE.match(s)
    if m:                      # a range cites its first verse
        s = m.group(1).strip()
    # Normalise numeric book prefixes: "1 John" / "I John" / "1st John"
    s = re.sub(r"^(?:([123])(?:st|nd|rd)?|(iii|ii|i))\s+",
               lambda m: (m.group(1) or {"i": "1", "ii": "2", "iii": "3"}[m.group(2)]) + " ",
               s)
    parts = s.split(" ", 1)
    if parts[0] in ("1", "2", "3") and len(parts) > 1:
        head, rest = parts[0] + " ", parts[1]
    else:
        head, rest = "", s
    m = re.match(r"^(.*?)\s+(\d+:\d+)$", rest)
    if m:
        book, loc = m.group(1), m.group(2)
        book = BOOK_ALIASES.get(book, book)
        s = f"{head}{book} {loc}"
    return s


def search(text, theme=None, k=6, code=DEFAULT_TRANSLATION, extra_terms=""):
    """BM25 search across the full text of one translation."""
    idx = index(code)
    rows = load(code)

    # The user's own words and the scripture-vocabulary expansion are weighted
    # differently. Raw colloquial text matches surface words ("stop", "late")
    # and drags in irrelevant verses, so the expanded biblical terms carry most
    # of the weight while the original text only breaks ties.
    weights = {}
    for t in tokenize(text):
        weights[t] = max(weights.get(t, 0.0), 1.0)
    for t in tokenize(extra_terms):
        weights[t] = weights.get(t, 0.0) + 3.0
    if not weights:
        weights = {t: 1.0 for t in tokenize(theme or "")}

    scores = defaultdict(float)
    for term, w in weights.items():
        p = idx["postings"].get(term)
        if not p:
            continue
        idf = idx["idf"][term]
        for doc_id, tf in p:
            dl = idx["lengths"][doc_id]
            denom = tf + K1 * (1 - B + B * dl / idx["avg_len"])
            scores[doc_id] += w * idf * (tf * (K1 + 1)) / denom

    anchors = theme_anchors().get(theme, frozenset()) if theme else frozenset()
    if anchors:
        rmap = _ref_map(code)
        for ref in anchors:
            i = rmap.get(ref)
            if i is not None:
                # Curated anchors are a prior, not a guarantee of top rank.
                scores[i] += 6.0

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]

    # Lexical search tends to return one edge of a topic: for "I can't forgive
    # my dad" every top hit was a variant of "if you do not forgive, you will
    # not be forgiven". The curated anchors are chosen to cover the pastoral
    # side too, so the best-scoring anchors are guaranteed a place, replacing
    # the weakest lexical hits, and the counsel agent has a real choice.
    if anchors:
        rmap = _ref_map(code)
        anchor_ids = [rmap[r] for r in anchors if r in rmap]
        in_ranked = {d for d, _ in ranked}
        missing = sorted((i for i in anchor_ids if i not in in_ranked),
                         key=lambda i: scores.get(i, 0), reverse=True)
        need = max(0, min(2, len(anchor_ids)) - sum(1 for d, _ in ranked if d in set(anchor_ids)))
        for i in missing[:need]:
            if len(ranked) >= k:
                ranked.pop()
            ranked.append((i, scores.get(i, 0.0)))
    out = []
    for doc_id, s in ranked:
        row = rows[doc_id]
        out.append({
            "ref": row["ref"], "book": row["book"], "ch": row["ch"], "v": row["v"],
            "text": row["text"], "translation": code,
            "score": round(s, 3),
            "anchor": row["ref"].lower() in anchors,
        })
    return out


def context(ref, code=DEFAULT_TRANSLATION, before=2, after=2):
    """Surrounding verses, so counsel is not built on a clipped half-sentence."""
    rows = load(code)
    i = _ref_map(code).get(_normalize_ref(ref))
    if i is None:
        return []
    lo, hi = max(0, i - before), min(len(rows), i + after + 1)
    base = rows[i]
    return [dict(r, translation=code, is_target=(j == i))
            for j, r in enumerate(rows[lo:hi], start=lo)
            if r["book"] == base["book"] and r["ch"] == base["ch"]]


def corpus_stats():
    m = manifest()["translations"]
    return {c: {"verses": v["verses"], "books": v["books"], "label": v["label"]}
            for c, v in m.items()}
