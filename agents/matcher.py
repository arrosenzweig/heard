"""Overcomer matching - the part of Heard that is not a chatbot.

The mechanic: connect a person fighting a struggle right now with a person who
has fought that same struggle and come through it, or who is fighting it
consistently and staying free. This is the sponsor model that recovery
fellowships have used for decades, carried into a faith context.

Scoring is deterministic and costs no tokens. Ranking people by a model call
would be slower, more expensive, and less auditable, and the signals that
matter here are structured fields rather than prose.

SAFETY RULES BAKED INTO MATCHING
  - Nobody in an acute crisis is offered as a match, and nobody in an acute
    crisis is shown a match list. Crisis routes to real help, not to peers.
  - Contact details are never included in a match result. They are released
    only after both sides accept (see app.reveal_contact).
  - Location is used at city granularity only.
  - A user can browse profiles, but the suggested list is what the app leads
    with, because browsing people by the sin they confessed is the exact
    pattern that attracts predatory use.
"""
import json

import store


def _trads(p):
    v = p.get("tradition")
    if isinstance(v, list):
        return set(v)
    if not v:
        return set()
    try:
        x = json.loads(v)
        return set(x) if isinstance(x, list) else {x}
    except (json.JSONDecodeError, TypeError):
        return {v}

# A person who has come through a struggle is the strongest signal; someone
# actively and successfully fighting it is next; shared tradition and city
# matter, but less than lived experience of the same thing.
W_OVERCAME = 10.0
W_FIGHTING_SAME = 4.0
W_SAME_TRADITION = 3.0
W_SAME_CITY = 2.0
W_BELIEF_AGREE = 0.6
W_OPEN_TO_CONTACT = 1.5
W_SEEKER_PAIR = 1.0


def _belief_alignment(a, b):
    """Shared, non-neutral convictions. Only counts where BOTH stated a view."""
    ba, bb = (a.get("beliefs") or {}), (b.get("beliefs") or {})
    neutral = {"prefer_not_to_say", "still_discerning", None, ""}
    shared = 0
    clash = 0
    for k, va in ba.items():
        vb = bb.get(k)
        # Own-words convictions are free text. Two people's own words cannot be
        # honestly scored as "agree" or "disagree" by comparing values, so they
        # are left out of matching rather than guessed at.
        if isinstance(va, dict) or isinstance(vb, dict):
            continue
        if va in neutral or vb in neutral:
            continue
        if va == vb:
            shared += 1
        else:
            clash += 1
    return shared, clash


def score(seeker_profile, candidate, theme):
    """Score `candidate` as a potential companion for `seeker_profile`."""
    if candidate["id"] == seeker_profile["id"]:
        return None
    if candidate.get("kind") != "person":
        return None

    reasons = []
    s = 0.0

    overcame = [t.lower() for t in (candidate.get("overcame") or [])]
    fighting = [t.lower() for t in (candidate.get("fighting") or [])]
    t = (theme or "").lower()

    if t and t in overcame:
        s += W_OVERCAME
        reasons.append("has come through this same struggle")
    elif t and t in fighting:
        s += W_FIGHTING_SAME
        reasons.append("is fighting this same struggle now")
    else:
        # No lived experience of this theme: not a companion for this moment.
        return None

    shared_trads = _trads(candidate) & _trads(seeker_profile) - {"exploring", "unsure", "other"}
    if shared_trads:
        s += W_SAME_TRADITION
        reasons.append("shares your tradition")
    if candidate.get("city") and candidate["city"] == seeker_profile.get("city"):
        s += W_SAME_CITY
        reasons.append("same city")

    shared, clash = _belief_alignment(seeker_profile, candidate)
    s += W_BELIEF_AGREE * shared - 0.4 * clash
    if shared >= 2:
        reasons.append(f"{shared} shared convictions")

    if candidate.get("open_to_contact"):
        s += W_OPEN_TO_CONTACT
        reasons.append("open to being contacted")

    if seeker_profile.get("is_seeker") and candidate.get("is_seeker"):
        s += W_SEEKER_PAIR
        reasons.append("also still working out what they believe")

    return {
        "id": candidate["id"],
        "handle": candidate["handle"],
        "city": candidate.get("city"),
        "tradition": candidate.get("tradition"),
        "bio": candidate.get("bio"),
        "overcame": candidate.get("overcame") or [],
        "fighting": candidate.get("fighting") or [],
        "synthetic": bool(candidate.get("synthetic")),
        "open_to_contact": bool(candidate.get("open_to_contact")),
        "score": round(s, 2),
        "reasons": reasons,
        "relation": "overcame" if t in overcame else "fighting",
    }


def suggest(profile, theme, k=4, severity="ordinary"):
    """Ranked companions for this person and this struggle.

    Returns [] during a crisis: a person in acute danger is routed to real
    help, never handed a list of peers.
    """
    if severity == "crisis":
        return []
    out = []
    for cand in store.all_profiles("person"):
        sc = score(profile, cand, theme)
        if sc:
            out.append(sc)
    out.sort(key=lambda r: (-r["score"], r["handle"]))
    return out[:k]


def browse(profile, theme=None, limit=50):
    """All people-profiles, optionally filtered by theme. Contact never included."""
    rows = []
    for cand in store.all_profiles("person"):
        if cand["id"] == profile["id"]:
            continue
        overcame = [t.lower() for t in (cand.get("overcame") or [])]
        fighting = [t.lower() for t in (cand.get("fighting") or [])]
        if theme and theme.lower() not in overcame + fighting:
            continue
        rows.append({
            "id": cand["id"], "handle": cand["handle"], "city": cand.get("city"),
            "tradition": cand.get("tradition"), "bio": cand.get("bio"),
            "overcame": cand.get("overcame") or [], "fighting": cand.get("fighting") or [],
            "open_to_contact": bool(cand.get("open_to_contact")),
            "synthetic": bool(cand.get("synthetic")),
        })
    return rows[:limit]


def churches(city=None, tradition=None, limit=25):
    """Self-registered church profiles. Heard never invents a congregation.

    Every church entry in this system was created by someone filling in the
    church sign-up form. Demo entries are flagged `synthetic` and labelled as
    such in the interface.
    """
    rows = []
    for c in store.all_profiles("church"):
        if city and (c.get("city") or "").lower() != city.lower():
            continue
        if tradition and tradition not in _trads(c):
            continue
        rows.append({
            "id": c["id"], "handle": c["handle"], "city": c.get("city"),
            "tradition": c.get("tradition"), "bio": c.get("bio"),
            "service_times": c.get("service_times"), "address": c.get("address"),
            "website": c.get("website"), "synthetic": bool(c.get("synthetic")),
        })
    return rows[:limit]
