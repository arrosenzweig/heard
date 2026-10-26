"""Pipeline orchestration - one user entry, start to finish.

    entry text
        |
        v
    [1] TRIAGE          theme, severity, intensity, scripture-vocabulary expansion
        |
        +---- severity == crisis ----> STOP. Real help. No counsel, no verses,
        |                              no matching. A person in acute danger is
        |                              not handed a devotional.
        v
    [2] RETRIEVAL       BM25 over the full biblical text (no model call)
        |
        v
    [3] COUNSEL         belief-conditioned response, adaptive tone
        |
        v
    [4] VERIFIER        every citation checked against the real text;
        |               fabrications deleted before display (no model call)
        v
    [5] MATCHER         companions who came through this same struggle
                        (no model call)

Only steps 1 and 3 cost tokens. That is deliberate: it keeps cost per run low
and keeps the grounding and safety logic deterministic and auditable.
"""
import llm
import store
from agents import counsel, matcher, retrieval, triage, verifier


def crisis_response(tri):
    """What a user sees when triage detects acute danger.

    Written by hand, not generated. In a crisis the app must say the same
    reliable thing every time, with real contact details that cannot drift.
    """
    return {
        "mode": "crisis",
        "headline": "This sounds like more than an app should carry with you.",
        "body": (
            "What you have just described needs a real person, right now, not a verse "
            "and not advice from software. Please reach out to one of these. They are "
            "free, they are confidential, and the people answering are trained for "
            "exactly this.\n\n"
            "Your faith is not in question here, and reaching for help is not a failure "
            "of it. Call your priest or pastor too if you have one. But please start "
            "with the people below."
        ),
        "resources": tri.get("resources", []),
        "crisis_kind": tri.get("crisis_kind"),
    }


def out_of_scope_response(tri):
    return {
        "mode": "out_of_scope",
        "headline": "This one needs a professional, not a companion app.",
        "body": (
            "What you are describing is really a medical, legal, or financial question. "
            "Heard is built for moral and spiritual struggle, and answering this "
            "well needs someone qualified and accountable. Please take it to a doctor, "
            "a lawyer, or a licensed advisor.\n\n"
            "If there is a spiritual weight attached to it - fear, shame, a decision you "
            "are dreading - write that part and I will stay with you there."
        ),
        "resources": [],
    }


def run(text, profile=None, history=None, k_verses=6, translation=None,
        with_matches=True, label="entry", conversation=None, sos=False):
    """Process one entry. Returns (result, trace_summary)."""
    trace = llm.Trace(label=label)

    # ---------------------------------------------------------------- [1]
    tri = triage.run(text, trace=trace)

    if tri["severity"] == "crisis":
        trace.step("halt", {"reason": "crisis", "kind": tri["crisis_kind"]})
        return {"triage": tri, "response": crisis_response(tri),
                "verses": [], "verification": None, "matches": []}, trace.summary()

    if tri["scope"] == "out_of_scope":
        trace.step("halt", {"reason": "out_of_scope"})
        return {"triage": tri, "response": out_of_scope_response(tri),
                "verses": [], "verification": None, "matches": []}, trace.summary()

    # ---------------------------------------------------------------- [2]
    code = retrieval.translation_for((profile or {}).get("tradition"), translation)
    verses = retrieval.search(text, theme=tri["theme"], k=k_verses, code=code,
                              extra_terms=tri["search_terms"])
    trace.step("retrieval", {"translation": code, "n": len(verses),
                             "refs": [v["ref"] for v in verses],
                             "model_calls": 0})

    # ---------------------------------------------------------------- [3]
    adv = counsel.run(text, tri, verses, profile=profile, history=history, trace=trace,
                      conversation=conversation, sos=sos)

    # ---------------------------------------------------------------- [4]
    kept, report = verifier.verify_citations(
        adv["citations_claimed"], adv["allowed_refs"], code=code)
    prose = verifier.verify_prose(adv["reflection"], code=code)
    report["prose_refs"] = prose
    trace.step("verification", {"claimed": report["n_claimed"], "kept": report["n_kept"],
                                "removed": report["n_removed"],
                                "unresolvable_in_prose": prose["unresolvable"],
                                "model_calls": 0})

    # ---------------------------------------------------------------- [5]
    matches = []
    if with_matches and profile:
        matches = matcher.suggest(profile, tri["theme"], k=4, severity=tri["severity"])
        trace.step("matching", {"n": len(matches), "model_calls": 0})

    result = {
        "triage": tri,
        "response": {
            "mode": "counsel",
            "tone": adv["tone"],
            "tone_reason": adv["tone_reason"],
            "reflection": adv["reflection"],
            "steps": adv["steps"],
            "commitment": adv["commitment"],
            "prayer": adv["prayer"],
            "human_nudge": adv["human_nudge"],
        },
        "verses": kept,
        "retrieved": verses,
        "verification": report,
        "matches": matches,
        "translation": code,
    }
    return result, trace.summary()
