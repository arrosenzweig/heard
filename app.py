"""Heard - web application.

  "I don't understand, but I want to understand. That's why I need you God."

Run:  python app.py      then open http://127.0.0.1:5000
"""
import json
import time

from flask import Flask, jsonify, render_template, request, session

import config
import llm
import store
from agents import beliefs as beliefs_agent
from agents import accountability, awareness, matcher, pipeline, retrieval, triage, verifier

app = Flask(__name__)
# Dev-only session secret. A deployed instance must set HEARD_SECRET.
app.secret_key = __import__("os").getenv("HEARD_SECRET", "dev-only-not-for-production")

MOTTO = "I don't understand, but I want to understand. That's why I need you God."


def _topics():
    with open(config.DATA_DIR / "topics.json", encoding="utf-8") as f:
        return json.load(f)


def _traditions():
    with open(config.DATA_DIR / "traditions.json", encoding="utf-8") as f:
        return json.load(f)


def me():
    pid = session.get("pid")
    return store.get_profile(pid) if pid else None


def require_profile():
    """Signed-in PERSON. Church accounts manage a listing; they do not use
    chat, check-ins, or fellowship."""
    p = me()
    if not p:
        return None, (jsonify({"error": "no_profile"}), 401)
    if p.get("kind") == "church":
        return None, (jsonify({"error": "church_account"}), 403)
    return p, None


# --------------------------------------------------------------------- pages
@app.route("/")
def index():
    return render_template("index.html", motto=MOTTO)


@app.route("/api/session/reset", methods=["POST"])
def session_reset():
    """Start over as a new user in this browser. The old profile stays in the
    database; this browser simply stops being signed in to it."""
    session.pop("pid", None)
    return jsonify({"ok": True})

# ------------------------------------------------------- installable app
# "Add to Home Screen" puts Heard on a phone or desktop. The SOS shortcut
# (long-press the icon on Android, or right-click it on desktop) opens straight
# into "I'm being tempted". On iPhone, the /?sos=1 link itself can be added to
# the home screen as its own one-tap SOS icon.
@app.route("/manifest.webmanifest")
def manifest():
    m = {
        "name": "Heard", "short_name": "Heard",
        "description": "A Christian companion for standing strong in what you believe.",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#f7f3ec", "theme_color": "#8a5a2b",
        "icons": [
            {"src": "/static/icons/heard-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/heard-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/heard-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [{
            "name": "I'm being tempted right now", "short_name": "I'm tempted",
            "description": "Verses now, then tell Heard what's going on",
            "url": "/?sos=1",
            "icons": [{"src": "/static/icons/sos-192.png", "sizes": "192x192", "type": "image/png"}],
        }],
    }
    return app.response_class(json.dumps(m), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    # Minimal on purpose: it makes Heard installable but caches nothing, so no
    # one's private conversations are ever stored by the browser cache.
    js = "self.addEventListener('install',e=>self.skipWaiting());"          "self.addEventListener('activate',e=>self.clients.claim());"          "self.addEventListener('fetch',()=>{});"
    return app.response_class(js, mimetype="application/javascript",
                              headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


# ----------------------------------------------------------------- bootstrap
@app.route("/api/bootstrap")
def bootstrap():
    return jsonify({
        "options": {"gender": GENDERS, "marital": MARITAL, "faith_years": FAITH_YEARS,
                    "min_age": MIN_AGE, "fellowship_age": FELLOWSHIP_AGE},
        "motto": MOTTO,
        "traditions": _traditions()["traditions"],
        "topics": _topics()["topics"],
        "own_words_option": _topics().get("own_words_option"),
        "themes": retrieval.themes(),
        "translations": retrieval.available_translations(),
        "profile": me(),
        "provider": config.PROVIDER,
        "model": config.MODEL,
        "price_verified": config.PRICE_VERIFIED,
    })


# ------------------------------------------------------------------- profile
@app.route("/api/profile", methods=["POST"])
def save_profile():
    d = request.get_json(force=True) or {}
    trads = d.get("tradition") or []
    if isinstance(trads, str):
        trads = [trads]
    seeker_ids = {t["id"] for t in _traditions()["traditions"] if t.get("seeker")}
    is_seeker = 1 if any(t in seeker_ids for t in trads) else 0

    existing = me()
    if existing and existing.get("kind") != "person":
        existing = None  # never turn a church account into a person
    pid = existing["id"] if existing else None
    pid = store.create_profile(
        id=pid,
        kind="person",
        handle=(d.get("handle") or "friend").strip()[:40],
        city=(d.get("city") or "").strip()[:60],
        tradition=json.dumps(trads),
        tradition_other=(d.get("tradition_other") or "").strip()[:120],
        is_seeker=is_seeker,
        beliefs=d.get("beliefs") or {},
        bio=(d.get("bio") or "").strip()[:600],
        overcame=d.get("overcame") or [],
        fighting=d.get("fighting") or [],
        open_to_contact=1 if d.get("open_to_contact") else 0,
        contact_method=(d.get("contact_method") or "").strip()[:20],
        contact_value=(d.get("contact_value") or "").strip()[:120],
        synthetic=0,
        checkin_hours=_hours(d.get("checkin_hours"), (existing or {}).get("checkin_hours")),
        custom_struggles=_custom(d.get("custom_struggles")),
        gender=d.get("gender") if d.get("gender") in GENDERS else None,
        age=_age(d.get("age")),
        marital=d.get("marital") if d.get("marital") in MARITAL else None,
        faith_years=d.get("faith_years") if d.get("faith_years") in FAITH_YEARS else None,
        onboarded=1,
    )
    session["pid"] = pid
    return jsonify({"ok": True, "profile": store.get_profile(pid)})


@app.route("/api/profile/tradition-parse", methods=["POST"])
def tradition_parse():
    """Profiles store tradition as JSON; normalise on read for the client."""
    p = me()
    return jsonify({"tradition": _as_list(p.get("tradition")) if p else []})


GENDERS = ("woman", "man", "prefer_not")
MARITAL = ("single", "dating", "engaged", "married", "separated", "divorced", "widowed", "prefer_not")
FAITH_YEARS = ("exploring", "lt1", "1_5", "5_10", "10_20", "20_plus", "prefer_not")
MIN_AGE = 13          # minimum age to use Heard at all
FELLOWSHIP_AGE = 18   # minimum age to appear in or use Fellowship


def _age(v):
    try:
        a = int(v)
    except (TypeError, ValueError):
        return None
    return a if 0 < a < 120 else None


def _hours(v, fallback=None):
    """Check-in interval: any whole number of days from 1 to 60, stored in hours."""
    try:
        h = int(v)
    except (TypeError, ValueError):
        h = int(fallback or 24)
    return max(24, min(24 * 60, (h // 24) * 24 or 24))


def _custom(items):
    out = []
    for cs in items or []:
        if not isinstance(cs, dict):
            continue
        text = " ".join(str(cs.get("text", "")).split())[:120]
        theme = cs.get("theme")
        if text and theme in retrieval.themes():
            out.append({"text": text, "theme": theme,
                        "kind": "overcame" if cs.get("kind") == "overcame" else "fighting"})
    return out[:10]


@app.route("/api/struggle/classify", methods=["POST"])
def struggle_classify():
    """Suggest the closest category for a struggle described in the user's own
    words. The user confirms or changes it. Triage also screens the text, so a
    crisis described here is caught like anywhere else."""
    d = request.get_json(force=True) or {}
    text = " ".join(str(d.get("text", "")).split())[:500]
    if len(text) < 3:
        return jsonify({"error": "empty"}), 400
    tr = llm.Trace(label="struggle-classify")
    t = triage.run(text, trace=tr)
    return jsonify({"theme": t["theme"], "severity": t["severity"],
                    "resources": t["resources"] if t["severity"] == "crisis" or t.get("safety_note") else [],
                    "themes": retrieval.themes(), "trace": tr.summary()})


def _filters():
    a = request.args
    f = {k: a.get(k) for k in ("gender", "marital", "tradition", "faith_years", "age_min", "age_max") if a.get(k)}
    return f or None


def _fellowship_gate(p):
    if matcher.fellowship_eligible(p):
        return None
    reason = "under_18" if p.get("age") else "age_required"
    msg = ("Fellowship is for adults, so it isn't available until you're 18. Talk and Check-ins are here for you."
           if reason == "under_18" else "Add your age in Settings to use Fellowship. It keeps minors from being connected with adults.")
    return jsonify({"suggestions": [], "matches": [], "people": [], "fighting": [], "gate": reason, "message": msg})


@app.route("/api/suggestions")
def suggestions():
    """Fellowship suggestions across everything the user is fighting, so the
    tab is useful the moment it opens."""
    p, err = require_profile()
    if err:
        return err
    gate = _fellowship_gate(p)
    if gate:
        return gate
    prof = dict(p)
    prof["tradition"] = _as_list(p.get("tradition"))
    labels = {t: t.replace("_", " ") for t in retrieval.themes()}
    for cs in p.get("custom_struggles") or []:
        if cs.get("kind") == "fighting":
            labels.setdefault(cs["theme"], cs["text"])
    best = {}
    for theme in matcher.themes_of(prof, "fighting"):
        for m in matcher.suggest(prof, theme, k=5, filters=_filters()):
            m["for_theme"] = theme
            m["for_label"] = labels.get(theme, theme.replace("_", " "))
            if m["id"] not in best or m["score"] > best[m["id"]]["score"]:
                best[m["id"]] = m
    ranked = sorted(best.values(), key=lambda r: (-r["score"], r["handle"]))
    return jsonify({"suggestions": ranked[:8], "fighting": matcher.themes_of(prof, "fighting")})


def _as_list(v):
    if isinstance(v, list):
        return v
    if not v:
        return []
    try:
        out = json.loads(v)
        return out if isinstance(out, list) else [out]
    except (json.JSONDecodeError, TypeError):
        return [v]


# ------------------------------------------------------- belief capture loop
@app.route("/api/beliefs/simplify", methods=["POST"])
def beliefs_simplify():
    """Step 1 of the approval loop: restate, then show back. Stores nothing."""
    d = request.get_json(force=True) or {}
    topic_id = d.get("topic_id")
    question = None
    for t in _topics()["topics"]:
        if t["id"] == topic_id:
            question = t["question"]
            break
    tr = llm.Trace(label="belief-simplify")
    out = beliefs_agent.simplify(d.get("text", ""), question=question, trace=tr)
    out["trace"] = tr.summary()
    return jsonify(out)


# --------------------------------------------------------------------- entry
@app.route("/api/entry", methods=["POST"])
def entry():
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400

    prof = dict(p)
    prof["tradition"] = _as_list(p.get("tradition"))
    history = store.entries_for(p["id"], limit=6)

    result, trace = pipeline.run(
        text, profile=prof, history=history,
        translation=d.get("translation"), label="entry")

    eid = store.add_entry(p["id"], text, result["triage"]["theme"],
                          result["triage"]["severity"], result["triage"]["intensity"])
    rid = store.save_run(p["id"], eid, "entry", trace)
    result["entry_id"] = eid
    result["run_id"] = rid
    result["trace"] = trace
    return jsonify(result)


# --------------------------------------------------------------- commitments
@app.route("/api/commitment", methods=["POST"])
def make_commitment():
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400
    cid = store.add_commitment(p["id"], d.get("entry_id"), text[:300],
                               due_minutes=int(d.get("due_minutes", 180)))
    return jsonify({"ok": True, "commitment_id": cid,
                    "commitments": store.commitments_for(p["id"], "open")})


@app.route("/api/commitments")
def list_commitments():
    p, err = require_profile()
    if err:
        return err
    return jsonify({
        "open": store.commitments_for(p["id"], "open"),
        "due": store.due_commitments(p["id"]),
        "outcomes": store.outcomes_for(p["id"]),
        "stats": store.outcome_stats(p["id"]),
        "now": time.time(),
    })


@app.route("/api/outcome", methods=["POST"])
def record_outcome():
    """The measurement loop: what actually happened after the commitment."""
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    result = d.get("result")
    if result not in ("held", "slipped", "partial", "no_answer"):
        return jsonify({"error": "bad_result"}), 400
    store.record_outcome(d.get("commitment_id"), p["id"], result,
                         (d.get("note") or "")[:500])
    return jsonify({"ok": True, "stats": store.outcome_stats(p["id"]),
                    "outcomes": store.outcomes_for(p["id"])})


# ---------------------------------------------------------------------- chat
@app.route("/api/chat", methods=["GET"])
def chat_history():
    p, err = require_profile()
    if err:
        return err
    return jsonify({"messages": store.chat_for(p["id"])})


@app.route("/api/chat", methods=["DELETE"])
def chat_clear():
    p, err = require_profile()
    if err:
        return err
    store.clear_chat(p["id"])
    return jsonify({"ok": True})


@app.route("/api/chat", methods=["POST"])
def chat_send():
    """One conversational turn. Triage runs on EVERY message, so a crisis
    disclosed ten messages into a conversation is caught as reliably as one
    disclosed in the first."""
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400

    prof = dict(p)
    prof["tradition"] = _as_list(p.get("tradition"))
    convo = store.chat_for(p["id"], limit=16)
    store.add_chat(p["id"], "user", text)

    result, trace = pipeline.run(text, profile=prof, history=store.entries_for(p["id"], limit=6),
                                 conversation=convo, label="sos" if d.get("sos") else "chat",
                                 sos=bool(d.get("sos")))
    eid = store.add_entry(p["id"], text, result["triage"]["theme"],
                          result["triage"]["severity"], result["triage"]["intensity"])
    rid = store.save_run(p["id"], eid, "chat", trace)

    res = result["response"]
    store.add_chat(p["id"], "assistant", res.get("reflection") or res.get("body") or "", meta={
        "mode": res.get("mode"), "tone": res.get("tone"), "headline": res.get("headline"),
        "verses": result.get("verses", []), "steps": res.get("steps", []),
        "commitment": res.get("commitment"), "prayer": res.get("prayer"),
        "human_nudge": res.get("human_nudge"), "resources": res.get("resources") or result["triage"].get("resources", []),
        "safety_note": result["triage"].get("safety_note"),
        "matches": result.get("matches", []), "theme": result["triage"]["theme"],
        "entry_id": eid, "run_id": rid,
        "verification": {k: (result.get("verification") or {}).get(k) for k in ("n_claimed", "n_kept", "n_removed")},
    })
    result["entry_id"], result["run_id"], result["trace"] = eid, rid, trace
    return jsonify(result)


# ----------------------------------------------------------------- feedback
@app.route("/api/feedback", methods=["GET", "POST"])
def feedback():
    """Three 1-5 ratings and an optional comment. A comment may appear in the
    project report only if the person ticked "OK to quote anonymously"."""
    p, err = require_profile()
    if err:
        return err
    if request.method == "GET":
        return jsonify({"given": store.has_feedback(p["id"])})
    d = request.get_json(force=True) or {}

    def rating(k):
        try:
            v = int(d.get(k))
        except (TypeError, ValueError):
            return None
        return v if 1 <= v <= 5 else None
    vals = [rating(k) for k in ("understood", "would_use", "loving")]
    if None in vals:
        return jsonify({"error": "ratings_required"}), 400
    store.add_feedback(p["id"], *vals, (d.get("comment") or "").strip()[:800], bool(d.get("quote_ok")))
    return jsonify({"ok": True})


# ---------------------------------------------------------------- awareness
AWARENESS_REFRESH_AFTER = 2   # new conversations/check-ins before it rebuilds itself


def _activity(basis):
    try:
        e, c = basis.split(":")[:2]
        return int(e) + int(c)
    except (AttributeError, ValueError):
        return 0


@app.route("/api/awareness", methods=["GET", "POST"])
def awareness_view():
    """The user's reflective profile. Rebuilt when enough new history has
    accumulated (or on request); otherwise served from cache at no cost."""
    p, err = require_profile()
    if err:
        return err
    prof = dict(p)
    prof["tradition"] = _as_list(p.get("tradition"))
    now_basis = awareness.basis(prof)
    old_basis, cached, created = store.get_awareness(p["id"])
    force = request.method == "POST"
    new_items = _activity(now_basis) - _activity(old_basis) if old_basis else None
    stale = cached is None or (new_items is not None and new_items >= AWARENESS_REFRESH_AFTER)         or (cached.get("starter") and new_items and new_items > 0)
    rebuilt = False
    if force or stale:
        tr = llm.Trace(label="awareness")
        cached = awareness.build(prof, previous=cached, trace=tr)
        store.save_awareness(p["id"], now_basis, cached)
        store.save_run(p["id"], None, "awareness", tr.summary())
        rebuilt, new_items = True, 0
    cached = dict(cached, trends=awareness.trends(p["id"]), rebuilt=rebuilt,
                  new_since=new_items or 0, refresh_after=AWARENESS_REFRESH_AFTER)
    return jsonify(cached)


# ---------------------------------------------------------------------- SOS
SOS_VERSE = "I Corinthians 10:13"   # "God is faithful... he will also provide a way of escape"


@app.route("/api/sos")
def sos():
    """Everything the SOS screen needs, instantly and with no model call:
    real verse text, the people this user can actually reach, and real help
    lines. The model is only involved if they choose to say what's happening."""
    p, err = require_profile()
    if err:
        return err
    trads = _as_list(p.get("tradition"))
    code = retrieval.translation_for(trads)
    verses = []
    v = retrieval.by_ref(SOS_VERSE, code)
    if v:
        verses.append(v)
    # One verse for what they're fighting, from the curated anchors.
    with open(config.DATA_DIR / "verses.json", encoding="utf-8") as f:
        anchors = json.load(f)["verses"]
    for theme in matcher.themes_of(p, "fighting"):
        for a in anchors:
            if theme in a["themes"]:
                w = retrieval.by_ref(a["ref"], code)
                if w and w["ref"] != (v or {}).get("ref"):
                    verses.append(dict(w, for_theme=theme))
                    break
        if len(verses) > 1:
            break
    # People they can reach right now: accepted connections who share contact.
    contacts = []
    for c in store.connections_for(p["id"]):
        if c["status"] != "accepted":
            continue
        other = store.get_profile(c["to_id"] if c["from_id"] == p["id"] else c["from_id"])
        if other and other.get("open_to_contact") and other.get("contact_value")                 and matcher.fellowship_eligible(other) and matcher.fellowship_eligible(p):
            contacts.append({"handle": other["handle"], "method": other.get("contact_method") or "text",
                             "value": other["contact_value"], "theme": c.get("theme"),
                             "synthetic": bool(other.get("synthetic"))})
    return jsonify({"verses": verses, "contacts": contacts,
                    "fellowship_ok": matcher.fellowship_eligible(p),
                    "resources": [{"name": "988 Suicide & Crisis Lifeline", "contact": "Call or text 988"},
                                  {"name": "Emergency services", "contact": "Call 911"}]})


# ------------------------------------------------------------ check-ins
@app.route("/api/checkins")
def checkins():
    p, err = require_profile()
    if err:
        return err
    items = accountability.due(p)
    for it in items:
        it["questions"] = accountability.questions(it["theme"])
        it["series"] = accountability.series(p["id"], it["theme"])
    return jsonify({"struggles": items, "hours": p.get("checkin_hours") or 24,
                    "stats": store.checkin_stats(p["id"]),
                    "commitments": store.commitments_for(p["id"], "open"),
                    "due_commitments": store.due_commitments(p["id"]),
                    "outcomes": store.outcomes_for(p["id"]),
                    "outcome_stats": store.outcome_stats(p["id"])})


@app.route("/api/checkin/start", methods=["POST"])
def checkin_start():
    """First message of a conversational check-in."""
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    tr = llm.Trace(label="checkin-start")
    out = accountability.opener(p, d.get("theme", ""), trace=tr)
    s_ = tr.summary()
    if s_["n_calls"]:
        store.save_run(p["id"], None, "checkin", s_)
    out["trace"] = s_
    return jsonify(out)


@app.route("/api/checkin/chat", methods=["POST"])
def checkin_chat():
    """One turn of a conversational check-in. Every reply is triaged."""
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    msgs = [m for m in (d.get("messages") or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    tr = llm.Trace(label="checkin-chat")
    out = accountability.chat_turn(p, d.get("theme", ""), msgs, trace=tr)
    s_ = tr.summary()
    store.save_run(p["id"], None, "checkin", s_)
    out["trace"] = s_
    return jsonify(out)


@app.route("/api/checkin", methods=["POST"])
def checkin():
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    tr = llm.Trace(label="checkin")
    out = accountability.assess(p, d.get("theme"), d.get("pull", 3), d.get("acted"),
                                d.get("direction"), d.get("note", ""), trace=tr,
                                given_reply=(d.get("closing") or None),
                                screened=bool(d.get("conversation")))
    s = tr.summary()
    if s["n_calls"]:
        store.save_run(p["id"], None, "checkin", s)
    out["trace"] = s
    return jsonify(out)


# -------------------------------------------------------------- people
@app.route("/api/matches")
def matches():
    p, err = require_profile()
    if err:
        return err
    prof = dict(p)
    prof["tradition"] = _as_list(p.get("tradition"))
    gate = _fellowship_gate(p)
    if gate:
        return gate
    theme = request.args.get("theme")
    return jsonify({"matches": matcher.suggest(prof, theme, k=8, filters=_filters())})


@app.route("/api/browse")
def browse():
    p, err = require_profile()
    if err:
        return err
    gate = _fellowship_gate(p)
    if gate:
        return gate
    return jsonify({"people": matcher.browse(p, request.args.get("theme"), filters=_filters())})


@app.route("/api/connect", methods=["POST"])
def connect():
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    target = store.get_profile(d.get("to_id")) if d.get("to_id") else None
    # Both people must be adults: the gate holds even for a direct request.
    if not target or target.get("kind") != "person" or not matcher.fellowship_eligible(p)             or not matcher.fellowship_eligible(target):
        return jsonify({"error": "not_allowed"}), 403
    cid = store.request_connection(p["id"], d.get("to_id"), d.get("theme"))
    # Demo profiles are not real people and can never press Accept. So that the
    # whole flow (request -> accept -> call or text) can be seen, a demo profile
    # accepts at once, and the interface says so. Real people always decide.
    demo = bool(target.get("synthetic"))
    if demo:
        store.decide_connection(cid, "accepted")
    return jsonify({"ok": True, "connection_id": cid, "demo_auto_accepted": demo})


@app.route("/api/connect/<cid>/decide", methods=["POST"])
def decide(cid):
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    status = d.get("status")
    if status not in ("accepted", "declined", "blocked"):
        return jsonify({"error": "bad_status"}), 400
    c = store.connection(cid)
    if not c or c["to_id"] != p["id"]:
        return jsonify({"error": "not_yours"}), 403
    store.decide_connection(cid, status)
    return jsonify({"ok": True})


@app.route("/api/connections")
def connections():
    p, err = require_profile()
    if err:
        return err
    rows = []
    for c in store.connections_for(p["id"]):
        other = store.get_profile(c["to_id"] if c["from_id"] == p["id"] else c["from_id"])
        rows.append(dict(c, other_handle=(other or {}).get("handle", "?"),
                         other_synthetic=bool((other or {}).get("synthetic")),
                         direction="sent" if c["from_id"] == p["id"] else "received"))
    return jsonify({"connections": rows})


@app.route("/api/demo/connections", methods=["POST"])
def demo_connections():
    """For demos only, and only when the user asks: two accepted connections
    with demo people (so Call/Text and SOS "reach a person" can be shown) and
    one incoming request to accept or decline. Every one is a labelled demo
    profile; nothing here creates a connection with a real person."""
    p, err = require_profile()
    if err:
        return err
    if not matcher.fellowship_eligible(p):
        return jsonify({"error": "not_eligible"}), 403
    prof = dict(p)
    prof["tradition"] = _as_list(p.get("tradition"))
    existing = {c["to_id"] if c["from_id"] == p["id"] else c["from_id"] for c in store.connections_for(p["id"])}
    demos = [d for d in store.all_profiles("person")
             if d.get("synthetic") and d.get("open_to_contact") and matcher.fellowship_eligible(d)
             and d["id"] not in existing and d["id"] != p["id"]]
    ranked = []
    for theme in matcher.themes_of(prof, "fighting") or ["temptation"]:
        for m in matcher.suggest(prof, theme, k=5):
            if m["id"] in {d["id"] for d in demos} and m["id"] not in [r[0] for r in ranked]:
                ranked.append((m["id"], theme))
    for d in demos:  # top up if their struggles matched fewer than three
        if d["id"] not in [r[0] for r in ranked]:
            ranked.append((d["id"], (d.get("overcame") or d.get("fighting") or ["temptation"])[0]))
    made = []
    for pid, theme in ranked[:2]:
        cid = store.request_connection(p["id"], pid, theme)
        store.decide_connection(cid, "accepted")
        made.append(pid)
    if len(ranked) > 2:
        pid, theme = ranked[2]
        store.request_connection(pid, p["id"], theme)   # incoming, left pending
        made.append(pid)
    return jsonify({"ok": True, "created": len(made)})


@app.route("/api/contact/<cid>")
def reveal_contact(cid):
    """Contact details are released ONLY after the request is accepted.

    Browsing a profile never exposes a phone number. This is the gate that
    keeps 'call or text them' from becoming a directory of vulnerable people.
    """
    p, err = require_profile()
    if err:
        return err
    c = store.connection(cid)
    if not c or p["id"] not in (c["from_id"], c["to_id"]):
        return jsonify({"error": "not_yours"}), 403
    if c["status"] != "accepted":
        return jsonify({"error": "not_accepted",
                        "message": "Contact details unlock once both of you agree."}), 403
    other = store.get_profile(c["to_id"] if c["from_id"] == p["id"] else c["from_id"])
    if not other or not other.get("open_to_contact"):
        return jsonify({"error": "not_shared"}), 403
    return jsonify({"handle": other["handle"],
                    "method": other.get("contact_method"),
                    "value": other.get("contact_value"),
                    "synthetic": bool(other.get("synthetic"))})


# ------------------------------------------------------------------ churches
@app.route("/api/churches")
def churches():
    return jsonify({"churches": matcher.churches(request.args.get("city"),
                                                 request.args.get("tradition") or None,
                                                 profile=(me() if (me() or {}).get("kind") == "person" else None)),
                    "method": matcher.MATCH_METHOD})


@app.route("/api/church", methods=["POST"])
def save_church():
    """Create or update a church profile. Heard never invents a congregation.

    During setup a church chooses "I'm setting up a church"; that creates the
    church and signs this browser in as it, so the church can edit its own
    listing later. A signed-in church that saves again updates its listing.
    """
    d = request.get_json(force=True) or {}
    if not (d.get("handle") or "").strip():
        return jsonify({"error": "name_required"}), 400
    trads = d.get("tradition") or []
    if isinstance(trads, str):
        trads = [trads]
    current = me()
    cid = current["id"] if current and current.get("kind") == "church" else None
    cid = store.create_profile(
        id=cid,
        kind="church",
        handle=d["handle"].strip()[:120],
        city=(d.get("city") or "").strip()[:60],
        tradition=json.dumps(trads),
        bio=(d.get("bio") or "").strip()[:800],
        service_times=(d.get("service_times") or "").strip()[:200],
        address=(d.get("address") or "").strip()[:200],
        website=(d.get("website") or "").strip()[:200],
        beliefs={k: v for k, v in (d.get("beliefs") or {}).items()
                 if v in ("position_a", "position_b", "somewhere_between")},
        ministries=json.dumps([m for m in (d.get("ministries") or []) if m in retrieval.themes()]),
        seeker_friendly=1 if d.get("seeker_friendly") else 0,
        onboarded=1,
        synthetic=0,
    )
    if d.get("as_account") or cid == (current or {}).get("id"):
        session["pid"] = cid
    return jsonify({"ok": True, "id": cid, "profile": store.get_profile(cid)})


# ------------------------------------------------- evidence / inspection
@app.route("/api/verse")
def verse():
    """Direct lookup - used by the UI to prove a citation is real."""
    code = request.args.get("t", retrieval.DEFAULT_TRANSLATION)
    v = retrieval.by_ref(request.args.get("ref", ""), code)
    return jsonify({"verse": v, "found": v is not None})


@app.route("/api/stats")
def stats():
    runs = store.all_runs(500)
    tot_p = sum(r["trace"].get("prompt_tokens", 0) for r in runs)
    tot_c = sum(r["trace"].get("completion_tokens", 0) for r in runs)
    per_run = []
    for r in runs:
        t = r["trace"]
        per_run.append({"id": r["id"], "label": r["label"],
                        "total_tokens": t.get("total_tokens", 0),
                        "n_calls": t.get("n_calls", 0),
                        "wall_s": t.get("wall_s"), "created": r["created"]})
    n = len(runs) or 1
    return jsonify({
        "runs": len(runs),
        "prompt_tokens": tot_p,
        "completion_tokens": tot_c,
        "total_tokens": tot_p + tot_c,
        "avg_tokens_per_run": round((tot_p + tot_c) / n, 1),
        "usd_total": round(config.cost_usd(tot_p, tot_c), 6),
        "usd_per_run": round(config.cost_usd(tot_p, tot_c) / n, 6),
        "price_verified": config.PRICE_VERIFIED,
        "model": config.MODEL,
        "provider": config.PROVIDER,
        "outcomes": store.outcome_stats(),
        "per_run": per_run[:50],
    })


@app.route("/api/trace/<rid>")
def trace(rid):
    for r in store.all_runs(500):
        if r["id"] == rid:
            return jsonify(r)
    return jsonify({"error": "not_found"}), 404


def _seed_if_empty():
    """Make sure the labelled demo people and churches exist (fixed ids, so this
    is safe to run on every start and never duplicates them). Real profiles
    are never touched."""
    import seed_demo
    seed_demo.main()


if __name__ == "__main__":
    store.init()
    _seed_if_empty()
    print(f"Heard - {MOTTO}")
    print(f"provider={config.PROVIDER} model={config.MODEL}")
    print("http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
