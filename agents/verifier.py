"""Citation verifier - Heard's primary generation-side defense.

Language models fabricate scripture. They invent references that do not exist,
attach real references to invented wording, and blend verses together. In a
discipleship app this is the most damaging failure mode available: a user is
told that God said something God did not say, in a moment when they are
unusually inclined to believe it.

This module is the gate. Every citation produced by the counsel agent is
checked against the local biblical text before it can reach a user:

  1. EXISTENCE   does this reference resolve to a real verse in the corpus?
  2. FIDELITY    if the model quoted wording, does that wording actually match
                 the verse text? (token-level overlap, punctuation-insensitive)
  3. PERMISSION  was this verse actually among the ones retrieval handed to the
                 counsel agent, or did the model reach outside its evidence?

Citations failing 1 or 2 are REMOVED, never shown. Citations failing only 3 are
kept but flagged, since the verse is real even though it was recalled rather
than retrieved.

The verifier is deterministic and uses no model call, so it cannot itself
hallucinate and costs nothing per request.
"""
import re

from agents import retrieval

WORD_RE = re.compile(r"[a-z]+")


def _words(s):
    return WORD_RE.findall((s or "").lower())


def _overlap(quoted, actual):
    """Fraction of the quoted wording that appears in the real verse."""
    q, a = _words(quoted), set(_words(actual))
    if not q:
        return 1.0
    return sum(1 for w in q if w in a) / len(q)


def verify_citations(citations, allowed_refs, code=retrieval.DEFAULT_TRANSLATION,
                     fidelity_threshold=0.72):
    """Check a list of {ref, quote?, why?} dicts against the biblical text.

    Returns (kept, report). `kept` is safe to display. `report` is the audit
    trail used in the write-up and surfaced in the app's trace view.
    """
    # Compare normalised references, so "Proverbs 15:1 (BSB)" counts as the
    # retrieved "Proverbs 15:1" rather than as something the model reached for.
    allowed = {retrieval._normalize_ref(r) for r in allowed_refs}
    kept, checks = [], []

    for c in citations or []:
        ref = str(c.get("ref", "")).strip()
        quote = c.get("quote") or c.get("text") or ""
        rec = {"ref": ref, "exists": False, "fidelity": None,
               "retrieved": False, "verdict": "removed", "reason": ""}

        verse = retrieval.by_ref(ref, code) if ref else None
        if verse is None:
            rec["reason"] = "reference does not exist in the corpus"
            checks.append(rec)
            continue

        rec["exists"] = True
        rec["retrieved"] = retrieval._normalize_ref(ref) in allowed

        fidelity = _overlap(quote, verse["text"]) if quote else None
        rec["fidelity"] = round(fidelity, 3) if fidelity is not None else None

        if fidelity is not None and fidelity < fidelity_threshold:
            rec["verdict"] = "removed"
            rec["reason"] = (f"quoted wording does not match the text "
                             f"(overlap {fidelity:.2f} < {fidelity_threshold})")
            checks.append(rec)
            continue

        rec["verdict"] = "kept" if rec["retrieved"] else "kept_unretrieved"
        if not rec["retrieved"]:
            rec["reason"] = "real verse, but not among the retrieved evidence"

        # Always display the corpus text, never the model's version of it.
        kept.append({
            "ref": verse["ref"],
            "text": verse["text"],
            "translation": verse.get("translation", code),
            "why": str(c.get("why", ""))[:400],
            "from_retrieval": rec["retrieved"],
        })
        checks.append(rec)

    report = {
        "n_claimed": len(citations or []),
        "n_kept": len(kept),
        "n_removed": sum(1 for c in checks if c["verdict"] == "removed"),
        "n_unretrieved": sum(1 for c in checks if c["verdict"] == "kept_unretrieved"),
        "fidelity_threshold": fidelity_threshold,
        "checks": checks,
    }
    return kept, report


def scan_text_for_refs(text):
    """Find bare references written into prose, e.g. 'as Romans 8:28 says'.

    Prose citations bypass the structured citation list, so they are extracted
    and checked too.
    """
    pattern = re.compile(
        r"\b((?:[1-3]\s*)?(?:[A-Z][a-z]+)(?:\s+of\s+[A-Z][a-z]+)?)\s+(\d{1,3}):(\d{1,3})(?:\s*-\s*\d{1,3})?\b")
    return [{"ref": f"{m.group(1).strip()} {m.group(2)}:{m.group(3)}", "span": m.span()}
            for m in pattern.finditer(text or "")]


def verify_prose(text, code=retrieval.DEFAULT_TRANSLATION):
    """Report any in-prose references that do not resolve to a real verse."""
    found = scan_text_for_refs(text)
    bad = []
    for f in found:
        if retrieval.by_ref(f["ref"], code) is None:
            bad.append(f["ref"])
    return {"found": [f["ref"] for f in found], "unresolvable": bad}
