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


CUSTOM = "custom:"


def _pretty(theme):
    """Display label. Custom struggles are shown in the user's own words."""
    t = theme or ""
    if t.startswith(CUSTOM):
        return t[len(CUSTOM):]
    return t.replace("_", " ")


def match_theme(profile, theme):
    """The category used for fellowship and church matching. A custom struggle
    uses the category the user confirmed for it."""
    if theme and theme.startswith(CUSTOM):
        text = theme[len(CUSTOM):]
        for cs in profile.get("custom_struggles") or []:
            if isinstance(cs, dict) and cs.get("text") == text:
                return cs.get("theme")
        return None
    return theme


def active_themes(profile, days=14):
    """Struggles worth checking on: the ones the user said they're fighting,
    plus anything they've brought to Talk in the last two weeks."""
    themes = list(profile.get("fighting") or [])
    for cs in profile.get("custom_struggles") or []:
        if isinstance(cs, dict) and cs.get("kind") == "fighting" and cs.get("text"):
            themes.append(CUSTOM + cs["text"])
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
                    "match_theme": match_theme(profile, theme),
                    "last": last[0] if last else None,
                    "next_at": (last_t + hours * 3600) if last_t else None})
    return out


def questions(theme):
    t = _pretty(theme)
    # A custom struggle is a whole sentence in the user's words (shown as the
    # card title), so it can't be dropped into the middle of a question.
    pull_q = ("Since we last talked, how strong has the pull been?" if (theme or "").startswith(CUSTOM)
              else f"Since we last talked, how strong has the pull toward {t} been?")
    return [
        {"id": "pull", "type": "scale", "q": pull_q,
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
- If they are slipping, correct the way Jesus did: name it honestly, never
  shame who they are, and say plainly that you believe they can do better.
  Name one concrete next step for the next 24 hours.
- If they are gaining ground, help them notice what helped.
- No Bible citations here (this is a quick check-in, not counsel), no lists,
  no "I'm sorry to hear that".

Return JSON: {"trend": "overcoming"|"holding"|"deeper", "reply": "..."}
The trend you are given was computed from their answers. Keep it unless their
written note clearly says otherwise."""


def assess(profile, theme, pull, acted, direction, note="", trace=None,
           given_reply=None, screened=False):
    """Score one check-in, reply, and store it. Returns the stored result."""
    pull = max(1, min(5, int(pull)))
    acted = acted if acted in ACTED else "once"
    direction = direction if direction in DIRECTION else "holding"
    note = (note or "").strip()[:1500]

    history = store.checkins_for(profile["id"], theme, limit=3)
    prev = history[0] if history else None

    # A note is free text, so it is screened exactly like a chat message.
    tri = None
    if note and not screened:
        tri = triage.run(note, trace=trace)
        if tri["severity"] == "crisis":
            store.add_checkin(profile["id"], theme, pull, acted, direction, note,
                              "crisis", "", used_model=True)
            return {"mode": "crisis", "triage": tri, "resources": tri["resources"]}

    trend = rule_trend(pull, acted, direction, prev)
    t = _pretty(theme)
    used_model = False
    reply = TEMPLATES[trend].format(t=t)

    if given_reply:
        # Conversational check-in: the closing reply was already written in
        # the conversation, from everything they said. Don't write another.
        reply = given_reply
    elif note or trend == "deeper":
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
        "mode": "checkin", "theme": theme, "match_theme": match_theme(profile, theme),
        "trend": trend, "reply": reply,
        "escalate": escalate, "used_model": used_model,
        "safety_note": bool(tri and tri.get("safety_note")),
        "resources": (tri or {}).get("resources", []) if tri and tri.get("safety_note") else [],
    }


def series(profile_id, theme, limit=30):
    """Oldest-first history for the trend chart."""
    rows = store.checkins_for(profile_id, theme, limit=limit)
    return [{"t": r["created"], "pull": r["pull"], "trend": r["trend"], "acted": r["acted"]}
            for r in reversed(rows) if r["trend"] != "crisis"]


# ------------------------------------------------- personalized check-ins
# The three structured questions never change, so the trend stays comparable.
# On top of them, Heard adds one opening line and one specific question built
# ONLY from what the user actually wrote in Talk (and the commitments they
# made there). A guard rejects any proper name or quoted detail that does not
# appear in their own words, so a check-in can never "remember" something the
# user never said.
import re

PERSONAL_SYSTEM = """You write the opening of a short accountability check-in for a
Christian companion app. You are given what this person actually wrote about one
struggle, and any commitments they made.

Return JSON: {"intro": "...", "question": "..."} or {"intro": null, "question": null}

- intro: ONE sentence recalling a specific detail they told you, in second
  person ("Last time you told me about..."). Warm, not clinical.
- question: ONE specific follow-up question about that situation since then
  (did they apologise, did the conversation happen, did they keep the
  commitment). Answerable in a sentence or two.
- Use ONLY details that appear in what they wrote. Never add a name, place,
  event, relationship, or feeling they did not state. If you use a person's
  name, spell it exactly as they did.
- No Bible verses, no advice, no judgement in these two sentences.
- If nothing they wrote is specific enough to ask about, return nulls."""

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")
_COMMON_CAPS = {"I", "Last", "Since", "Did", "Have", "Has", "How", "What", "When", "Was", "Were", "You",
                "Your", "God", "Jesus", "Lord", "Christ", "Heard", "Talk", "Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday", "Saturday", "Sunday", "Is", "Are", "Do", "Does", "The", "A", "An",
                "It", "That", "This", "And", "Or", "But", "Any", "Anything", "After", "Before", "Earlier",
                "Yesterday", "Today", "Tonight", "Recently", "Were", "Will", "Would", "Could", "Can"}


def _grounded(text, sources):
    """Every capitalised word that isn't sentence-initial or common must appear
    in the user's own words (case-insensitive). Catches invented names."""
    src = " ".join(sources).lower()
    for m in _WORD.finditer(text or ""):
        w = m.group(0)
        start = m.start()
        sentence_start = start == 0 or text[:start].rstrip().endswith((".", "?", "!", '"', "“"))
        base = re.sub(r"['’]s$", "", w)
        if w[0].isupper() and not sentence_start and base not in _COMMON_CAPS:
            if base.lower() not in src:
                return False, w
    return True, None


def personal_prompt(profile, theme_key, match, trace=None, days=21, max_entries=4):
    """Opening line + specific question for one struggle, or None.

    Cached per (struggle, latest entry, latest check-in), so it is generated
    once per check-in cycle, not on every page load.
    """
    cutoff = time.time() - days * 86400
    # One message often touches several struggles ("I fought with Kyle... I'm
    # too proud to apologise" may be filed under pride, not anger). So recent
    # messages on the same struggle come first, then other recent ones, and the
    # model is told which struggle this check-in is about.
    recent = [e for e in store.entries_for(profile["id"], limit=60)
              if e["created"] >= cutoff and e["severity"] != "crisis"]
    same = [e for e in recent if e["theme"] == match]
    other = [e for e in recent if e["theme"] != match]
    entries = (same + other)[:max_entries]
    if not entries:
        return None
    last_ck = store.checkins_for(profile["id"], theme_key, limit=1)
    basis = f"{entries[0]['id']}:{last_ck[0]['id'] if last_ck else '-'}"
    cached = store.get_checkin_prompt(profile["id"], theme_key, basis)
    if cached is not None:
        return cached or None

    entry_ids = {e["id"] for e in entries}
    commits = [c for c in store.commitments_for(profile["id"]) if c.get("entry_id") in entry_ids][:3]
    sources = [e["text"] for e in entries] + [c["text"] for c in commits]
    if last_ck and last_ck[0].get("note"):
        sources.append(last_ck[0]["note"])

    ctx = (f"THIS CHECK-IN IS ABOUT: {_pretty(theme_key)}\n"
           "Only use a detail that relates to this struggle. If nothing they wrote relates to it, return nulls.\n\n"
           "WHAT THEY WROTE (most recent first):\n") + "\n".join(f"- {e['text'][:500]}" for e in entries)
    if commits:
        ctx += "\n\nCOMMITMENTS THEY MADE:\n" + "\n".join(f"- {c['text']}" for c in commits)
    if last_ck and last_ck[0].get("note"):
        ctx += f"\n\nTHEIR LAST CHECK-IN NOTE:\n- {last_ck[0]['note'][:400]}"

    out = llm.complete_json("accountability", [
        {"role": "system", "content": PERSONAL_SYSTEM},
        {"role": "user", "content": ctx}], trace=trace, max_tokens=1200)
    intro, question = out.get("intro"), out.get("question")
    payload = {}
    if intro and question:
        ok1, bad1 = _grounded(intro, sources)
        ok2, bad2 = _grounded(question, sources)
        if ok1 and ok2:
            payload = {"intro": str(intro).strip(), "question": str(question).strip(),
                       "based_on": len(entries)}
        elif trace is not None:
            trace.step("personal_rejected", {"ungrounded_word": bad1 or bad2})
    store.save_checkin_prompt(profile["id"], theme_key, basis, payload)
    return payload or None


# ------------------------------------------------- conversational check-ins
# A check-in can be a short conversation instead of a form. Heard asks
# naturally, follows what the person says, and when it has enough, proposes
# how to log it (pull, acted, direction). The person confirms or adjusts
# before anything is stored, so the trend chart stays honest.

CHAT_SYSTEM = """You are Heard, a Christian companion, checking in with someone about
one struggle they are fighting. This is a conversation between two people who
know each other, not a questionnaire.

Over the conversation you need to learn three things, WITHOUT listing them:
  pull       how strong the temptation or struggle has been since last time (1-5)
  acted      whether they acted on it: none | once | several
  direction  whether they feel they're getting stronger, holding steady, or slipping:
             stronger | holding | slipping

How to talk:
- Respond to what they actually said before asking anything. One question at a
  time, at most. Short: 1-3 sentences.
- Ask naturally ("Has it been pulling at you much this week?"), never as a form.
- Correct the way Jesus did if they slipped: honest about the wrong, never
  shaming who they are, and plainly hopeful that they can do better.
- No Bible citations here. No lists. No "I'm sorry to hear that".
- Use only details they have told you. Never invent names or events.
- By their third reply at the latest, wrap up even if you must estimate.

Return JSON:
  {"reply": "...", "done": false}
or, when you can reasonably tell all three,
  {"reply": "<warm closing, 2-3 sentences; if slipping, one concrete next step for the next 24 hours>",
   "done": true, "pull": 1-5, "acted": "none|once|several", "direction": "stronger|holding|slipping"}"""


def opener(profile, theme_key, trace=None):
    """First message of a check-in conversation. Personal when Heard has
    something specific (and grounded) to ask about; otherwise a plain opener
    that costs no model call."""
    match = match_theme(profile, theme_key)
    p = personal_prompt(profile, theme_key, match, trace=trace)
    if p:
        return {"text": f"{p['intro']} {p['question']}", "personal": True}
    label = _pretty(theme_key)
    if theme_key.startswith(CUSTOM):
        return {"text": f"Let's check in on what you told me: \"{label}\". How has it been since we last talked?",
                "personal": False}
    return {"text": f"Let's check in. How has {label} been since we last talked?", "personal": False}


def chat_turn(profile, theme_key, messages, trace=None):
    """One turn of a check-in conversation. `messages` is the whole exchange so
    far, ending with the person's latest reply."""
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    # Every reply is screened, exactly as in Talk.
    tri = triage.run(last, trace=trace) if last else None
    if tri and tri["severity"] == "crisis":
        return {"mode": "crisis", "resources": tri["resources"]}

    label = _pretty(theme_key)
    hist = store.checkins_for(profile["id"], theme_key, limit=1)
    ctx = f"The struggle: {label}\n"
    if hist:
        h = hist[0]
        ctx += f"Last check-in: pull {h['pull']}/5, acted {h['acted']}, trend {h['trend']}.\n"
    user_turns = sum(1 for m in messages if m["role"] == "user")
    if user_turns >= 3:
        ctx += "This is their third reply: wrap up now (done=true), estimating if you must.\n"
    convo = [{"role": "system", "content": CHAT_SYSTEM}, {"role": "user", "content": ctx}]
    for m in messages[-10:]:
        convo.append({"role": "assistant" if m["role"] == "assistant" else "user", "content": str(m["content"])[:1200]})
    out = llm.complete_json("accountability", convo, trace=trace, max_tokens=1500)

    reply = str(out.get("reply") or "").strip() or "Thank you for telling me. How strong has the pull been since we last talked?"
    res = {"mode": "chat", "reply": reply, "done": bool(out.get("done"))}
    if res["done"]:
        try:
            pull = max(1, min(5, int(out.get("pull"))))
        except (TypeError, ValueError):
            pull = 3
        res["proposed"] = {
            "pull": pull,
            "acted": out.get("acted") if out.get("acted") in ACTED else "once",
            "direction": out.get("direction") if out.get("direction") in DIRECTION else "holding",
        }
    res["safety_note"] = bool(tri and tri.get("safety_note"))
    res["resources"] = tri["resources"] if tri and tri.get("safety_note") else []
    return res
