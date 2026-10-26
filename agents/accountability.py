"""Accountability agent - comes back and asks how it's going.

For each struggle a user is fighting, Heard asks a brief check-in on the
schedule the user chose at onboarding (daily, every few days, weekly):

    1. How strong was the pull since last time?        1-5
    2. Did you act on it?                               not at all / once / more than once
    3. Which way do you feel you're heading?            stronger / holding / slipping
    4. Anything you want to say? (optional, typed or spoken)

It then names the trend - OVERCOMING, HOLDING, or DEEPER - and replies.

Cost design: the trend comes from a deterministic rule over the answers and the
previous check-in, so a check-in with no written note costs ZERO tokens. The
model is only called when the user writes something (it reads their words) or
when the rule says they are sliding deeper (a templated reply is not good
enough there). Any written note is also run through triage first, so a crisis
disclosed in a check-in is caught exactly as it would be in chat.

Escalation: two DEEPER check-ins in a row on the same struggle and the reply
points the user to a fellowship companion and a local church, because a person
sliding deeper needs people, not more prompts from an app.
"""
import time

import llm
import store
from agents import triage

PULL_LABELS = {1: "barely there", 2: "noticeable", 3: "strong", 4: "very strong", 5: "overwhelming"}
ACTED = ("none", "once", "several")
DIRECTION = ("stronger", "holding", "slipping")

DEFAULT_HOURS = 24


def _pretty(theme):
    return (theme or "").replace("_", " ")


def active_themes(profile, days=14):
    """Struggles worth checking on: the ones the user said they're fighting,
    plus anything they've brought to Talk in the last two weeks."""
    themes = list(profile.get("fighting") or [])
    cutoff = time.time() - days * 86400
    for e in store.entries_for(profile["id"], limit=40):
        if e["created"] >= cutoff and e["theme"] and e["theme"] not in themes \
                and e["severity"] != "crisis":
            themes.append(e["theme"])
    return themes


def due(profile):
    """Which struggles are due for a check-in right now."""
    hours = profile.get("checkin_hours") or DEFAULT_HOURS
    out = []
    for theme in active_themes(profile):
        last = store.checkins_for(profile["id"], theme, limit=1)
        last_t = last[0]["created"] if last else None
        is_due = last_t is None or (time.time() - last_t) >= hours * 3600
        out.append({"theme": theme, "label": _pretty(theme), "due": is_due,
                    "last": last[0] if last else None,
                    "next_at": (last_t + hours * 3600) if last_t else None})
    return out


def questions(theme):
    t = _pretty(theme)
    return [
        {"id": "pull", "type": "scale", "q": f"Since we last talked, how strong has the pull toward {t} been?",
         "options": [{"v": k, "label": f"{k} · {v}"} for k, v in PULL_LABELS.items()]},
        {"id": "acted", "type": "choice", "q": "Did you act on it?",
         "options": [{"v": "none", "label": "Not at all"}, {"v": "once", "label": "Once"},
                     {"v": "several", "label": "More than once"}]},
        {"id": "direction", "type": "choice", "q": "Which way do you feel you're heading?",
         "options": [{"v": "stronger", "label": "Getting stronger"}, {"v": "holding", "label": "Holding steady"},
                     {"v": "slipping", "label": "Slipping"}]},
        {"id": "note", "type": "text", "q": "Anything you want to say about it? (optional)"},
    ]


def rule_trend(pull, acted, direction, prev):
    """Deterministic trend. No model call."""
    if acted == "several" or direction == "slipping":
        return "deeper"
    if prev and pull > (prev.get("pull") or 0) and acted != "none":
        return "deeper"
    if acted == "none" and (pull <= 2 or direction == "stronger"):
        return "overcoming"
    if acted == "none" and prev and pull < (prev.get("pull") or 99):
        return "overcoming"
    return "holding"


TEMPLATES = {
    "overcoming": "That's real ground gained against {t}. Notice what helped this time, so you can lean on it again when the pull comes back.",
    "holding": "Holding steady against {t} still counts. Some seasons are about not losing ground. Keep doing what's keeping you here.",
    "deeper": "Thank you for being honest about {t}. Slipping isn't the end of the story, but it's a sign to bring someone else in.",
}

SYSTEM = """You are the accountability voice of Heard, a Christian companion app.
A person just answered a brief check-in about a struggle. Reply in 2-4 short
sentences, warm and direct, like a trusted friend who shares their faith.

- Respond to what they actually wrote. Do not repeat their scores back to them.
- If they are slipping, do not shame them and do not minimise it. Name one
  concrete next step for the next 24 hours.
- If they are gaining ground, help them notice what helped.
- No Bible citations here (this is a quick check-in, not counsel), no lists,
  no "I'm sorry to hear that".

Return JSON: {"trend": "overcoming"|"holding"|"deeper", "reply": "..."}
The trend you are given was computed from their answers. Keep it unless their
written note clearly says otherwise."""


def assess(profile, theme, pull, acted, direction, note="", trace=None):
    """Score one check-in, reply, and store it. Returns the stored result."""
    pull = max(1, min(5, int(pull)))
    acted = acted if acted in ACTED else "once"
    direction = direction if direction in DIRECTION else "holding"
    note = (note or "").strip()[:1500]

    history = store.checkins_for(profile["id"], theme, limit=3)
    prev = history[0] if history else None

    # A note is free text, so it is screened exactly like a chat message.
    tri = None
    if note:
        tri = triage.run(note, trace=trace)
        if tri["severity"] == "crisis":
            store.add_checkin(profile["id"], theme, pull, acted, direction, note,
                              "crisis", "", used_model=True)
            return {"mode": "crisis", "triage": tri, "resources": tri["resources"]}

    trend = rule_trend(pull, acted, direction, prev)
    t = _pretty(theme)
    used_model = False
    reply = TEMPLATES[trend].format(t=t)

    if note or trend == "deeper":
        used_model = True
        ctx = (f"Struggle: {t}\nPull: {pull}/5 ({PULL_LABELS[pull]})\nActed on it: {acted}\n"
               f"Heading: {direction}\nComputed trend: {trend}\n")
        if prev:
            ctx += f"Previous check-in: pull {prev['pull']}/5, acted {prev['acted']}, trend {prev['trend']}\n"
        if note:
            ctx += f"\nWhat they wrote:\n{note}"
        out = llm.complete_json("accountability", [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": ctx}], trace=trace, max_tokens=1500)
        if out.get("reply"):
            reply = str(out["reply"]).strip()
        if out.get("trend") in ("overcoming", "holding", "deeper"):
            trend = out["trend"]

    # Two DEEPER in a row on the same struggle -> point toward people.
    escalate = trend == "deeper" and prev is not None and prev.get("trend") == "deeper"

    store.add_checkin(profile["id"], theme, pull, acted, direction, note, trend, reply, used_model)
    if trace is not None:
        trace.step("checkin", {"theme": theme, "trend": trend, "used_model": used_model,
                               "escalate": escalate})
    return {
        "mode": "checkin", "theme": theme, "trend": trend, "reply": reply,
        "escalate": escalate, "used_model": used_model,
        "safety_note": bool(tri and tri.get("safety_note")),
        "resources": (tri or {}).get("resources", []) if tri and tri.get("safety_note") else [],
    }


def series(profile_id, theme, limit=30):
    """Oldest-first history for the trend chart."""
    rows = store.checkins_for(profile_id, theme, limit=limit)
    return [{"t": r["created"], "pull": r["pull"], "trend": r["trend"], "acted": r["acted"]}
            for r in reversed(rows) if r["trend"] != "crisis"]
