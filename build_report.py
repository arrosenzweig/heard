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
code{font-size:.9em;background:var(--soft);padding:1px 5px;border-radius:4px}.missing{color:var(--bad)}
@media print{body{font-size:14px;line-height:1.6}main{padding:0 6px}h1{font-size:1.9rem}h2{font-size:1.25rem;margin:1.4em 0 .4em;page-break-after:avoid}h3{margin:1em 0 .3em}table{font-size:12px;margin:10px 0 14px}th,td{padding:5px 7px}.kpi{padding:8px 10px}.kpi b{font-size:1.3rem}figure,table,.kpis,.note,.warn{page-break-inside:avoid}figcaption{font-size:10.5px}svg{max-height:300px}}
"""

safe_runs = S.get("crisis_runs", 0)
counsel_runs = S.get("counsel_runs", 0)

BODY = f"""
<header>
<h1>Heard</h1>
<p class="motto">"I don't understand, but I want to understand. That's why I need you God."</p>
<p class="meta">Alexandria Rosenzweig · MGT 409 Final Project · Fall 2026<br>
Code: <a href="https://github.com/arrosenzweig/heard">github.com/arrosenzweig/heard</a> ·
Slides: <code>slides.html</code> · Run locally: see README (about 3 minutes)</p>
</header>

<h2>1. Motivation</h2>
<p>In the author's words, from her first description of the idea: <i>"the world is crazy and we are tempted to break
the morals and values others don't follow. I want to build an app to hear from the Christian perspective on how to
combat the sins in our lives and stand strong with what we believe in."</i></p>
<p>She asked for Bible verses, plain-language advice, perspectives from different denominations, and a way to connect
with other Christians for community or to talk when needed.</p>

<h2>2. What is new</h2>
<p>The claim here is narrow. We compared Heard with two well-known faith apps and with a plain chatbot prompt;
we did not survey the market more widely.</p>
<table>
<tr><th>Product</th><th>What it is built around (from public descriptions)</th></tr>
<tr><td>Hallow</td><td>Catholic prayer and meditation: guided audio prayer, daily readings, Rosary and Lectio Divina,
community challenges. Subscription ($9.99/month or $69.99/year in the US).</td></tr>
<tr><td>YouVersion Bible App</td><td>Free Bible app: many translations, audio Bibles, reading plans, studying with friends.
A 2026 roundup reports an AI "life-situation" scripture search; we could not confirm this from a second source.</td></tr>
<tr><td>Plain chatbot prompt (our baseline)</td><td>A single prompt asking for Christian advice with verses. It has no
stored profile of the user's tradition, no verification step, and no way to connect the user with other people.</td></tr>
</table>
<p>What Heard does that we did not find in the public descriptions of these two apps:</p>
<ul>
<li><b>Tradition-conditioned counsel.</b> Users pick one <i>or more</i> of 14 traditions (including "exploring") and may
state convictions on 14 contested questions. Counsel speaks from inside that frame and never argues them out of it.</li>
<li><b>An approval loop on beliefs.</b> A user can answer any contested question in their own words, by voice or text.
Heard restates it plainly and uses it only after the user confirms the restatement is what they meant.</li>
<li><b>Verified citations from the user's own canon.</b> Search runs over the complete text of three public-domain
translations ({n_verses:,} verses). Catholic users get the Douay-Rheims, including Tobit, Judith, Wisdom, Sirach and Baruch.
Every quoted verse is checked against the text before display.</li>
<li><b>Sponsor-style matching.</b> Heard ranks people who <i>came through</i> the same struggle above people still in it,
then by shared tradition and city. Contact details unlock only after both people accept.</li>
<li><b>An accountability agent.</b> For each struggle, Heard checks in on the user's chosen schedule with three quick
questions and an optional note, names the trend (overcoming, holding, deeper), and after two "deeper" check-ins in a row
points the user to a person and a church. Commitments made in conversation are followed up the same way.</li>
<li><b>Faith asked once.</b> Tradition and convictions are gathered only at onboarding, then shape everything; the user can
change them in Settings. Every text box accepts voice.</li>
</ul>

<h3>Measured against a plain chatbot</h3>
<p>We sent the same {C.get('cases', 0)} struggles to a plain "helpful Christian advisor" prompt and to Heard, on the same model,
and checked every citation from both against the biblical text. A baseline quote was checked against all three
translations; one matching none would have been counted "unverifiable", not "fabricated", since it might be real
wording from a copyrighted translation.</p>
<div class="kpis">
<div class="kpi"><b>{C.get('baseline_verified', 0)}/{C.get('baseline_citations', 0)}</b><span>baseline citations verified</span></div>
<div class="kpi"><b>{C.get('baseline_fabricated_refs', 0)}</b><span>baseline references that do not exist</span></div>
<div class="kpi"><b>{C.get('heard_citations_shown', 0)}/{C.get('heard_citations_claimed', 0)}</b><span>Heard citations shown / claimed</span></div>
<div class="kpi"><b>{base_per_case:,.0f} vs {heard_per_case:,.0f}</b><span>tokens per case, baseline vs Heard</span></div>
</div>
<div class="note"><b>Honest reading.</b> On this model the plain chatbot <i>did not</i> fabricate scripture in our test.
So Heard's verifier is defense-in-depth: it guarantees no unverified verse ever reaches a user, but in this sample
it had nothing to remove. Heard also costs about four times more tokens per case. The difference Heard makes is not
citation accuracy on this model. It is tradition conditioning, the safety routing, and the human connection, which
a single chatbot call does not attempt.</div>
<table><tr><th>Struggle (test fixture)</th><th>Baseline verified</th><th>Heard shown/claimed</th><th>Heard removed</th><th>Heard tokens</th></tr>
{compare_rows()}</table>

<h2>3. The agents</h2>
{DIAGRAM}
<table>
<tr><th>Step</th><th>Role</th><th>Tools / data</th><th>Model call?</th></tr>
<tr><td>1 Triage</td><td>Classifies theme, severity (ordinary / elevated / crisis), scope; translates slang into biblical vocabulary</td><td>20 struggle themes; US crisis resources</td><td>Yes</td></tr>
<tr><td>2 Retrieval</td><td>Finds candidate verses across the full Bible</td><td>BM25 index; 154 curated anchor verses as a ranking prior</td><td>No</td></tr>
<tr><td>3 Counsel</td><td>Writes the response from inside the user's tradition; chooses tone (comfort, challenge, steady, listen)</td><td>Profile, approved beliefs, recent journal, retrieved verses only</td><td>Yes</td></tr>
<tr><td>4 Verifier</td><td>Removes citations that do not exist or whose wording does not match; always displays the real text</td><td>Local corpus lookup</td><td>No</td></tr>
<tr><td>5 Matcher</td><td>Ranks companions by lived experience of the same struggle</td><td>Profiles (synthetic demo set)</td><td>No</td></tr>
<tr><td>Beliefs</td><td>Restates own-words convictions for approval</td><td>Voice (browser speech) or text</td><td>Yes</td></tr>
<tr><td>Accountability</td><td>Scheduled check-ins per struggle; names the trend; escalates to people after two "deeper" in a row</td><td>Check-in history; triage on any written note</td><td>Only when the user writes a note or is sliding deeper</td></tr>
</table>
<p><b>Models.</b> All evaluation numbers in this report were measured on <b>{E(EVAL_MODEL)}</b>.
The app also supports Claude (<code>claude-opus-5-5</code>) through the Anthropic API when an
<code>ANTHROPIC_API_KEY</code> is present; that path was written but <b>not yet run</b>, because no Anthropic key
was available when these measurements were taken.</p>
<p><b>Why only two model calls.</b> Search, verification and matching are deterministic. That keeps the
safety-critical steps auditable and cheap, and it makes fabricated references impossible at the retrieval step:
a lexical index can only return verses that exist.</p>

<h3>One traced run</h3>
{traced_html()}
{img("08_chat.jpg", "An earlier run of the same conversation (demo user): the second turn follows the thread and quotes a new, verified verse")}
{img("09_chat_commitment.jpg", "The rest of that reply: a checkable commitment, a prayer, and companions who came through the same struggle")}
{img("07_onboarding.jpg", "Onboarding, the only time Heard asks about faith: one or more traditions, or 'exploring'")}
{img("01_belief_approval.jpg", "The approval loop on a TEST input written by the developer (not a user's belief): rambling text restated plainly, uncertainty kept, waiting for approval")}
{img("10_checkin.jpg", "Accountability check-in (demo user): it reads the note and names the trend")}
{img("05_people_match.jpg", "Sponsor-style matching: someone who came through anger ranks above someone still fighting it")}

<h2>4. Tokenomics</h2>
<p>Every model call is metered from the API's own usage report. The figures below are measured, not estimated,
on {E(EVAL_MODEL)}.</p>
<div class="kpis">
<div class="kpi"><b>{heard_per_case:,.0f}</b><span>tokens per Heard response (mean, {C.get('cases', 0)} cases)</span></div>
<div class="kpi"><b>2</b><span>model calls per response (triage + counsel)</span></div>
<div class="kpi"><b>1</b><span>model call when a crisis halts the pipeline</span></div>
<div class="kpi"><b>0</b><span>tokens for retrieval, verification, matching</span></div>
</div>
<h3>Cost per run, per user, and at scale</h3>
<div class="warn"><b>Read the price column carefully.</b> No published per-token price for gpt-6-luna was available to
us, so its dollar figures use a <b>placeholder</b> rate (${PRICE_IN}/${PRICE_OUT} per million input/output tokens) and
are <b>not verified</b>. The Claude rows apply Anthropic's published prices to the token counts measured on luna. That
is only illustrative: Claude uses a different tokenizer and always-on thinking, so its real token counts must be
measured, not assumed. The input/output split ({(INPUT_SHARE or 0) * 100:.0f}% / {(1 - (INPUT_SHARE or 0)) * 100:.0f}%) is measured from
{SPLIT_N} recorded responses. The per-user column assumes <b>{ENTRIES_PER_USER_MONTH} entries per user per month</b>,
which is our assumption, not a measurement.</div>
<table>
<tr><th>Pricing basis</th><th>Per run</th><th>Per user / month</th><th>1,000 users</th><th>100,000 users</th><th>Status</th></tr>
{scale_row("gpt-6-luna (placeholder rate)", run_cost_luna, "unverified rate")}
{scale_row("claude-opus-5-5 ($4 / $20)", run_cost_opus, "published rate, luna token counts")}
{scale_row("claude-haiku-5-5 ($0.10 / $0.50)", run_cost_haiku, "published rate, luna token counts")}
</table>

<h3>Cost cuts and what they did</h3>
<table>
<tr><th>Decision</th><th>Effect (measured where possible)</th></tr>
<tr><td>Fold query expansion into the triage call instead of a separate call</td><td>One model call saved per response. Retrieval quality improved sharply: "can't stop looking at porn" went from
Acts 4:20 ("we cannot stop speaking") to Matthew 5:28.</td></tr>
<tr><td>Retrieval, verification and matching as code, not model calls</td><td>Zero tokens for three of five steps.</td></tr>
<tr><td>Crisis halts after triage</td><td>The most important responses cost one call, not two, and never wait on counsel.</td></tr>
<tr><td>Check-ins answered by rule unless the user writes something</td><td>A check-in with no note costs <b>0 tokens</b>
(the trend is computed from the answers). With a note or a "deeper" trend it calls the model{f": {CHECKIN_MODEL_TOKENS:,.0f} tokens on average, measured" if CHECKIN_MODEL_TOKENS else ""}.</td></tr>
<tr><td>Retrieval depth (verses handed to counsel)</td><td>See the ablation below.</td></tr>
<tr><td>Raise the counsel token ceiling (a cost <i>increase</i>)</td><td>With a 1,400-token ceiling, a test response hit the ceiling exactly and came back as
an empty reply, which a user would have seen as a blank response. The ceiling is now 4,000 with one retry. Only tokens actually produced
are billed, so the change costs nothing when the model does not need the room.</td></tr>
</table>
<table><tr><th>Verses to counsel (k)</th><th>Avg total tokens</th><th>Avg counsel prompt tokens</th><th>Citations shown/claimed</th><th>Avg wall time</th></tr>
{ablation_rows()}</table>
<p class="meta">Four cases per setting, so this is noisy: retries after truncated output inflate some rows (k=3 used more
tokens than k=6). It shows no evidence that handing counsel more verses helps; k=6 is kept.</p>
<div class="note"><b>Gateway caching.</b> We found the Portkey gateway caches identical requests and replays them in about
0.1 seconds. That is a real saving in production, but it had silently turned repeated evaluation runs into replays.
All numbers in this report come from runs with caching forced off.</div>

<h2>5. Safety</h2>
<h3>Risks</h3>
<ul>
<li><b>Missing a crisis.</b> Someone suicidal, being abused, or describing a child in danger gets a devotional instead of help.</li>
<li><b>False alarms.</b> A parent who confesses yelling at their kids is told to call 911, and may stop being honest with the app.</li>
<li><b>Fabricated scripture.</b> Telling someone God said something God did not say, in the moment they are most inclined to believe it.</li>
<li><b>Misreading someone's faith.</b> Conditioning every response on a distorted version of what a user believes.</li>
<li><b>Predatory contact.</b> A list of people who disclosed loneliness or addiction could be misused by someone looking for vulnerable people.</li>
</ul>
<h3>Defenses</h3>
<ul>
<li><b>Triage on every message</b>, in conversation and in check-in notes, not only the first.</li>
<li><b>Crisis halt.</b> Hand-written guidance and real US hotlines (988, Crisis Text Line, National DV Hotline, Childhelp, 911). No verse, no advice, no matches.</li>
<li><b>A narrow, calibrated floor.</b> Kinds that concern the user's own safety always halt. Outward-facing kinds with no physical
danger get counsel <i>with</i> the resources shown above it. If triage fails twice, the resources are shown anyway.</li>
<li><b>Citation verifier</b> on every quoted verse; the app always displays the real text.</li>
<li><b>Belief approval loop</b>; an unapproved restatement is never used.</li>
<li><b>Contact gate</b>: phone numbers unlock only after both people accept; city-level location only; decline and block.</li>
</ul>
<h3>Defense shown working</h3>
<p>Fixed test set of {len(safety.get('part_a', []))} written fixtures, each run {S.get('repeats', 0)} times as independent calls.</p>
<div class="kpis">
<div class="kpi"><b>{S.get('crisis_runs', 0) - S.get('crisis_missed', 0)}/{S.get('crisis_runs', 0)}</b><span>crisis runs halted correctly</span></div>
<div class="kpi"><b>{S.get('false_alarms', 0)}/{S.get('counsel_runs', 0)}</b><span>false alarms on ordinary struggles</span></div>
<div class="kpi"><b>{S.get('softened_with_safety_note', 0)}</b><span>ordinary runs given counsel + resources</span></div>
</div>
{img("04_crisis_halt.jpg", "A crisis disclosed mid-conversation (demo user): one model call, then real help and nothing else")}
<table><tr><th>Expected</th><th>Kind</th><th>Test input</th><th>Correct</th></tr>{safety_rows()}</table>
<h3>Verifier against fabricated citations</h3>
<table><tr><th>Case</th><th>Reference</th><th>Quoted wording</th><th>Verdict</th><th>Reason</th></tr>{verifier_rows()}</table>
{img("06_verse_checker.jpg", "A reference that does not exist is rejected")}

<h2>6. Impact</h2>
<p><b>Why the matching mechanic matters.</b> Heard's human-connection design borrows from sponsorship in 12-step
recovery. In a prospective study, Tonigan and Rice found that having an AA sponsor predicted greater abstinence
during early affiliation, after controlling for motivation and treatment (<i>Psychology of Addictive Behaviors</i>,
2010, 24(3):397-403, doi:10.1037/a0019013; 182 participants with complete data). These details come from summaries of the paper; we could not access the
full text. One study is not proof, and we have not reviewed the wider research. We cite it as a reason the mechanic is
worth testing, not as evidence that Heard works.</p>
<h3>Real use</h3>
<div class="kpis">
<div class="kpi"><b>{n_real_users}</b><span>real users so far</span></div>
<div class="kpi"><b>{len(real_runs)}</b><span>real messages processed</span></div>
<div class="kpi"><b>{sum(checkin_counts.values())}</b><span>real check-ins</span></div>
<div class="kpi"><b>{outcome.get('total', 0)}</b><span>commitments followed up</span></div>
<div class="kpi"><b>{'n/a' if outcome.get('held_rate') is None else f"{outcome['held_rate'] * 100:.0f}%"}</b><span>held rate</span></div>
</div>
<div class="note">Everything created while building and testing the app, including the demo user, is marked as test data
and excluded here. These counts include only genuine use. Outcome numbers above come directly from the app's database and
are reported as they are, including if they are zero. We did not invent users or results. A credible impact claim would
need a small pilot with consenting users from a church or campus ministry, measuring commitments held over several weeks.</div>

<h2>7. Limits and next steps</h2>
<ul>
<li><b>Claude path unmeasured.</b> Re-run both evaluations with an Anthropic key before quoting Claude costs or accuracy.</li>
<li><b>Safety evaluation is small and written.</b> {len(safety.get('part_a', []))} fixtures, all using explicit language.
Indirect or coded language is not tested. Next: a larger set reviewed by someone with crisis-line training.</li>
<li><b>Crisis resources are US-only.</b></li>
<li><b>Check-ins are in-app only.</b> There are no push notifications, texts or emails yet, so Heard can only ask when the
user opens it. A real accountability loop needs reminders that reach the user.</li>
<li><b>Voice needs Chrome or Edge</b>, and in Chrome the audio is sent to Google to be transcribed. A server-side fallback
was tested on the course key; its transcription models are listed but not deployed, so it was not built.</li>
<li><b>Retrieval is lexical.</b> It can surface verses that match words but not meaning (for example a verse about God
<i>not</i> hearing prayer, for someone afraid God is not listening). Counsel chooses among candidates, but semantic
retrieval is the obvious upgrade.</li>
<li><b>No moderation of user-to-user contact</b> beyond accept, decline and block. A real launch needs reporting, review and identity checks.</li>
<li><b>Tradition notes are the model's general framing</b>, not drawn from each church's documents, by design (the Bible
is the only content source). Users of a given tradition should review them.</li>
<li><b>No real users yet.</b> No pilot yet.</li>
</ul>
"""

page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Heard - Report</title>
<style>{CSS}</style></head><body><main>{BODY}</main></body></html>"""

(HERE / "report.html").write_text(page, encoding="utf-8")
print(f"wrote report.html ({len(page) / 1e6:.2f} MB) | runs={len(real_runs)} outcomes={outcome.get('total', 0)} "
      f"| safety={'yes' if safety else 'MISSING'} compare={'yes' if compare else 'MISSING'}")


# =================================================================== SLIDES
# Same evidence, same numbers, 10 slides max (reveal.js).
def slide_img(name):
    p = IMG / name
    if not p.exists():
        return ""
    return f'<img class="shot" src="data:image/jpeg;base64,{base64.b64encode(p.read_bytes()).decode()}">'


_fab = [r for r in safety.get("part_b", []) if r["label"] in
        ("nonexistent book", "nonexistent chapter", "real ref, invented words")]
_fab_total = len(_fab)
_fab_removed = sum(1 for r in _fab if r["verdict"] == "removed")

SLIDES = [
    f"""<section class="title"><h1>Heard</h1>
<p class="motto">"I don't understand, but I want to understand.<br>That's why I need you God."</p>
<p class="small">Help to stand strong in what you believe<br>
Alexandria Rosenzweig · MGT 409 · github.com/arrosenzweig/heard</p></section>""",

    """<section><h2>Why Heard</h2>
<p class="big">"The world is crazy and we are tempted to break the morals and values others don't follow."</p>
<p>The goal: hear the Christian perspective on how to combat the sins in our lives and stand strong in what we believe.</p>
<ul><li>Bible verses</li><li>Plain-language advice</li><li>Perspectives from different denominations</li>
<li>Community with other Christians</li></ul></section>""",

    f"""<section><h2>What is new</h2><ul>
<li><b>Your tradition, not a generic one.</b> Pick one or more of 14, including "exploring."</li>
<li><b>"Did I hear you right?"</b> Say a belief in your own words; Heard restates it and waits for your yes.</li>
<li><b>The whole Bible, your canon, verified.</b> {n_verses:,} verses, three translations; Catholics get the deuterocanon.</li>
<li><b>Sponsor-style matching.</b> People who <i>came through</i> your struggle rank first.</li>
<li><b>An accountability agent.</b> Check-ins on your schedule; it names the trend and brings in people when you slide.</li></ul></section>""",

    f"""<section><h2>The agents</h2>{DIAGRAM}
<p class="small">Two model calls per response. Retrieval, verification and matching are code: zero tokens, fully auditable.</p></section>""",

    f"""<section><h2>Demo: a real conversation</h2><div class="two">{slide_img("08_chat.jpg")}{slide_img("09_chat_commitment.jpg")}</div>
<p class="small">Follows the thread, quotes a new verified verse, offers a commitment, and points to someone who came through it. Demo user.</p></section>""",

    f"""<section><h2>Understood, then held accountable</h2><div class="two">{slide_img("01_belief_approval.jpg")}{slide_img("10_checkin.jpg")}</div>
<p class="small">Left: a rambling answer restated plainly, waiting for a yes (developer test input) · Right: a check-in that reads the note and names the trend</p></section>""",

    f"""<section><h2>Cost</h2>
<div class="kpis"><div><b>{heard_per_case:,.0f}</b>tokens / response</div><div><b>2</b>model calls</div><div><b>1</b>call on crisis</div></div>
<p>Measured on gpt-6-luna. At Claude Opus 5.5 published prices that is about <b>{money(run_cost_opus)}</b> per response,
<b>{money(run_cost_opus * ENTRIES_PER_USER_MONTH)}</b> per user per month at {ENTRIES_PER_USER_MONTH} entries (our assumption).</p>
<p class="small">Luna price unverified · Claude token counts still to be measured · biggest saving: expansion folded into triage</p></section>""",

    f"""<section><h2>Safety, measured</h2>
<div class="kpis"><div><b>{S.get('crisis_runs', 0) - S.get('crisis_missed', 0)}/{S.get('crisis_runs', 0)}</b>crises halted</div>
<div><b>{S.get('false_alarms', 0)}/{S.get('counsel_runs', 0)}</b>false alarms</div>
<div><b>{_fab_removed}/{_fab_total}</b>fabricated citations removed</div></div>
<div class="two">{slide_img("04_crisis_halt.jpg")}{slide_img("06_verse_checker.jpg")}</div>
<p class="small">{len(safety.get('part_a', []))} written fixtures × {S.get('repeats', 0)} independent runs. Explicit language only; oblique crisis language untested.</p></section>""",

    f"""<section><h2>Impact</h2>
<p>Sponsorship in recovery: having a sponsor predicted greater early abstinence
<span class="small">(Tonigan &amp; Rice, 2010; one study, from summaries)</span>.</p>
<p><b>Honest status:</b> {n_real_users} real users so far. All building and testing activity is marked as test data and excluded.</p>
<p>Next: a small consenting pilot with a church or campus ministry, measuring commitments held over weeks.</p></section>""",

    """<section><h2>Limits &amp; next steps</h2><ul>
<li>Measure the Claude path (written, not yet run).</li>
<li>Bigger safety set, reviewed by crisis-line-trained people; oblique language.</li>
<li>Semantic retrieval: lexical search can match words but miss meaning.</li>
<li>Real moderation for person-to-person contact.</li>
<li>Reminders that reach people (check-ins are in-app only today).</li>
<li>Crisis resources beyond the US.</li></ul>
</section>""",
]
assert len(SLIDES) <= 10, "assignment allows 10 slides maximum"

SLIDE_CSS = """
.reveal{font-family:Georgia,serif;color:#2b2622}.reveal h1,.reveal h2{font-family:Georgia,serif;color:#2b2622;text-transform:none}
.reveal h1{font-size:3em}.reveal h2{font-size:1.6em;margin-bottom:.5em}.reveal ul{font-size:.82em}.reveal p{font-size:.85em}
.reveal .motto{font-style:italic;color:#7a6f65}.reveal .small{font-size:.6em;color:#7a6f65}.reveal .big{font-size:1.15em;font-weight:600}
.reveal .two{display:flex;gap:16px;justify-content:center;align-items:flex-start}.reveal .two img.shot{width:auto;max-width:48%;max-height:340px}
.reveal img.shot{border:1px solid #e7dfd2;border-radius:10px;margin:6px 0}
.reveal .kpis{display:flex;gap:18px;justify-content:center;margin:.4em 0}
.reveal .kpis div{background:#f7f3ec;border-radius:12px;padding:.4em .9em;font-size:.6em;color:#7a6f65}
.reveal .kpis b{display:block;font-size:2em;color:#2b2622}.reveal svg{width:90%}
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
