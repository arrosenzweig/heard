"""Awareness - a reflective profile built from what the user has shared.

Shaped like a personality-type result (a name, a one-line essence, tendencies
on spectrums, strengths, struggles, pitfalls, practices) but it is NOT a
validated psychological instrument and never claims to be. The interface labels
it "a reflection from your conversations, not a diagnosis".

Two guards keep it honest:

  EVIDENCE   The model is given the user's own history as numbered items
             (E = Talk messages, C = check-ins, K = commitments, P = profile).
             Every observation must cite item ids. Ids that don't exist are
             stripped, and an observation left with no real evidence is
             dropped. The interface shows "based on N" for each card.

  PRIVACY    It must stay general. Any card that names a person or place the
             user mentioned is dropped, so the profile can't leak details.

Scripture is never written by the model here: "verses to carry" are chosen by
retrieval from the real text for the user's main struggles.
"""
import json
import re
import time
from collections import Counter

import llm
import store
from agents import retrieval

MIN_ITEMS_FULL = 3   # below this, a starter profile from setup answers only

SYSTEM = """You write a reflective "awareness" profile for a user of Heard, a
Christian companion app. It helps them see themselves clearly: strengths,
struggles, patterns, and practices that would help. It reads like a warm,
perceptive mentor who shares their faith, not a clinician.

You get the user's history as numbered items:
  P*  their profile (tradition, struggles they named, convictions)
  E*  things they wrote in conversations
  C*  check-ins on a struggle (pull 1-5, whether they acted, trend)
  K*  commitments they made and how they went

RULES
- Every observation must cite the ids of the items it rests on, in "evidence".
  Never cite an id you were not given. If you cannot support something, leave
  it out. Fewer, true observations beat many guesses.
- Stay GENERAL. Never include names of people, places, workplaces, or any
  identifying detail. Say "a close relationship", not a name.
- Do not diagnose or use clinical labels. Do not quote scripture (verses are
  added separately).
- Speak to them in second person. Be honest about struggles and kind about the
  person, the way Jesus corrected: truthful, never condemning.
- The title is 2-4 words, evocative and dignified ("The Honest Wrestler",
  "The Quiet Rebuilder"). No four-letter codes.

Return JSON:
{
 "title": "...",
 "essence": "one sentence",
 "spectra": [ {"left": "...", "right": "...", "position": 0-100, "note": "one sentence", "evidence": ["E1", ...]} ],   // 3-4
 "strengths": [ {"title": "...", "detail": "1-2 sentences", "evidence": [...]} ],   // up to 3
 "struggles": [ {"title": "...", "detail": "...", "evidence": [...]} ],           // up to 3
 "pitfalls":  [ {"title": "...", "detail": "...", "evidence": [...]} ],           // up to 3: recurring patterns that trip them up
 "practices": [ {"title": "...", "detail": "one concrete, repeatable action", "why": "...", "evidence": [...]} ],   // 3-4
 "changed": "one or two sentences on what has shifted since the previous profile, or null"
}
position: 0 = fully the left description, 100 = fully the right."""

_CAP = re.compile(r"\b[A-Z][a-z]{2,}\b")
_ALLOWED_CAPS = {
    "God", "Jesus", "Lord", "Christ", "Spirit", "Holy", "Scripture", "Bible", "Christian", "Christians",
    "Gospel", "Father", "Savior", "Saviour", "Heard", "Sunday", "Sundays", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Catholic", "Orthodox", "Baptist", "Methodist", "Lutheran",
    "Anglican", "Reformed", "Presbyterian", "Pentecostal", "Anabaptist", "Evangelical", "Church",
    "The", "You", "Your", "When", "What", "This", "That", "Keep", "Notice", "Take", "Try", "Pause", "Name",
    "Choose", "Ask", "Tell", "Write", "Bring", "Pray", "Read", "Set", "Make", "Let", "Before", "After",
    "Each", "Every", "Instead", "Over", "Under", "Even", "Because", "While", "Once", "Then", "Some",
}


def _items(profile):
    """Number the user's history so every claim can point at its source."""
    items, text_of = [], {}
    trads = profile.get("tradition") or []
    if isinstance(trads, str):
        try:
            trads = json.loads(trads)
        except json.JSONDecodeError:
            trads = [trads]
    p_lines = [f"traditions: {', '.join(trads) or 'not stated'}",
               f"fighting: {', '.join(profile.get('fighting') or []) or 'none named'}",
               f"came through: {', '.join(profile.get('overcame') or []) or 'none named'}"]
    for cs in profile.get("custom_struggles") or []:
        p_lines.append(f"in their own words ({cs.get('kind')}): {cs.get('text')}")
    for k, v in (profile.get("beliefs") or {}).items():
        if isinstance(v, dict) and v.get("approved"):
            p_lines.append(f"conviction on {k}: {v.get('summary')}")
        elif isinstance(v, str) and v not in ("prefer_not_to_say", "still_discerning"):
            p_lines.append(f"conviction on {k}: {v}")
    for i, line in enumerate(p_lines, 1):
        items.append(f"P{i}: {line}")
        text_of[f"P{i}"] = line

    for i, e in enumerate(store.entries_for(profile["id"], limit=25), 1):
        if e["severity"] == "crisis":
            continue  # never fold a crisis disclosure into a personality profile
        when = time.strftime("%b %d", time.localtime(e["created"]))
        line = f"[{when}] ({e['theme']}, intensity {e['intensity']}/10) {e['text'][:400]}"
        items.append(f"E{i}: {line}")
        text_of[f"E{i}"] = e["text"]
    for i, c in enumerate(store.checkins_for(profile["id"], limit=25), 1):
        if c["trend"] == "crisis":
            continue
        when = time.strftime("%b %d", time.localtime(c["created"]))
        line = f"[{when}] {c['theme'].replace('custom:', '')}: pull {c['pull']}/5, acted {c['acted']}, felt {c['direction']}, trend {c['trend']}" \
               + (f"; note: {c['note'][:200]}" if c.get("note") else "")
        items.append(f"C{i}: {line}")
        text_of[f"C{i}"] = (c.get("note") or "")
    outcomes = {o["commitment_id"]: o["result"] for o in store.outcomes_for(profile["id"])}
    for i, k in enumerate(store.commitments_for(profile["id"])[:15], 1):
        line = f"commitment: {k['text']} -> {outcomes.get(k['id'], 'not yet followed up')}"
        items.append(f"K{i}: {line}")
        text_of[f"K{i}"] = k["text"]
    return items, text_of


def basis(profile):
    """Changes whenever there is new history, so the profile can refresh."""
    e = store.entries_for(profile["id"], limit=1)
    c = store.checkins_for(profile["id"], limit=1)
    return f"{len(store.entries_for(profile['id'], limit=200))}:{len(store.checkins_for(profile['id'], limit=200))}:" \
           f"{e[0]['id'] if e else '-'}:{c[0]['id'] if c else '-'}"


def _names_in_history(text_of):
    """Capitalised words the user wrote that aren't ordinary words: likely names."""
    found = set()
    for k, t in text_of.items():
        if k.startswith("P") or not t:
            continue
        for m in _CAP.finditer(t):
            before = t[:m.start()].rstrip()
            if not before or before.endswith((".", "!", "?", '"', "“")):
                continue  # sentence-initial capital, not a name
            if m.group(0) not in _ALLOWED_CAPS:
                found.add(m.group(0))
    return found


_IDS = re.compile(r"\s*[\[(]\s*(?:[PECK]\d+\s*[,;]?\s*)+[\])]")


def _strip_ids(text):
    """The model sometimes pastes evidence labels like "[E1, K2]" into prose."""
    return _IDS.sub("", str(text or "")).strip()


def _clean(section, valid_ids, names, need_evidence=True):
    out = []
    for item in section or []:
        if not isinstance(item, dict):
            continue
        ev = [x for x in (item.get("evidence") or []) if isinstance(x, str) and x in valid_ids]
        if need_evidence and not ev:
            continue  # unsupported: drop it
        text = " ".join(str(item.get(k, "")) for k in ("title", "detail", "why", "note", "left", "right"))
        if any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
            continue  # names someone the user mentioned: drop it (privacy)
        item = dict(item, evidence=ev, based_on=len(ev))
        for k in ("title", "detail", "why", "note", "left", "right"):
            if k in item:
                item[k] = _strip_ids(item[k])
        out.append(item)
    return out


def verses_to_carry(profile, k=3):
    """Real verses for the user's most frequent struggles, chosen by retrieval."""
    themes = Counter(e["theme"] for e in store.entries_for(profile["id"], limit=50) if e["severity"] != "crisis")
    for t in (profile.get("fighting") or []):
        themes[t] += 2
    code = retrieval.translation_for(profile.get("tradition") or [])
    with open(retrieval.config.DATA_DIR / "verses.json", encoding="utf-8") as f:
        anchors = json.load(f)["verses"]
    out, seen = [], set()
    for theme, _ in themes.most_common(4):
        for a in anchors:
            if theme in a["themes"]:
                v = retrieval.by_ref(a["ref"], code)
                if v and v["ref"] not in seen:
                    seen.add(v["ref"])
                    out.append(dict(v, for_theme=theme))
                    break
        if len(out) >= k:
            break
    return out


def build(profile, previous=None, trace=None):
    items, text_of = _items(profile)
    history_items = [i for i in items if not i.startswith("P")]
    starter = len(history_items) < MIN_ITEMS_FULL
    ctx = "\n".join(items)
    if starter:
        ctx += ("\n\nThere is very little history yet. Write a brief STARTER profile from the P items only, "
                "and say plainly in the essence that it will deepen as they talk with Heard.")
    if previous and previous.get("title"):
        ctx += (f"\n\nPREVIOUS PROFILE (for 'changed'): {previous.get('title')} - {previous.get('essence')}")
    out = llm.complete_json("awareness", [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": ctx}], trace=trace, max_tokens=4000)

    valid = set(text_of)
    names = _names_in_history(text_of)
    title = _strip_ids(out.get("title"))[:60]
    essence = _strip_ids(out.get("essence"))[:300]
    if any(re.search(rf"\b{re.escape(n)}\b", title + " " + essence) for n in names):
        title, essence = "Your reflection", ""
    spectra = _clean(out.get("spectra"), valid, names)
    for s in spectra:
        try:
            s["position"] = max(0, min(100, int(s.get("position", 50))))
        except (TypeError, ValueError):
            s["position"] = 50
    profile_out = {
        "title": title or "Your reflection",
        "essence": essence,
        "spectra": spectra[:4],
        "strengths": _clean(out.get("strengths"), valid, names)[:3],
        "struggles": _clean(out.get("struggles"), valid, names)[:3],
        "pitfalls": _clean(out.get("pitfalls"), valid, names)[:3],
        "practices": _clean(out.get("practices"), valid, names)[:4],
        "changed": (_strip_ids(out["changed"]) if out.get("changed") and previous and not
                    any(re.search(rf"\b{re.escape(n)}\b", str(out["changed"])) for n in names) else None),
        "verses": verses_to_carry(profile),
        "starter": starter,
        "counts": {"conversations": sum(1 for i in items if i.startswith("E")),
                   "checkins": sum(1 for i in items if i.startswith("C")),
                   "commitments": sum(1 for i in items if i.startswith("K"))},
        "generated": time.time(),
    }
    dropped = sum(len(out.get(k) or []) for k in ("spectra", "strengths", "struggles", "pitfalls", "practices")) \
        - sum(len(profile_out[k]) for k in ("spectra", "strengths", "struggles", "pitfalls", "practices"))
    profile_out["dropped_unsupported"] = max(0, dropped)
    if trace is not None:
        trace.step("awareness", {"starter": starter, "dropped": profile_out["dropped_unsupported"]})
    return profile_out


def trends(profile_id):
    """Per-struggle check-in trend for the tab's progress strip."""
    out = {}
    for c in reversed(store.checkins_for(profile_id, limit=100)):
        if c["trend"] == "crisis":
            continue
        out.setdefault(c["theme"], []).append({"pull": c["pull"], "trend": c["trend"], "t": c["created"]})
    return [{"theme": k, "label": k.replace("custom:", "").replace("_", " "), "points": v[-12:]} for k, v in out.items()]
