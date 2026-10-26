"""Triage agent - the first thing every entry hits.

Decides three things before any counsel is generated:
  theme      which struggle this is, from the curated corpus themes
  severity   ordinary | elevated | crisis
  scope      in_scope | out_of_scope (medical, legal, financial advice)

The safety floor implemented here is the one chosen for this project:
Heard adapts to the user's tradition on every doctrinal question and never
argues them out of their church's teaching. It departs from pure affirmation
ONLY where following the stated belief would endanger a person - abuse being
endured in silence, self-harm, withheld urgent medical care. In those cases the
app surfaces help ALONGSIDE the person's faith, never as an attack on it.

On crisis the pipeline stops. No verse, no counsel, no matching - a person in
acute danger gets routed to humans who can actually help.
"""
import json

import llm
from agents import retrieval

# Real, published US crisis resources. Numbers are verifiable and must never be
# invented or altered. Non-US users are told plainly that these are US lines.
CRISIS_RESOURCES = {
    "suicidal": [
        {"name": "988 Suicide & Crisis Lifeline", "contact": "Call or text 988", "where": "US"},
        {"name": "Crisis Text Line", "contact": "Text HOME to 741741", "where": "US"},
    ],
    "self_harm": [
        {"name": "988 Suicide & Crisis Lifeline", "contact": "Call or text 988", "where": "US"},
        {"name": "Crisis Text Line", "contact": "Text HOME to 741741", "where": "US"},
    ],
    "abuse_victim": [
        {"name": "National Domestic Violence Hotline", "contact": "Call 1-800-799-7233 or text START to 88788", "where": "US"},
        {"name": "Emergency services", "contact": "Call 911 if you are in immediate danger", "where": "US"},
    ],
    "child_endangerment": [
        {"name": "Childhelp National Child Abuse Hotline", "contact": "Call or text 1-800-422-4453", "where": "US"},
        {"name": "Emergency services", "contact": "Call 911 if a child is in immediate danger", "where": "US"},
    ],
    "medical_emergency": [
        {"name": "Emergency services", "contact": "Call 911", "where": "US"},
    ],
    "harm_to_others": [
        {"name": "Emergency services", "contact": "Call 911", "where": "US"},
        {"name": "988 Suicide & Crisis Lifeline", "contact": "Call or text 988", "where": "US"},
    ],
}

CRISIS_KINDS = list(CRISIS_RESOURCES)

SYSTEM = """You are the triage step of a Christian discipleship support app.
You do not counsel. You classify, and you err toward caution.

Return JSON with exactly these keys:
  theme              one of: {themes}
  severity           "ordinary" | "elevated" | "crisis"
  crisis_kind        one of: {crisis} , or null
  intensity          integer 1-10, how acute the distress sounds
  scope              "in_scope" | "out_of_scope"
  endangerment       true only if following through would put a person in physical danger
  search_terms       8-12 single words likely to appear in the BIBLE ITSELF for
                     this struggle, as a space-separated string
  rationale          at most 12 words

search_terms is a scripture-vocabulary translation of what the person said. A
lexical index searches the biblical text with these words, so modern or slang
phrasing must be converted into the vocabulary scripture actually uses.
  "can't stop watching porn"  -> lust eyes flesh adultery heart purity desire
                                 temptation chaste defile covet
  "I blew up at my wife"      -> anger wrath temper quarrel strife tongue
                                 patience soft gentle rage
  "everyone at work cheats"   -> honest weights deceit gain integrity upright
                                 righteous wicked bribe just
Use no modern words that do not occur in scripture. Do not include the person's
own slang. Give the plain word, not a phrase.

Severity rules:
  crisis    - suicidal thoughts, intent to self-harm, being abused, abusing
              someone, a child in danger, or a medical emergency. If there is
              any real signal of these, choose crisis.
  elevated  - serious ongoing distress, addiction, despair, but no acute danger.
  ordinary  - everyday temptation, moral struggle, doubt, conflict.

scope is "out_of_scope" for requests that are really medical, legal, or
financial advice rather than spiritual or moral struggle.

Classify only. Never write pastoral content.
Be terse. The whole JSON object must stay under 120 words."""


THEME_FALLBACK_TERMS = {
    "temptation": "temptation tempted flesh endure escape watch pray",
    "anger": "anger wrath temper strife quarrel rage patience gentle tongue",
    "lust_purity": "lust eyes flesh adultery heart purity desire chaste defile",
    "greed_money": "money riches covet gain mammon treasure greed content",
    "envy": "envy covet jealous neighbour grudge strife",
    "pride": "pride proud haughty humble lowly boast exalt",
    "lying_integrity": "lie lying truth deceit false witness honest upright",
    "gossip": "tongue slander talebearer whisper strife backbiting words",
    "addiction": "bondage servant slave deliver free strong drink flesh",
    "anxiety_fear": "fear afraid anxious care peace troubled cast",
    "loneliness": "alone forsake comfort near refuge dwell companion",
    "forgiveness": "forgive forgiven mercy trespass pardon reconcile",
    "bitterness": "bitter root wrath malice grudge avenge",
    "work_integrity": "honest weights deceit gain integrity upright just labour",
    "relationships": "love neighbour brother kind forbearing one another",
    "doubt": "doubt faith believe unbelief trust sign",
    "despair": "despair cast down soul hope comfort broken heart",
    "patience": "patience endure wait long suffering steadfast",
    "humility": "humble lowly meek submit exalt pride",
    "self_control": "temperance sober control flesh spirit discipline",
}


def _fallback_terms(theme):
    return THEME_FALLBACK_TERMS.get(theme, THEME_FALLBACK_TERMS["temptation"])


def run(text, trace=None):
    msg = [
        {"role": "system", "content": SYSTEM.format(
            themes=", ".join(retrieval.themes()), crisis=", ".join(CRISIS_KINDS))},
        {"role": "user", "content": text},
    ]
    out = llm.complete_json("triage", msg, trace=trace, max_tokens=1500)
    if not out.get("severity"):
        out = llm.complete_json("triage", msg, trace=trace, max_tokens=3000,
                                note="retry after empty parse")
    triage_failed = not out.get("severity")

    theme = out.get("theme")
    if theme not in retrieval.themes():
        theme = "temptation"
    severity = out.get("severity")
    if severity not in ("ordinary", "elevated", "crisis"):
        severity = "elevated"
    kind = out.get("crisis_kind")
    if kind not in CRISIS_KINDS:
        kind = None
    # Fail closed: a crisis label with no recognised kind still stops the pipeline.
    if severity == "crisis" and kind is None:
        kind = "suicidal"
    try:
        intensity = max(1, min(10, int(out.get("intensity", 5))))
    except (TypeError, ValueError):
        intensity = 5

    terms = out.get("search_terms") or ""
    if isinstance(terms, list):
        terms = " ".join(str(t) for t in terms)
    terms = " ".join(str(terms).split())[:400]
    if not terms:
        # Model gave nothing usable. Fall back to the theme's own anchor
        # vocabulary rather than letting retrieval run on raw slang.
        terms = _fallback_terms(theme)

    endangerment = bool(out.get("endangerment"))

    # Calibration, measured in testing: "I yelled at my kids and they flinched"
    # was sometimes labelled crisis/harm_to_others while the same call reported
    # endangerment=false, sending a parent who honestly confessed a bad night
    # to 911. That teaches people to stop being honest with the app.
    #
    # The fix is deliberately narrow. Kinds that concern the USER'S OWN life or
    # safety always stay crisis (fail closed). Only the outward-facing kinds are
    # softened, and only when the classifier itself says nobody is in physical
    # danger. Even then the resources are still shown, beside the counsel
    # rather than instead of it.
    safety_note = False
    if (severity == "crisis" and kind in ("harm_to_others", "child_endangerment")
            and not endangerment):
        severity = "elevated"
        safety_note = True

    if triage_failed:
        # Fail toward help. If classification failed twice we cannot know this
        # is NOT a crisis, so the person still gets counsel, but the crisis
        # resources are shown above it rather than silently omitted.
        severity = "elevated"
        safety_note = True
        kind = kind or "suicidal"

    result = {
        "triage_failed": triage_failed,
        "safety_note": safety_note,
        "theme": theme,
        "severity": severity,
        "crisis_kind": kind,
        "intensity": intensity,
        "search_terms": terms,
        "scope": "out_of_scope" if out.get("scope") == "out_of_scope" else "in_scope",
        "endangerment": endangerment,
        "rationale": str(out.get("rationale", ""))[:300],
        "resources": CRISIS_RESOURCES.get(kind, []) if (severity == "crisis" or safety_note) else [],
    }
    if trace is not None:
        trace.step("triage", result)
    return result
