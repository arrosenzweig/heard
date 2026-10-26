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


def themes_of(p, kind):
    """Category list for 'fighting' or 'overcame', including the user's own
    described struggles, through the category they confirmed for each."""
    out = [t.lower() for t in (p.get(kind) or [])]
    for cs in p.get("custom_struggles") or []:
        if isinstance(cs, dict) and cs.get("kind") == kind and cs.get("theme"):
            out.append(cs["theme"].lower())
    return out


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

    overcame = themes_of(candidate, "overcame")
    fighting = themes_of(candidate, "fighting")
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


def _list(v):
    if isinstance(v, list):
        return v
    if not v:
        return []
    try:
        x = json.loads(v)
        return x if isinstance(x, list) else [x]
    except (json.JSONDecodeError, TypeError):
        return [v]


# Church match weights. A component only counts when there is data for it on
# both sides, and the score is earned / possible, so a church is never marked
# down for a question the user did not answer.
CW_TRADITION = 40      # shares a tradition (or welcomes seekers, for a seeker)
CW_BELIEFS = 35        # agreement on contested questions both answered
CW_MINISTRY = 25       # has support for a struggle the user is fighting
NEUTRAL = {"prefer_not_to_say", "still_discerning", None, ""}

MATCH_METHOD = (
    "Match % = points earned / points possible. Tradition: 40 if the church shares one of your "
    "traditions (or, if you are exploring, welcomes people exploring faith). Beliefs: up to 35, by the "
    "share of contested questions you both answered where you agree; answers in your own words are not "
    "compared. Support: 25 if the church lists support for a struggle you are fighting. A part counts "
    "only when both sides have something to compare.")


def church_match(profile, church):
    """Score one church for one user. Deterministic; no model call."""
    if not profile:
        return None
    earned, possible, reasons, notes = 0.0, 0.0, [], []
    labels = lambda xs: ", ".join(x.replace("_", " ") for x in xs)

    # Tradition
    ut = _trads(profile) - {"other"}
    ct = _trads(church)
    seeker = bool(profile.get("is_seeker")) or bool(ut & {"exploring", "unsure"})
    if seeker:
        possible += CW_TRADITION
        if church.get("seeker_friendly"):
            earned += CW_TRADITION
            reasons.append("Welcomes people exploring faith")
        else:
            notes.append("Hasn't said it welcomes people exploring faith")
    elif ut and ct:
        possible += CW_TRADITION
        shared = ut & ct
        if shared:
            earned += CW_TRADITION
            reasons.append("Shares your tradition (" + labels(sorted(shared)) + ")")
        else:
            notes.append("Different tradition")

    # Beliefs on contested questions
    ub, cb = profile.get("beliefs") or {}, church.get("beliefs") or {}
    agree = differ = 0
    for k, uv in ub.items():
        cv = cb.get(k)
        if isinstance(uv, dict) or isinstance(cv, dict) or uv in NEUTRAL or cv in NEUTRAL:
            continue
        if uv == cv:
            agree += 1
        else:
            differ += 1
    compared = agree + differ
    if compared:
        possible += CW_BELIEFS
        earned += CW_BELIEFS * agree / compared
        reasons.append(f"Agrees with you on {agree} of {compared} questions")
        if differ:
            notes.append(f"Differs on {differ}")

    # Support for what the user is fighting
    fighting = set(themes_of(profile, "fighting"))
    ministries = set(_list(church.get("ministries")))
    if fighting and ministries:
        possible += CW_MINISTRY
        overlap = fighting & ministries
        if overlap:
            earned += CW_MINISTRY
            reasons.append("Has support for " + labels(sorted(overlap)))

    pct = round(100 * earned / possible) if possible else None
    return {"pct": pct, "reasons": reasons, "notes": notes, "compared_beliefs": compared,
            "basis": possible}


def churches(city=None, tradition=None, limit=25, profile=None):
    """Self-registered church profiles, ranked by match when a profile is given.

    Heard never invents a congregation. Every entry was created through the
    church sign-up form. Demo entries are flagged `synthetic` and labelled as
    such in the interface.
    """
    rows = []
    for c in store.all_profiles("church"):
        if city and (c.get("city") or "").strip().lower() != city.strip().lower():
            continue
        if tradition and tradition not in _trads(c):
            continue
        m = church_match(profile, c)
        rows.append({
            "id": c["id"], "handle": c["handle"], "city": c.get("city"),
            "tradition": c.get("tradition"), "bio": c.get("bio"),
            "service_times": c.get("service_times"), "address": c.get("address"),
            "website": c.get("website"), "synthetic": bool(c.get("synthetic")),
            "ministries": _list(c.get("ministries")),
            "seeker_friendly": bool(c.get("seeker_friendly")),
            "match": m,
        })
    # Ties go to the church matched on MORE evidence: 100% on three measures
    # beats 100% on two.
    rows.sort(key=lambda r: (-((r["match"] or {}).get("pct") or -1),
                             -((r["match"] or {}).get("basis") or 0)))
    if rows and rows[0]["match"] and (rows[0]["match"]["pct"] or 0) >= 60:
        rows[0]["best"] = True
    return rows[:limit]
