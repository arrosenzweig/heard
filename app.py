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
from agents import accountability, matcher, pipeline, retrieval, triage, verifier

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

# ----------------------------------------------------------------- bootstrap
@app.route("/api/bootstrap")
def bootstrap():
    return jsonify({
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
        onboarded=1,
    )
    session["pid"] = pid
    return jsonify({"ok": True, "profile": store.get_profile(pid)})


@app.route("/api/profile/tradition-parse", methods=["POST"])
def tradition_parse():
    """Profiles store tradition as JSON; normalise on read for the client."""
    p = me()
    return jsonify({"tradition": _as_list(p.get("tradition")) if p else []})


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


@app.route("/api/suggestions")
def suggestions():
    """Fellowship suggestions across everything the user is fighting, so the
    tab is useful the moment it opens."""
    p, err = require_profile()
    if err:
        return err
    prof = dict(p)
    prof["tradition"] = _as_list(p.get("tradition"))
    labels = {t: t.replace("_", " ") for t in retrieval.themes()}
    for cs in p.get("custom_struggles") or []:
        if cs.get("kind") == "fighting":
            labels.setdefault(cs["theme"], cs["text"])
    best = {}
    for theme in matcher.themes_of(prof, "fighting"):
        for m in matcher.suggest(prof, theme, k=5):
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
                                 conversation=convo, label="chat")
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


@app.route("/api/checkin", methods=["POST"])
def checkin():
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    tr = llm.Trace(label="checkin")
    out = accountability.assess(p, d.get("theme"), d.get("pull", 3), d.get("acted"),
                                d.get("direction"), d.get("note", ""), trace=tr)
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
    theme = request.args.get("theme")
    return jsonify({"matches": matcher.suggest(prof, theme, k=5)})


@app.route("/api/browse")
def browse():
    p, err = require_profile()
    if err:
        return err
    return jsonify({"people": matcher.browse(p, request.args.get("theme"))})


@app.route("/api/connect", methods=["POST"])
def connect():
    p, err = require_profile()
    if err:
        return err
    d = request.get_json(force=True) or {}
    cid = store.request_connection(p["id"], d.get("to_id"), d.get("theme"))
    return jsonify({"ok": True, "connection_id": cid})


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
                         direction="sent" if c["from_id"] == p["id"] else "received"))
    return jsonify({"connections": rows})


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
    """A fresh clone has an empty database. Load the labelled demo profiles so
    the People and Churches tabs show something. Never runs twice."""
    if not store.all_profiles("person") and not store.all_profiles("church"):
        import seed_demo
        seed_demo.main()


if __name__ == "__main__":
    store.init()
    _seed_if_empty()
    print(f"Heard - {MOTTO}")
    print(f"provider={config.PROVIDER} model={config.MODEL}")
    print("http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
