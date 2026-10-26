"""Belief capture - the "did I hear you right?" agent.

On every contested question a user may pick one of the two common positions,
decline to answer, or choose the third option: say it in their own words, by
voice or by typing.

Free-form belief is the most dangerous input in this app. If Heard
misreads what someone believes, every later response is conditioned on a
distortion of their faith - and they will not necessarily notice, because the
response will still sound plausibly Christian. So free text is never used as
given. It goes through a loop:

    user speaks or types their view
        |
        v
    SIMPLIFY   restate it in plain language, shorter, no jargon
        |
        v
    SHOW IT BACK   "here is what I understood you to mean"
        |
        +---- user says no ----> they edit, or re-record, and we try again
        |
        v
    user approves  --->  only now is it stored and used

Nothing reaches the counsel agent until the user has confirmed the paraphrase
is actually what they meant. The stored record keeps both the original wording
and the approved summary, so a user can always see what the app thinks they
believe and correct it later.
"""
import json

import llm

SIMPLIFY_SYSTEM = """You restate a person's religious conviction in their own
sense, in plainer words. You are not evaluating it, improving it, or softening
it.

Rules:
  - Keep their actual position, including anything a listener might find
    controversial. You are not here to moderate them.
  - Strip filler, repetition, and spoken-language artifacts ("um", "like",
    "you know", false starts). Voice transcripts are messy; clean them.
  - Use ordinary words. No theological jargon unless they used it themselves.
  - First person, present tense, as if they wrote it carefully: "I believe..."
  - One to three sentences. Shorter is better.
  - If they expressed uncertainty or said they hold two views at once, keep
    that. Do not resolve it for them.
  - If what they said is too vague or unrelated to the question to summarise
    honestly, set needs_retry true and say what is unclear.

Return JSON:
  summary       the restatement, or null if needs_retry
  needs_retry   boolean
  unclear       if needs_retry, one sentence on what you could not follow
  nuances       list of short strings: any qualifications they made that a
                reader should not lose (may be empty)"""


def simplify(raw_text, question=None, trace=None):
    """Turn a spoken or typed belief into a short restatement for approval.

    Returns a dict the UI shows back to the user. NOTHING here is stored as the
    user's belief until they approve it.
    """
    raw = " ".join(str(raw_text or "").split())
    if len(raw) < 3:
        return {"ok": False, "needs_retry": True,
                "unclear": "I did not catch anything there. Try again?",
                "summary": None, "nuances": [], "original": raw}

    user = raw if not question else f"QUESTION THEY ARE ANSWERING:\n{question}\n\nWHAT THEY SAID:\n{raw}"
    out = llm.complete_json("beliefs", [
        {"role": "system", "content": SIMPLIFY_SYSTEM},
        {"role": "user", "content": user},
    ], trace=trace, max_tokens=1500)

    summary = out.get("summary")
    needs_retry = bool(out.get("needs_retry")) or not summary
    result = {
        "ok": not needs_retry,
        "needs_retry": needs_retry,
        "summary": (str(summary).strip() if summary else None),
        "unclear": str(out.get("unclear", ""))[:200] or None,
        "nuances": [str(n) for n in (out.get("nuances") or [])][:4],
        "original": raw,
    }
    if trace is not None:
        trace.step("belief_simplify", {"needs_retry": needs_retry,
                                       "chars_in": len(raw),
                                       "chars_out": len(result["summary"] or "")})
    return result


def approved_record(topic_id, original, summary, nuances=None):
    """The stored shape of a free-form belief, after the user approved it."""
    return {
        "position": "own_words",
        "summary": summary,
        "original": original,
        "nuances": nuances or [],
        "approved": True,
    }


def describe_for_prompt(topics, beliefs):
    """Render a user's belief profile for the counsel agent.

    Free-form convictions are passed as the user's APPROVED summary, never as
    the raw transcript, so the counsel agent conditions on what the user
    confirmed they meant.
    """
    if not beliefs:
        return ""
    by_id = {t["id"]: t for t in (topics or [])}
    lines = []
    neutral = {"prefer_not_to_say", "still_discerning", None, ""}
    for tid, val in beliefs.items():
        topic = by_id.get(tid, {})
        q = topic.get("question", tid)
        if isinstance(val, dict) and val.get("position") == "own_words":
            if not val.get("approved"):
                continue  # never use an unapproved paraphrase
            line = f"- {q} In their own words: {val.get('summary')}"
            if val.get("nuances"):
                line += " (" + "; ".join(val["nuances"]) + ")"
            lines.append(line)
        elif val in neutral:
            continue
        else:
            label = topic.get("position_a") if val == "position_a" else \
                    topic.get("position_b") if val == "position_b" else \
                    "holds a view between the two common positions" if val == "somewhere_between" else str(val)
            lines.append(f"- {q} {label}")
    return "\n".join(lines)
