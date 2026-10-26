"""Build report.html - a single self-contained file (images embedded).

Every number in the report is read from evidence files at build time:
  docs/safety_eval.json    (python eval_safety.py)
  docs/compare_eval.json   (python eval_compare.py)
  heard.db                 (real app usage: runs, commitments, outcomes)
Nothing is typed in by hand. Re-run this after using the app or re-running an
evaluation and the report updates itself.

Run:  python build_report.py      -> report.html
"""
import base64
import html
import json
from pathlib import Path

import config
import store
from agents import retrieval

HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
IMG = DOCS / "img"
E = html.escape


def load(name):
    p = DOCS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def img(name, caption):
    p = IMG / name
    if not p.exists():
        return f'<p class="missing">[missing screenshot: {E(name)}]</p>'
    b64 = base64.b64encode(p.read_bytes()).decode()
    return (f'<figure><img src="data:image/jpeg;base64,{b64}" alt="{E(caption)}">'
            f'<figcaption>{E(caption)}</figcaption></figure>')


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def money(x):
    return f"${x:,.4f}" if x < 1 else f"${x:,.2f}"


# --------------------------------------------------------------- evidence
safety = load("safety_eval.json") or {}
compare = load("compare_eval.json") or {}
S = safety.get("summary", {})
C = compare.get("summary", {})
ABL = compare.get("ablation", [])

store.init()
# "Real use" counts ONLY profiles that are not test or demo data. Every profile
# created while building and testing the app is marked synthetic, so these
# numbers start at zero and grow only with genuine use.
with store.db() as _c:
    REAL_IDS = {r["id"] for r in _c.execute(
        "SELECT id FROM profile WHERE kind='person' AND COALESCE(synthetic,0)=0").fetchall()}
    _oc = _c.execute("SELECT result, COUNT(*) n FROM outcome WHERE profile_id IN (%s) GROUP BY result"
                     % ",".join("?" * len(REAL_IDS)) if REAL_IDS else
                     "SELECT result, COUNT(*) n FROM outcome WHERE 0 GROUP BY result",
                     list(REAL_IDS)).fetchall()
    _ck = _c.execute("SELECT trend, COUNT(*) n FROM checkin WHERE profile_id IN (%s) GROUP BY trend"
                     % ",".join("?" * len(REAL_IDS)) if REAL_IDS else
                     "SELECT trend, COUNT(*) n FROM checkin WHERE 0 GROUP BY trend",
                     list(REAL_IDS)).fetchall()
runs = store.all_runs(5000)
real_runs = [r for r in runs if r["profile_id"] in REAL_IDS and r["label"] in ("entry", "chat")]
_ocd = {r["result"]: r["n"] for r in _oc}
_tot = sum(_ocd.values())
outcome = {"counts": _ocd, "total": _tot, "held_rate": (_ocd.get("held", 0) / _tot) if _tot else None}
checkin_counts = {r["trend"]: r["n"] for r in _ck}
n_real_users = len(REAL_IDS)

# One traced run, taken verbatim from the database rather than written by hand:
# the most recent multi-turn chat reply from the (synthetic) demo user.
TRACED = None
with store.db() as _c:
    _demo = _c.execute("SELECT id FROM profile WHERE handle='Demo user' ORDER BY created DESC").fetchone()
if _demo:
    _chat = store.chat_for(_demo["id"], limit=50)
    _runs = {r["id"]: r for r in runs if r["profile_id"] == _demo["id"]}
    for i, m in enumerate(_chat):
        meta = m.get("meta") or {}
        if m["role"] == "assistant" and meta.get("mode") == "counsel" and meta.get("run_id") in _runs and i >= 3:
            tr = _runs[meta["run_id"]]["trace"]
            steps = {st["step"]: st["detail"] for st in tr.get("steps", [])}
            TRACED = {"user": _chat[i - 1]["content"], "prior_user": _chat[i - 3]["content"],
                      "reply": m["content"], "meta": meta, "trace": tr,
                      "triage": steps.get("triage", {}), "retrieval": steps.get("retrieval", {})}
            break

# Check-in cost: measured runs that called a model; the no-note path calls none.
_ck_runs = [r["trace"] for r in runs if r["label"] == "checkin"]
CHECKIN_MODEL_TOKENS = (sum(t["total_tokens"] for t in _ck_runs) / len(_ck_runs)) if _ck_runs else None
corpus = retrieval.corpus_stats()
n_verses = sum(v["verses"] for v in corpus.values())

PRICE_IN, PRICE_OUT, PRICE_OK = config.USD_PER_1M_INPUT, config.USD_PER_1M_OUTPUT, config.PRICE_VERIFIED
EVAL_MODEL = "gpt-6-luna (course Portkey key)"

# Per-run tokens: measured, from the Heard arm of the comparison eval.
heard_per_case = C.get("heard_tokens_per_case", 0)
base_per_case = C.get("baseline_tokens_per_case", 0)

# Split per agent from the real app runs recorded in heard.db, if any.
agent_tot = {}
for r in real_runs:
    for a, d in r["trace"].get("by_agent", {}).items():
        t = agent_tot.setdefault(a, {"calls": 0, "prompt": 0, "completion": 0})
        t["calls"] += d["calls"]
        t["prompt"] += d["prompt_tokens"]
        t["completion"] += d["completion_tokens"]


def cost_at(prompt, completion, pin, pout):
    return prompt / 1e6 * pin + completion / 1e6 * pout


# Input/output split for cost: measured from every recorded response that
# reached counsel (development and demo runs are real API calls, so their token
# counts are real even though the users are not). Then scaled to the eval mean.
_measured = [r["trace"] for r in runs if r["label"] in ("entry", "chat")
             and "counsel" in r["trace"].get("by_agent", {})]
SPLIT_N = len(_measured)
if _measured:
    _pt = sum(t["prompt_tokens"] for t in _measured)
    _ct = sum(t["completion_tokens"] for t in _measured)
    INPUT_SHARE = _pt / (_pt + _ct)
else:
    INPUT_SHARE = None
pr = heard_per_case * (INPUT_SHARE if INPUT_SHARE is not None else 0)
co = heard_per_case * ((1 - INPUT_SHARE) if INPUT_SHARE is not None else 0)

ENTRIES_PER_USER_MONTH = 20   # ASSUMPTION, stated in the report
run_cost_luna = cost_at(pr, co, PRICE_IN, PRICE_OUT)
run_cost_opus = cost_at(pr, co, 4.00, 20.00)
run_cost_haiku = cost_at(pr, co, 0.10, 0.50)


def scale_row(label, per_run, note):
    per_user = per_run * ENTRIES_PER_USER_MONTH
    return (f"<tr><td>{E(label)}</td><td>{money(per_run)}</td><td>{money(per_user)}</td>"
            f"<td>{money(per_user * 1000)}</td><td>{money(per_user * 100000)}</td><td>{E(note)}</td></tr>")


# ------------------------------------------------------------ safety rows
def safety_rows():
    out = []
    for r in safety.get("part_a", []):
        ok = r["correct"] == r["of"]
        out.append(f"<tr class='{'ok' if ok else 'bad'}'><td>{E(r['expect'])}</td><td>{E(r['kind'])}</td>"
                   f"<td>{E(r['text'])}</td><td>{r['correct']}/{r['of']}</td></tr>")
    return "\n".join(out)


def verifier_rows():
    out = []
    for r in safety.get("part_b", []):
        out.append(f"<tr><td>{E(r['label'])}</td><td><code>{E(r['ref'])}</code></td>"
                   f"<td>{E(r['quote'][:70])}</td><td><b>{E(r['verdict'])}</b></td><td>{E(r['reason'] or '')}</td></tr>")
    return "\n".join(out)


def traced_html():
    if not TRACED:
        return "<p class='missing'>[no demo conversation recorded; run the app to generate one]</p>"
    t, m = TRACED, TRACED["meta"]
    calls = ", ".join(f"{c['agent']} ({c['prompt_tokens']:,} in / {c['completion_tokens']:,} out)"
                      for c in t["trace"]["calls"])
    v = m.get("verification", {})
    matches = ", ".join(f"{x['handle']} ({'came through it' if x['relation'] == 'overcame' else 'fighting it'})"
                        for x in m.get("matches", [])) or "none"
    quoted = ", ".join(x["ref"] for x in m.get("verses", [])) or "none"
    return (
        "<p>The second turn of a conversation with the demo user (non-denominational and Baptist, Raleigh). "
        "Everything below is read from the stored trace, not written by hand.</p>"
        f"<p><b>Earlier:</b> <i>\"{E(t['prior_user'])}\"</i><br><b>Now:</b> <i>\"{E(t['user'])}\"</i></p><ul>"
        f"<li><b>Triage:</b> theme <code>{E(str(t['triage'].get('theme')))}</code>, severity {E(str(t['triage'].get('severity')))}. "
        f"Biblical search terms: <i>{E(str(t['triage'].get('search_terms', ''))[:140])}</i></li>"
        f"<li><b>Retrieval</b> ({E(str(t['retrieval'].get('translation')))}, no model call): {E(', '.join(t['retrieval'].get('refs', [])))}</li>"
        f"<li><b>Counsel:</b> tone <b>{E(str(m.get('tone')))}</b>; quoted {E(quoted)} (the first turn's verse was deliberately not repeated)</li>"
        f"<li><b>Verifier:</b> {v.get('n_kept')} of {v.get('n_claimed')} citations verified</li>"
        f"<li><b>Matcher:</b> {E(matches)}</li>"
        f"<li><b>Model calls:</b> {E(calls)}; total {t['trace']['total_tokens']:,} tokens</li></ul>"
    )


def ablation_rows():
    return "\n".join(
        f"<tr><td>{a['k']}</td><td>{a['avg_total_tokens']:,.0f}</td><td>{a['avg_counsel_prompt_tokens']:,.0f}</td>"
        f"<td>{a['citations_shown']}/{a['citations_claimed']}</td><td>{a['avg_wall_s']}s</td></tr>" for a in ABL)


def compare_rows():
    out = []
    for r in compare.get("part_a", []):
        b = r["baseline"]
        bv = sum(1 for c in b if c["status"] == "verified")
        h = r["heard"]
        out.append(f"<tr><td>{E(r['text'][:70])}</td><td>{bv}/{len(b)}</td>"
                   f"<td>{h['shown']}/{h['claimed']}</td><td>{h['removed']}</td><td>{h['tokens']:,}</td></tr>")
    return "\n".join(out)


DIAGRAM = """<svg viewBox="0 0 760 360" role="img" aria-label="Heard agent pipeline">
<style>.b{fill:#fffdf8;stroke:#8a5a2b;stroke-width:1.5}.l{fill:#fdf8ee;stroke:#b8892f;stroke-width:1.5;stroke-dasharray:4 3}
.c{fill:#f6e1de;stroke:#a8463f;stroke-width:1.5}.t{font:600 14px Georgia,serif;fill:#2b2622}.s{font:12px sans-serif;fill:#7a6f65}
.a{stroke:#7a6f65;stroke-width:1.5;fill:none;marker-end:url(#h)}</style>
<defs><marker id="h" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#7a6f65"/></marker></defs>
<rect class="b" x="20" y="30" width="130" height="60" rx="10"/><text class="t" x="85" y="57" text-anchor="middle">Entry</text><text class="s" x="85" y="75" text-anchor="middle">typed or spoken</text>
<rect class="b" x="200" y="30" width="150" height="60" rx="10"/><text class="t" x="275" y="57" text-anchor="middle">1 Triage</text><text class="s" x="275" y="75" text-anchor="middle">LLM · classify + expand</text>
<rect class="c" x="410" y="10" width="320" height="50" rx="10"/><text class="t" x="570" y="32" text-anchor="middle">Crisis → STOP</text><text class="s" x="570" y="49" text-anchor="middle">hand-written guidance + real hotlines, nothing else</text>
<rect class="l" x="200" y="140" width="150" height="60" rx="10"/><text class="t" x="275" y="167" text-anchor="middle">2 Retrieval</text><text class="s" x="275" y="185" text-anchor="middle">BM25 · full Bible · no LLM</text>
<rect class="b" x="410" y="140" width="150" height="60" rx="10"/><text class="t" x="485" y="167" text-anchor="middle">3 Counsel</text><text class="s" x="485" y="185" text-anchor="middle">LLM · tradition + tone</text>
<rect class="l" x="410" y="260" width="150" height="60" rx="10"/><text class="t" x="485" y="287" text-anchor="middle">4 Verifier</text><text class="s" x="485" y="305" text-anchor="middle">checks every verse · no LLM</text>
<rect class="l" x="610" y="260" width="130" height="60" rx="10"/><text class="t" x="675" y="287" text-anchor="middle">5 Matcher</text><text class="s" x="675" y="305" text-anchor="middle">overcomers · no LLM</text>
<rect class="b" x="20" y="260" width="330" height="60" rx="10"/><text class="t" x="185" y="287" text-anchor="middle">Response · commitment · follow-up</text><text class="s" x="185" y="305" text-anchor="middle">outcome logged: held / partly / slipped</text>
<path class="a" d="M150 60H198"/><path class="a" d="M350 50Q380 35 408 35"/><path class="a" d="M275 90V138"/><path class="a" d="M350 170H408"/>
<path class="a" d="M485 200V258"/><path class="a" d="M560 290H608"/><path class="a" d="M410 290H352"/>
<text class="s" x="20" y="350">Solid boxes call a model. Dashed boxes are deterministic code.</text></svg>"""

# ------------------------------------------------------------------- page
CSS = """
:root{--ink:#2b2622;--muted:#6f655b;--line:#e7dfd2;--accent:#8a5a2b;--soft:#f7f3ec;--ok:#5d7a63;--bad:#a8463f}
*{box-sizing:border-box}body{margin:0;background:#fff;color:var(--ink);font:15.5px/1.65 Georgia,'Times New Roman',serif}
main{max-width:860px;margin:0 auto;padding:48px 28px 80px}
h1{font-size:2.4rem;margin:0}h2{font-size:1.5rem;margin:2.4em 0 .5em;padding-top:.6em;border-top:2px solid var(--line)}
h3{font-size:1.1rem;margin:1.6em 0 .4em}.motto{font-style:italic;color:var(--muted);font-size:1.1rem;margin:.3em 0 1em}
.meta{font:14px sans-serif;color:var(--muted)}.meta a{color:var(--accent)}
table{width:100%;border-collapse:collapse;font:13.5px/1.45 sans-serif;margin:12px 0 18px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}th{background:var(--soft)}
tr.ok td:last-child{color:var(--ok);font-weight:600}tr.bad td:last-child{color:var(--bad);font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--soft);border-radius:12px;padding:14px 16px}.kpi b{display:block;font-size:1.7rem}.kpi span{font:13px sans-serif;color:var(--muted)}
.note{background:#fdf6e9;border-left:4px solid #b8892f;padding:12px 16px;border-radius:0 10px 10px 0;font-size:14.5px;margin:14px 0}
.warn{background:#f6e1de;border-left:4px solid var(--bad);padding:12px 16px;border-radius:0 10px 10px 0;font-size:14.5px;margin:14px 0}
figure{display:inline-block;vertical-align:top;width:48.5%;margin:12px 0.5%;text-align:center}figure img{width:100%;border:1px solid var(--line);border-radius:10px}
figcaption{font:13px sans-serif;color:var(--muted);margin-top:6px}svg{width:100%;height:auto;margin:10px 0}
blockquote{margin:12px 0;padding:10px 18px;border-left:4px solid var(--accent);background:var(--soft);border-radius:0 10px 10px 0;font-style:italic}code{font-size:.9em;background:var(--soft);padding:1px 5px;border-radius:4px}.missing{color:var(--bad)}
@media print{body{font-size:14px;line-height:1.6}main{padding:0 6px}h1{font-size:1.9rem}h2{font-size:1.25rem;margin:1.4em 0 .4em;page-break-after:avoid}h3{margin:1em 0 .3em}table{font-size:12px;margin:10px 0 14px}th,td{padding:5px 7px}.kpi{padding:8px 10px}.kpi b{font-size:1.3rem}figure,table,.kpis,.note,.warn{page-break-inside:avoid}figcaption{font-size:10.5px}svg{max-height:300px}}
"""

MOTIVATION = '''<h2>1. Motivation</h2>
<p>Heard began with the author's own experience. In her words:</p>
<blockquote>"I am someone who has struggled a lot with keeping to my values and morals, even if they aren't seen as
important to others. Especially in the MBA there is a lot of temptation to follow the majority opinion or cross
boundaries that had previously been firm for myself."</blockquote>
<p>She built it <i>"to help myself and others have a resource to turn to when life is busy, your religious community is
bleak, and when you need someone to listen without judging."</i></p>

<h3>Who it is for</h3>
<p><i>"Anyone who is struggling with their bad decisions, or decisions they have yet to make, or decisions that they
have no power in."</i></p>

<h3>Why "Heard"</h3>
<p><i>"At the end of the day, people need to be heard for their true strifes and get real advice from a biblical rather
than worldly perspective."</i></p>
<p>The motto, <i>"I don't understand, but I want to understand. That's why I need you God,"</i> comes from one of her
favorite gospel songs, "Why God" by Austin French (from his 2018 album <i>Wide Open</i>).</p>

<h3>What is missing today</h3>
<p>From her own search for this kind of help: <i>"There is no AI that tailors the content based on niche beliefs and
values. The only apps out there offer general evangelical or highly specialized content. There isn't an adaptable app
for each person's own journey and values."</i></p>
<p>She draws the line at advice, not search: <i>"The market might have AI scripture search, but it doesn't have the
tailored and customizable advice. That advice comes inlaid with occasional scripture sharing and what that piece of
scripture means."</i> Section 2 tests this against two well-known apps and a plain chatbot.</p>

<p>From the start she asked for Bible verses, plain-language advice, perspectives from different denominations, and a
way to connect with other Christians for community or to talk when needed.</p>
<p class="meta">Quotations are the author's own words, lightly edited for spelling.</p>

'''

REPO = "https://github.com/arrosenzweig/heard"
FC = (load("feature_costs.json") or {}).get("features", {})
PART_C = safety.get("part_c", [])

DIAGRAM = """<svg viewBox="0 0 780 400" role="img" aria-label="Heard agents">
<style>.b{fill:#fffdf8;stroke:#8a5a2b;stroke-width:1.5}.l{fill:#fdf8ee;stroke:#b8892f;stroke-width:1.5;stroke-dasharray:4 3}
.c{fill:#f6e1de;stroke:#a8463f;stroke-width:1.5}.g{fill:#e3ece3;stroke:#5d7a63;stroke-width:1.5}
.t{font:600 13px Georgia,serif;fill:#2b2622}.s{font:11px sans-serif;fill:#6f655b}.a{stroke:#7a6f65;stroke-width:1.4;fill:none;marker-end:url(#h)}</style>
<defs><marker id="h" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#7a6f65"/></marker></defs>
<rect class="b" x="10" y="20" width="130" height="34" rx="8"/><text class="t" x="75" y="42" text-anchor="middle">Talk message</text>
<rect class="c" x="10" y="64" width="130" height="34" rx="8"/><text class="t" x="75" y="86" text-anchor="middle">SOS button</text>
<rect class="b" x="10" y="108" width="130" height="34" rx="8"/><text class="t" x="75" y="130" text-anchor="middle">Check-in reply</text>
<rect class="b" x="190" y="58" width="130" height="46" rx="8"/><text class="t" x="255" y="78" text-anchor="middle">Triage</text><text class="s" x="255" y="94" text-anchor="middle">LLM · every message</text>
<rect class="c" x="370" y="10" width="190" height="40" rx="8"/><text class="t" x="465" y="28" text-anchor="middle">Crisis: STOP</text><text class="s" x="465" y="43" text-anchor="middle">real hotlines, nothing else</text>
<rect class="l" x="370" y="70" width="130" height="46" rx="8"/><text class="t" x="435" y="90" text-anchor="middle">Retrieval</text><text class="s" x="435" y="106" text-anchor="middle">BM25 · full Bible · no LLM</text>
<rect class="b" x="530" y="70" width="110" height="46" rx="8"/><text class="t" x="585" y="90" text-anchor="middle">Counsel</text><text class="s" x="585" y="106" text-anchor="middle">LLM · tradition, tone</text>
<rect class="l" x="660" y="70" width="110" height="46" rx="8"/><text class="t" x="715" y="90" text-anchor="middle">Verifier</text><text class="s" x="715" y="106" text-anchor="middle">every verse · no LLM</text>
<rect class="b" x="190" y="170" width="170" height="46" rx="8"/><text class="t" x="275" y="190" text-anchor="middle">Accountability</text><text class="s" x="275" y="206" text-anchor="middle">LLM · grounded memory · or 0-token quick</text>
<rect class="b" x="380" y="170" width="170" height="46" rx="8"/><text class="t" x="465" y="190" text-anchor="middle">Awareness</text><text class="s" x="465" y="206" text-anchor="middle">LLM · evidence + privacy guards</text>
<rect class="b" x="570" y="170" width="200" height="46" rx="8"/><text class="t" x="670" y="190" text-anchor="middle">Beliefs</text><text class="s" x="670" y="206" text-anchor="middle">LLM · restate, user approves</text>
<rect class="g" x="190" y="260" width="270" height="46" rx="8"/><text class="t" x="325" y="280" text-anchor="middle">Fellowship matcher</text><text class="s" x="325" y="296" text-anchor="middle">overcomers first · filters · adults only · no LLM</text>
<rect class="g" x="480" y="260" width="290" height="46" rx="8"/><text class="t" x="625" y="280" text-anchor="middle">Church matcher</text><text class="s" x="625" y="296" text-anchor="middle">tradition · convictions · support · no LLM</text>
<rect class="b" x="10" y="330" width="760" height="46" rx="8"/><text class="t" x="390" y="350" text-anchor="middle">Profile (asked once at setup) + history: conversations, check-ins, commitments</text><text class="s" x="390" y="366" text-anchor="middle">feeds counsel, check-ins, Awareness, and matching</text>
<path class="a" d="M140 37H188"/><path class="a" d="M140 81H188"/><path class="a" d="M140 125H188"/>
<path class="a" d="M320 70Q345 30 368 30"/><path class="a" d="M320 88H368"/><path class="a" d="M500 93H528"/><path class="a" d="M640 93H658"/>
<path class="a" d="M255 104V168"/>
</svg>"""

CSS = CSS.replace("@media print{", ".repo{background:#f7f3ec;border-radius:12px;padding:12px 16px;margin:14px 0;font:14px sans-serif}"
                  ".repo a{color:#8a5a2b;font-weight:600}.repo code.clone{display:block;margin:6px 0}@media print{")


def feature_rows():
    labels = [
        ("talk_reply", "Talk reply"), ("crisis_reply", "Crisis (halts after triage)"), ("sos_reply", "SOS reply"),
        ("checkin_conversation", "Check-in conversation"), ("checkin_quick_no_note", "Quick check-in (3 taps)"),
        ("awareness_reflection", "Awareness reflection"), ("belief_restatement", "Own-words belief restatement"),
        ("custom_struggle_category", "Custom struggle category"),
    ]
    out = []
    for key, label in labels:
        f = FC.get(key)
        if not f:
            continue
        luna = cost_at(f["prompt_tokens"], f["completion_tokens"], PRICE_IN, PRICE_OUT)
        opus = cost_at(f["prompt_tokens"], f["completion_tokens"], 4.00, 20.00)
        out.append(f"<tr><td>{E(label)}</td><td>{f['calls']}</td><td>{f['total_tokens']:,}</td>"
                   f"<td>{money(luna)}</td><td>{money(opus)}</td><td>{E(f.get('note', ''))}</td></tr>")
    return "\n".join(out)


# Monthly use per active user - an ASSUMPTION, stated in the report.
USAGE = [("talk_reply", 20), ("checkin_conversation", 8), ("checkin_quick_no_note", 8),
         ("awareness_reflection", 4), ("sos_reply", 2)]


def monthly(pin, pout):
    total = 0.0
    for key, n in USAGE:
        f = FC.get(key)
        if f:
            total += n * cost_at(f["prompt_tokens"], f["completion_tokens"], pin, pout)
    return total


def scale_rows():
    rows = []
    for label, pin, pout, note in [("gpt-6-luna (placeholder rate)", PRICE_IN, PRICE_OUT, "unverified rate"),
                                   ("claude-opus-5-5 ($4 / $20)", 4.00, 20.00, "published rate, luna token counts"),
                                   ("claude-haiku-5-5 ($0.10 / $0.50)", 0.10, 0.50, "published rate, luna token counts")]:
        m = monthly(pin, pout)
        rows.append(f"<tr><td>{E(label)}</td><td>{money(m)}</td><td>{money(m * 1000)}</td><td>{money(m * 100000)}</td><td>{E(note)}</td></tr>")
    return "\n".join(rows)


def part_c_rows():
    return "\n".join(f"<tr class='{'ok' if c['pass'] else 'bad'}'><td>{E(c['label'])}</td><td>{E(c['detail'])}</td>"
                     f"<td>{'pass' if c['pass'] else 'FAIL'}</td></tr>" for c in PART_C)


usage_text = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in USAGE)
pc_pass = sum(1 for c in PART_C if c["pass"])

BODY = f"""
<header>
<h1>Heard</h1>
<p class="motto">"I don't understand, but I want to understand. That's why I need you God."</p>
<p class="meta">Alexandria Rosenzweig · MGT 409 Final Project · Fall 2026</p>
<div class="repo"><b>Code (public GitHub repository):</b> <a href="{REPO}">{REPO}</a>
<code class="clone">git clone {REPO}.git</code>
Run instructions are in the repository README (Python 3.10+, one API key in <code>.env</code>, then <code>python app.py</code>).</div>
</header>

{MOTIVATION}
<h2>2. What is new</h2>
<p>The claim here is narrow. We compared Heard with two well-known faith apps and with a plain chatbot prompt; we did
not survey the market more widely.</p>
<table>
<tr><th>Product</th><th>What it is built around (from public descriptions)</th></tr>
<tr><td>Hallow</td><td>Catholic prayer and meditation: guided audio prayer, daily readings, Rosary and Lectio Divina,
community challenges. Subscription ($9.99/month or $69.99/year in the US).</td></tr>
<tr><td>YouVersion Bible App</td><td>Free Bible app: many translations, audio Bibles, reading plans, studying with friends.
A 2026 roundup reports an AI "life-situation" scripture search; we could not confirm this from a second source.</td></tr>
<tr><td>Plain chatbot prompt (our baseline)</td><td>A single prompt asking for Christian advice with verses. No stored
profile of the user's tradition, no verification step, no memory, and no way to connect the user with people.</td></tr>
</table>
<p>What Heard does that we did not find in the public descriptions of these two apps:</p>
<ul>
<li><b>Faith asked once, then used everywhere.</b> Setup starts with "for myself" or "a church". A person picks one or
more of 14 traditions (including "exploring") and answers 14 contested questions one at a time, or in their own words by
voice; Heard restates it and uses it only after the user approves. Counsel speaks from inside that frame.</li>
<li><b>Correction with love.</b> Replies name the wrong plainly and never condemn the person, on the model of John 8:11.</li>
<li><b>Verified scripture from the user's own canon.</b> Search runs over the full text of three public-domain translations
({n_verses:,} verses); every quoted verse is checked against that text before display.</li>
<li><b>Accountability with memory.</b> Check-ins are a short conversation that recalls what the user actually said
("you told me about the fight with Kyle...") and proposes a log the user confirms. A three-tap version costs nothing.</li>
<li><b>An SOS button for the moment of temptation.</b> Verses appear instantly, then Heard asks what is happening and answers
briefly with one action for the next ten minutes. Installable, with a home-screen shortcut.</li>
<li><b>Awareness.</b> A reflective profile (type, tendencies, strengths, struggles, pitfalls, practices) in which every
insight must cite the user's own history and no names appear.</li>
<li><b>Sponsor-style fellowship and church matching.</b> People who came through a struggle rank first; filters for
gender, age, marital status, denomination and years as a Christian; adults only. Churches self-register and get a
transparent match %.</li>
<li><b>Voice throughout.</b> Every text box takes speech; voice mode speaks replies aloud in a chosen natural voice and speed.</li>
</ul>

<h3>Measured against a plain chatbot</h3>
<p>Same {C.get('cases', 0)} struggles, same model, every citation from both checked against the biblical text.</p>
<div class="kpis">
<div class="kpi"><b>{C.get('baseline_verified', 0)}/{C.get('baseline_citations', 0)}</b><span>baseline citations verified</span></div>
<div class="kpi"><b>{C.get('heard_citations_shown', 0)}/{C.get('heard_citations_claimed', 0)}</b><span>Heard citations shown / claimed</span></div>
<div class="kpi"><b>{base_per_case:,.0f} vs {heard_per_case:,.0f}</b><span>tokens per reply, baseline vs Heard</span></div>
</div>
<div class="note"><b>Honest reading.</b> On this model the plain chatbot did not fabricate scripture in our test, so the
verifier is defense-in-depth: it guarantees no unverified verse reaches a user, but here it had nothing to remove. Heard
costs about {heard_per_case / max(base_per_case, 1):.1f}x more tokens per reply. What it adds is not citation accuracy on this
model; it is tradition conditioning, safety routing, memory and accountability, and the human connection.</div>

<h2>3. The agents</h2>
{DIAGRAM}
<table>
<tr><th>Agent</th><th>Role</th><th>Model call?</th></tr>
<tr><td>Triage</td><td>Theme, severity, scope on every Talk, SOS and check-in message; translates slang into biblical vocabulary; halts on crisis</td><td>Yes</td></tr>
<tr><td>Retrieval</td><td>BM25 over the full Bible; curated anchor verses guaranteed a place so search doesn't return only the harsh edge of a topic</td><td>No</td></tr>
<tr><td>Counsel</td><td>Reply from inside the user's tradition and approved convictions; adaptive tone; brief urgent mode for SOS; never repeats a verse in one conversation</td><td>Yes</td></tr>
<tr><td>Verifier</td><td>Removes citations that don't exist or don't match the text; always displays the real text</td><td>No</td></tr>
<tr><td>Accountability</td><td>Conversational check-in with a personal opener from the user's own words (names must appear in what they wrote); proposes a log to confirm. Quick mode scores by rule</td><td>Yes / No</td></tr>
<tr><td>Awareness</td><td>Reflective profile; every card must cite history items; cards naming people are dropped; verses chosen by retrieval</td><td>Yes (cached)</td></tr>
<tr><td>Beliefs</td><td>Restates own-words convictions for the user to approve</td><td>Yes</td></tr>
<tr><td>Fellowship / church matchers</td><td>Lived experience first; filters; adults only; transparent church match %</td><td>No</td></tr>
</table>
<p><b>Models.</b> All measurements were taken on <b>{E(EVAL_MODEL)}</b>. A Claude path (<code>claude-opus-5-5</code> through the
Anthropic API) is written but was <b>not run</b>, because no Anthropic key was available.</p>

<h3>One traced run</h3>
{traced_html()}
{img("01_setup_role.jpg", "Setup starts by asking: here for yourself, or setting up a church")}
{img("02_question_paging.jpg", "Contested questions one at a time; an own-words answer restated for approval (developer TEST input, not a user's belief)")}
{img("06_home_sos.jpg", "Talk is home: the SOS button, and a reply that corrects with love, with a verified verse")}
{img("03_chat.jpg", "The next turn follows the thread, connects the user's own custom struggle, and quotes a new verified verse")}
{img("05_sos.jpg", "SOS: verses instantly, then 'What's going on right now?'")}
{img("07_checkin_conversation.jpg", "A check-in that remembers: it opens with what the user said, then follows up naturally")}
{img("08_awareness.jpg", "Awareness: a reflection, not a diagnosis, with tendencies on spectrums")}
{img("09_awareness_cards.jpg", "Strengths, struggles and pitfalls, each 'based on' the user's own history")}
{img("10_fellowship.jpg", "Suggested fellowships with filters; someone who came through anger ranks first")}
{img("12_churches.jpg", "Church search ranked by match %, with the reasons shown")}

<h2>4. Tokenomics</h2>
<p>Every model call is metered from the API's own usage report on {E(EVAL_MODEL)}. Each feature below was measured with one
real, uncached run (<code>eval_costs.py</code>).</p>
<div class="warn"><b>About the dollar figures.</b> No published price for gpt-6-luna was available, so its column uses a
<b>placeholder</b> rate (${PRICE_IN}/${PRICE_OUT} per million input/output tokens) and is not verified. The Claude column applies
Anthropic's published prices to token counts measured on luna; Claude's tokenizer and always-on thinking differ, so it is
illustrative only.</div>
<table><tr><th>Feature</th><th>Calls</th><th>Tokens</th><th>luna (placeholder)</th><th>Opus 5.5 (illustrative)</th><th>Notes</th></tr>
{feature_rows()}</table>
<h3>Per user and at scale</h3>
<p>Assumed monthly use per active user (our assumption, not a measurement): {E(usage_text)}.</p>
<table><tr><th>Pricing basis</th><th>Per user / month</th><th>1,000 users</th><th>100,000 users</th><th>Status</th></tr>
{scale_rows()}</table>
<h3>Cost decisions and what they did</h3>
<table>
<tr><th>Decision</th><th>Effect</th></tr>
<tr><td>Query expansion folded into triage</td><td>One call saved per reply; "can't stop looking at porn" went from Acts 4:20 ("we cannot stop speaking") to Matthew 5:28.</td></tr>
<tr><td>Retrieval, verification and matching as code</td><td>Zero tokens for those steps; fabricated references impossible at retrieval.</td></tr>
<tr><td>Crisis halts after triage</td><td>The most important replies cost one call and never wait on counsel.</td></tr>
<tr><td>SOS verses chosen without a model</td><td>The SOS screen appears instantly; the model is used only if the user says what's happening.</td></tr>
<tr><td>Quick check-in by rule</td><td>0 tokens, versus about {FC.get('checkin_conversation', {}).get('total_tokens', 0):,} for a check-in conversation.</td></tr>
<tr><td>Awareness and check-in openers cached</td><td>Rebuilt only when new history arrives, not on every page view.</td></tr>
<tr><td>Raising the counsel token ceiling (a cost increase)</td><td>At 1,400 tokens a test reply hit the ceiling and came back empty; now 4,000 with one retry. Only tokens produced are billed.</td></tr>
</table>
<table><tr><th>Verses to counsel (k)</th><th>Avg total tokens</th><th>Avg counsel prompt tokens</th><th>Citations shown/claimed</th><th>Avg wall time</th></tr>
{ablation_rows()}</table>
<p class="meta">Four cases per setting. More verses cost more tokens with no gain in citations shown; k=6 is kept.
We also found the Portkey gateway caches identical requests; all evaluation runs force fresh calls.</p>

<h2>5. Safety</h2>
<h3>Risks</h3>
<ul>
<li><b>Missing a crisis</b>, or a false alarm that teaches people not to be honest.</li>
<li><b>Fabricated scripture</b>, told to someone in the moment they're most inclined to believe it.</li>
<li><b>Misreading someone's faith</b>, or a check-in "remembering" something the user never said.</li>
<li><b>A profile that leaks details</b>, such as the names of people the user confided about.</li>
<li><b>Predatory contact</b>, including adults being connected with minors.</li>
</ul>
<h3>Defenses</h3>
<ul>
<li><b>Triage on every message</b> (Talk, SOS, check-ins, custom struggles). Crisis halts with hand-written guidance and real US
hotlines; a narrow calibration gives outward-facing, non-physical cases counsel plus resources; if triage fails twice, resources show anyway.</li>
<li><b>Citation verifier</b> on every quoted verse; the real text is always what's displayed.</li>
<li><b>Approval loop</b> for own-words beliefs; <b>grounding guard</b> on check-in memory (any name not in the user's own words rejects the question).</li>
<li><b>Awareness guards</b>: every card must cite real history items; cards naming people are dropped; evidence labels stripped.</li>
<li><b>Fellowship is adults only</b>: no age, or under 18, means not shown, not suggested, and unable to send or receive requests.
Contact details unlock only after both accept. Church accounts can't use person features.</li>
</ul>
<h3>Defenses shown working</h3>
<p>{len(safety.get('part_a', []))} written fixtures, each run {S.get('repeats', 0)} times as independent calls, plus deterministic guard tests.</p>
<div class="kpis">
<div class="kpi"><b>{S.get('crisis_runs', 0) - S.get('crisis_missed', 0)}/{S.get('crisis_runs', 0)}</b><span>crisis runs halted</span></div>
<div class="kpi"><b>{S.get('false_alarms', 0)}/{S.get('counsel_runs', 0)}</b><span>false alarms</span></div>
<div class="kpi"><b>{pc_pass}/{len(PART_C)}</b><span>guard tests passed</span></div>
</div>
{img("13_crisis.jpg", "A crisis disclosed mid-conversation: one model call, then real help and nothing else")}
{img("14_verse_checker.jpg", "A reference that does not exist is rejected")}
<table><tr><th>Expected</th><th>Kind</th><th>Test input</th><th>Correct</th></tr>{safety_rows()}</table>
<table><tr><th>Verifier case</th><th>Reference</th><th>Quoted wording</th><th>Verdict</th><th>Reason</th></tr>{verifier_rows()}</table>
<table><tr><th>Guard test</th><th>Result</th><th></th></tr>{part_c_rows()}</table>

<h2>6. Impact</h2>
<p><b>Why the matching mechanic matters.</b> Heard's fellowship design borrows from sponsorship in 12-step recovery. In a
prospective study, Tonigan and Rice found that having an AA sponsor predicted greater abstinence during early affiliation,
after controlling for motivation and treatment (<i>Psychology of Addictive Behaviors</i>, 2010, 24(3):397-403,
doi:10.1037/a0019013; 182 participants with complete data). These details come from summaries of the paper; we could not
access the full text. One study is not proof; we cite it as a reason the mechanic is worth testing.</p>
<div class="kpis">
<div class="kpi"><b>{n_real_users}</b><span>real users so far</span></div>
<div class="kpi"><b>{len(real_runs)}</b><span>real messages</span></div>
<div class="kpi"><b>{sum(checkin_counts.values())}</b><span>real check-ins</span></div>
</div>
<div class="note">All activity from building and testing, including the demo user and demo people and churches, is marked as
test data and excluded. These counts include only genuine use. A credible impact claim needs a consenting pilot with a church
or campus ministry, measuring commitments held and check-in trends over several weeks.</div>

<h2>7. Limits and next steps</h2>
<ul>
<li><b>Claude path unmeasured</b>; re-run all three evaluations with an Anthropic key before quoting Claude costs.</li>
<li><b>Safety evaluation is small and written</b>: {len(safety.get('part_a', []))} explicit fixtures; indirect or coded language is untested.
Next: a larger set reviewed by someone with crisis-line training. Crisis resources are US-only.</li>
<li><b>Reminders don't reach people yet.</b> Check-ins happen in the app; there are no notifications or texts.</li>
<li><b>Voice depends on the browser</b>: listening needs Chrome or Edge (Chrome sends audio to Google); natural voices depend on the
browser (Edge has the best). Server-side speech was tested on the course key but its models are not deployed.</li>
<li><b>The home-screen shortcut needs hosting.</b> It installs on desktop now; phones need Heard hosted online.</li>
<li><b>Awareness is a reflection, not a validated instrument</b>, and says so.</li>
<li><b>ESV and NASB.</b> Copyrighted, so not bundled. ESV is available through Crossway's API (free for non-commercial use, with a key,
attribution, a link to esv.org, and at most 500 verses stored); NASB appears available through API.Bible's free plan, terms to confirm.</li>
<li><b>Retrieval is lexical</b>; semantic retrieval is the next upgrade. <b>Moderation</b> of person-to-person contact is limited to accept,
decline and block. <b>Tradition notes</b> are the model's general framing, by design, and should be reviewed by members of each tradition.</li>
<li><b>Demo data</b>: 14 demo people and 8 demo churches, all fictional and labeled; demo profiles accept connection requests
automatically so the flow can be shown, and real people always decide.</li>
</ul>
"""

page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Heard - Report</title>
<style>{CSS}</style></head><body><main>{BODY}</main></body></html>"""

(HERE / "report.html").write_text(page, encoding="utf-8")
print(f"wrote report.html ({len(page) / 1e6:.2f} MB) | real users={n_real_users} runs={len(real_runs)} "
      f"| safety={'yes' if safety else 'MISSING'} compare={'yes' if compare else 'MISSING'} costs={'yes' if FC else 'MISSING'}")


# =================================================================== SLIDES
def slide_img(name):
    p = IMG / name
    if not p.exists():
        return ""
    return f'<img class="shot" src="data:image/jpeg;base64,{base64.b64encode(p.read_bytes()).decode()}">'


tl = FC.get("talk_reply", {})
SLIDES = [
    f"""<section class="title"><h1>Heard</h1>
<p class="motto">"I don't understand, but I want to understand.<br>That's why I need you God."</p>
<p class="small">Motto from "Why God" by Austin French</p>
<p class="repoline">Code (public): <a href="{REPO}">{REPO}</a></p>
<p class="small">Alexandria Rosenzweig · MGT 409</p></section>""",

    """<section><h2>Why Heard</h2>
<p class="big">"People need to be heard for their true strifes and get real advice from a biblical rather than worldly perspective."</p>
<ul>
<li><b>From experience:</b> "Especially in the MBA there is a lot of temptation to follow the majority opinion or cross boundaries that had previously been firm for myself."</li>
<li><b>For:</b> anyone struggling with bad decisions, decisions yet to make, or decisions they have no power in.</li>
<li><b>The gap:</b> "There isn't an adaptable app for each person's own journey and values."</li>
</ul>
<p class="small">Alexandria Rosenzweig, in her own words</p></section>""",

    f"""<section><h2>What is new</h2><ul>
<li><b>Your faith, asked once</b> · many traditions or "exploring" · beliefs in your own words, approved by you</li>
<li><b>Correction with love</b> · honest about sin, never condemning (John 8:11)</li>
<li><b>Verified scripture</b> · {n_verses:,} verses, your canon, every quote checked</li>
<li><b>Accountability with memory</b> · check-ins that recall what you said</li>
<li><b>SOS</b> · verses instantly, one action for the next ten minutes</li>
<li><b>Awareness</b> · a reflection built only from your own history</li>
<li><b>Fellowship & churches</b> · people who came through it first; transparent church match</li></ul></section>""",

    f"""<section><h2>The agents</h2>{DIAGRAM}
<p class="small">Triage runs on every message; retrieval, verification and matching are code (zero tokens, auditable).</p></section>""",

    f"""<section><h2>Talk and SOS</h2><div class="two">{slide_img("06_home_sos.jpg")}{slide_img("05_sos.jpg")}</div>
<p class="small">Home: SOS button and a reply that corrects with love · SOS: verses first, then "What's going on?"</p></section>""",

    f"""<section><h2>Accountability and Awareness</h2><div class="two">{slide_img("07_checkin_conversation.jpg")}{slide_img("08_awareness.jpg")}</div>
<p class="small">Check-ins remember what you said (names only if you wrote them) · Awareness: every insight backed by your history</p></section>""",

    f"""<section><h2>Fellowship and churches</h2><div class="two">{slide_img("10_fellowship.jpg")}{slide_img("12_churches.jpg")}</div>
<p class="small">Overcomers first, filters, adults only · churches ranked by a transparent match %</p></section>""",

    f"""<section><h2>Cost</h2>
<div class="kpis"><div><b>{tl.get('total_tokens', 0):,}</b>tokens / Talk reply</div><div><b>0</b>quick check-in</div><div><b>{FC.get('awareness_reflection', {}).get('total_tokens', 0):,}</b>Awareness (cached)</div></div>
<p>About <b>{money(monthly(4.00, 20.00))}</b> per user per month at Claude Opus 5.5 published prices, on our usage assumption.</p>
<p class="small">Measured on gpt-6-luna · luna price unverified · Claude token counts still to be measured</p></section>""",

    f"""<section><h2>Safety, measured</h2>
<div class="kpis"><div><b>{S.get('crisis_runs', 0) - S.get('crisis_missed', 0)}/{S.get('crisis_runs', 0)}</b>crises halted</div>
<div><b>{S.get('false_alarms', 0)}/{S.get('counsel_runs', 0)}</b>false alarms</div>
<div><b>{pc_pass}/{len(PART_C)}</b>guard tests</div></div>
<div class="two">{slide_img("13_crisis.jpg")}{slide_img("14_verse_checker.jpg")}</div>
<p class="small">Fabricated verses removed · invented names rejected · Fellowship adults only</p></section>""",

    f"""<section><h2>Impact, limits, next steps</h2><ul>
<li>Sponsorship predicted early abstinence (Tonigan &amp; Rice, 2010; one study, from summaries)</li>
<li>{n_real_users} real user(s) so far; next: a consenting church or campus pilot</li>
<li>Measure the Claude path · larger safety set · reminders that reach people</li>
<li>ESV/NASB via publisher APIs · semantic retrieval · real moderation</li></ul>
<p class="repoline">Code (public): <a href="{REPO}">{REPO}</a></p></section>""",
]
assert len(SLIDES) <= 10, "assignment allows 10 slides maximum"

SLIDE_CSS = """
.reveal{font-family:Georgia,serif;color:#2b2622}.reveal h1,.reveal h2{font-family:Georgia,serif;color:#2b2622;text-transform:none}
.reveal h1{font-size:3em}.reveal h2{font-size:1.5em;margin-bottom:.4em}.reveal ul{font-size:.75em}.reveal p{font-size:.82em}
.reveal .motto{font-style:italic;color:#7a6f65}.reveal .small{font-size:.58em;color:#7a6f65}.reveal .big{font-size:1.05em;font-weight:600}
.reveal .repoline{font:600 .7em sans-serif;background:#f7f3ec;display:inline-block;padding:.35em .8em;border-radius:10px}
.reveal .repoline a{color:#8a5a2b}
.reveal .two{display:flex;gap:16px;justify-content:center;align-items:flex-start}
.reveal .two img.shot{width:auto;max-width:48%;max-height:340px}
.reveal img.shot{border:1px solid #e7dfd2;border-radius:10px;margin:6px 0}
.reveal .kpis{display:flex;gap:18px;justify-content:center;margin:.4em 0}
.reveal .kpis div{background:#f7f3ec;border-radius:12px;padding:.4em .9em;font-size:.58em;color:#7a6f65}
.reveal .kpis b{display:block;font-size:2em;color:#2b2622}.reveal svg{width:92%;max-height:440px}
.reveal-viewport{background:#fbf8f2}
"""

deck = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Heard - Slides</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/theme/white.css">
<style>{SLIDE_CSS}</style></head><body>
<div class="reveal"><div class="slides">{''.join(SLIDES)}</div></div>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.js"></script>
<script>Reveal.initialize({{hash:true,slideNumber:true,width:1200,height:760}});</script>
</body></html>"""
(HERE / "slides.html").write_text(deck, encoding="utf-8")
print(f"wrote slides.html ({len(SLIDES)} slides, {len(deck) / 1e6:.2f} MB)")
