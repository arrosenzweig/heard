"""Baseline comparison and token ablation - evidence for "what is new" and
for the tokenomics section.

  PART A  BASELINE vs HEARD. The same struggles go to (1) a plain chatbot
          prompt asking for Christian advice with scripture, and (2) the full
          Heard pipeline. Every citation from both is checked against the
          biblical text.

          Fairness: a baseline quote is checked against ALL THREE public-domain
          translations and the best match is kept. A quote that matches none
          is reported as UNVERIFIABLE, not fabricated, because it may be real
          wording from a copyrighted translation (NIV, ESV) that is not in the
          corpus. Only a reference that does not exist in any translation is
          counted as FABRICATED.

  PART B  ABLATION. How many retrieved verses to hand the counsel agent
          (k = 3, 6, 12). Measures the token cost of context size against how
          many citations survive verification.

Fixture inputs are written for this evaluation; they are not user data.

Run:  python eval_compare.py    -> docs/compare_eval.json
"""
import os

# Every evaluation call must be independent. The Portkey gateway otherwise
# replays cached responses, which turns repeat runs into copies of run one.
os.environ.setdefault("HEARD_FORCE_FRESH", "1")

import json
import time
from pathlib import Path

import llm
from agents import pipeline, retrieval, verifier

CASES = [
    "I keep looking at porn late at night and feel disgusting afterwards.",
    "Everyone at work fudges the numbers and I feel stupid for being honest.",
    "My best friend just got engaged and I can't be happy for her. I'm so jealous.",
    "I'm not sure God is even listening anymore. Prayer feels like talking to a wall.",
    "I've been drinking alone most nights and hiding the bottles from my wife.",
    "I told a secret my friend trusted me with and now the whole small group knows.",
    "I snapped at my mom on the phone again and hung up on her.",
    "I can't forgive my dad for leaving. Every time someone says I should, I get angrier.",
]

PROFILE = {"id": "eval", "tradition": ["nondenom", "baptist"], "is_seeker": 0,
           "beliefs": {}, "fighting": [], "overcame": [], "city": "Raleigh, NC"}

BASELINE_SYSTEM = """You are a helpful Christian advisor. Give compassionate,
practical advice for the person's struggle. Include 2-4 relevant Bible verses.
Return JSON: {"advice": "...", "citations": [{"ref": "Book C:V", "quote": "exact verse text"}]}"""

CODES = ["BSB", "KJV", "DRC"]


def best_check(cite):
    """Check one citation against every translation; keep the best result."""
    best = None
    for code in CODES:
        _, rep = verifier.verify_citations([cite], allowed_refs=[cite.get("ref", "")], code=code)
        chk = rep["checks"][0]
        score = (chk["exists"], chk["fidelity"] or 0)
        if best is None or score > best[0]:
            best = (score, chk, code)
    _, chk, code = best
    if not chk["exists"]:
        status = "fabricated"
    elif chk["verdict"] == "removed":
        status = "unverifiable_wording"
    else:
        status = "verified"
    return {"ref": cite.get("ref"), "status": status, "fidelity": chk["fidelity"], "best_translation": code}


def run_baseline(text, trace):
    out = llm.complete_json("baseline", [
        {"role": "system", "content": BASELINE_SYSTEM},
        {"role": "user", "content": text}], trace=trace, max_tokens=900)
    cites = out.get("citations") or []
    return [best_check(c) for c in cites if isinstance(c, dict)]


def part_a():
    rows = []
    t_base, t_heard = llm.Trace(label="baseline"), None
    heard_tokens = []
    for text in CASES:
        base = run_baseline(text, t_base)
        res, tr = pipeline.run(text, profile=PROFILE, with_matches=False, label="heard")
        heard_tokens.append(tr["total_tokens"])
        v = res.get("verification") or {}
        rows.append({
            "text": text,
            "baseline": base,
            "heard": {"mode": res["response"]["mode"],
                      "claimed": v.get("n_claimed", 0), "shown": v.get("n_kept", 0),
                      "removed": v.get("n_removed", 0),
                      "shown_refs": [x["ref"] for x in res.get("verses", [])],
                      "tokens": tr["total_tokens"]},
        })
        b = {s: sum(1 for c in base if c["status"] == s) for s in ("verified", "unverifiable_wording", "fabricated")}
        print(f"  base {len(base)} cites {b} | heard shown {v.get('n_kept',0)}/{v.get('n_claimed',0)} removed {v.get('n_removed',0)}  -- {text[:44]}")
    bs = t_base.summary()
    all_b = [c for r in rows for c in r["baseline"]]
    summary = {
        "cases": len(CASES),
        "baseline_citations": len(all_b),
        "baseline_verified": sum(1 for c in all_b if c["status"] == "verified"),
        "baseline_unverifiable_wording": sum(1 for c in all_b if c["status"] == "unverifiable_wording"),
        "baseline_fabricated_refs": sum(1 for c in all_b if c["status"] == "fabricated"),
        "baseline_tokens_total": bs["total_tokens"],
        "baseline_tokens_per_case": round(bs["total_tokens"] / len(CASES), 1),
        "heard_citations_claimed": sum(r["heard"]["claimed"] for r in rows),
        "heard_citations_shown": sum(r["heard"]["shown"] for r in rows),
        "heard_citations_removed": sum(r["heard"]["removed"] for r in rows),
        "heard_unverified_shown": 0,  # by construction: only verified citations reach the UI
        "heard_tokens_total": sum(heard_tokens),
        "heard_tokens_per_case": round(sum(heard_tokens) / len(CASES), 1),
    }
    return rows, summary


def part_b(ks=(3, 6, 12), n_cases=4):
    out = []
    for k in ks:
        toks, prompt_toks, shown, claimed, wall = [], [], 0, 0, []
        for text in CASES[:n_cases]:
            res, tr = pipeline.run(text, profile=PROFILE, k_verses=k, with_matches=False, label=f"k{k}")
            toks.append(tr["total_tokens"])
            prompt_toks.append(tr["by_agent"].get("counsel", {}).get("prompt_tokens", 0))
            v = res.get("verification") or {}
            shown += v.get("n_kept", 0)
            claimed += v.get("n_claimed", 0)
            wall.append(tr["wall_s"])
        row = {"k": k, "cases": n_cases,
               "avg_total_tokens": round(sum(toks) / n_cases, 1),
               "avg_counsel_prompt_tokens": round(sum(prompt_toks) / n_cases, 1),
               "citations_shown": shown, "citations_claimed": claimed,
               "avg_wall_s": round(sum(wall) / n_cases, 2)}
        out.append(row)
        print(f"  k={k:<3} avg tokens {row['avg_total_tokens']:>7}  counsel prompt {row['avg_counsel_prompt_tokens']:>7}  "
              f"citations shown {shown}/{claimed}  wall {row['avg_wall_s']}s")
    return out


def main():
    t0 = time.time()
    print("PART A - plain chatbot baseline vs Heard\n")
    rows, summary = part_a()
    print("\n", json.dumps(summary, indent=1))
    print("\nPART B - retrieval depth ablation\n")
    abl = part_b()
    Path("docs").mkdir(exist_ok=True)
    Path("docs/compare_eval.json").write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%d %H:%M"), "summary": summary,
        "part_a": rows, "ablation": abl, "wall_s": round(time.time() - t0, 1)}, indent=1), encoding="utf-8")
    print(f"\nwrote docs/compare_eval.json in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
