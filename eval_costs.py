"""Measured cost of each feature - one real, uncached run of each.

Runs on a throwaway database so no real profile is touched. Inputs are
fixtures written for this measurement, not user data.

Run:  python eval_costs.py      -> docs/feature_costs.json
"""
import os
import tempfile

os.environ.setdefault("HEARD_FORCE_FRESH", "1")
os.environ["HEARD_DB"] = os.path.join(tempfile.gettempdir(), "heard_costs_eval.db")
if os.path.exists(os.environ["HEARD_DB"]):
    os.remove(os.environ["HEARD_DB"])

import json
import time
from pathlib import Path

import llm
import store
from agents import accountability, awareness, beliefs, pipeline, triage

store.init()
pid = store.create_profile(kind="person", handle="Cost fixture", tradition='["nondenom","baptist"]',
                           fighting=["anger", "envy"], age=30, onboarded=1, checkin_hours=24)
profile = store.get_profile(pid)
profile["tradition"] = ["nondenom", "baptist"]
rows = {}


def measure(name, fn, note=""):
    tr = llm.Trace(label=name)
    t = time.time()
    own = fn(tr)
    # The Talk pipeline keeps its own trace and returns it; use that when given.
    s = own if isinstance(own, dict) and "n_calls" in own else tr.summary()
    rows[name] = {"calls": s["n_calls"], "prompt_tokens": s["prompt_tokens"],
                  "completion_tokens": s["completion_tokens"], "total_tokens": s["total_tokens"],
                  "wall_s": round(time.time() - t, 1), "note": note}
    print(f"  {name:<28} calls={s['n_calls']}  tokens={s['total_tokens']:>6}  {rows[name]['wall_s']}s")


TEXT = "I got into a huge fight with my roommate about the dishes and said cruel things. I'm too proud to apologize."
measure("talk_reply", lambda tr: pipeline.run(TEXT, profile=profile, with_matches=False, label="x")[1],
        "triage + counsel")
measure("crisis_reply", lambda tr: pipeline.run("I've been thinking everyone would be better off if I just wasn't here anymore.",
                                                 profile=profile, with_matches=False)[1], "triage only, then halts")
measure("sos_reply", lambda tr: pipeline.run("It's late, I'm alone with my phone and about to look at porn again.",
                                              profile=profile, with_matches=False, sos=True)[1], "brief, urgent counsel")
measure("belief_restatement", lambda tr: beliefs.simplify(
    "um so like I think women can teach and lead ministries but I'm unsure about the senior pastor role",
    question="Who may lead and teach in church?", trace=tr), "own-words approval loop")
measure("custom_struggle_category", lambda tr: triage.run("I keep comparing my career to my classmates and it makes me bitter", trace=tr),
        "suggests a category for a struggle in the user's words")

# history for check-ins and Awareness
for text, theme in [(TEXT, "anger"),
                    ("Work was brutal and I snapped at my mom on the phone. I take it out on whoever is closest.", "anger"),
                    ("A friend got the promotion I wanted and I couldn't congratulate her.", "envy")]:
    store.add_entry(pid, text, theme, "ordinary", 5)
store.add_checkin(pid, "envy", 4, "several", "slipping", "Kept checking her page.", "deeper", "", False)
profile = store.get_profile(pid)
profile["tradition"] = ["nondenom", "baptist"]


def checkin_conversation(tr):
    o = accountability.opener(profile, "anger", trace=tr)
    msgs = [{"role": "assistant", "content": o["text"]}]
    for said in ["Not yet. I keep avoiding him.", "It's been strong. I snapped again yesterday.", "Honestly I'm slipping."]:
        msgs.append({"role": "user", "content": said})
        r = accountability.chat_turn(profile, "anger", msgs, trace=tr)
        msgs.append({"role": "assistant", "content": r.get("reply", "")})
        if r.get("done"):
            break


measure("checkin_conversation", checkin_conversation, "personal opener + up to 3 turns")
measure("checkin_quick_no_note", lambda tr: accountability.assess(profile, "envy", 2, "none", "stronger", "", trace=tr),
        "three taps, trend by rule")
measure("awareness_reflection", lambda tr: awareness.build(profile, trace=tr), "one call, cached until new history")

Path("docs").mkdir(exist_ok=True)
Path("docs/feature_costs.json").write_text(json.dumps({"generated": time.strftime("%Y-%m-%d %H:%M"), "features": rows}, indent=1),
                                           encoding="utf-8")
print("wrote docs/feature_costs.json")
