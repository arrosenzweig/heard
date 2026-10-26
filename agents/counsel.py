"""Counsel agent - writes the response the user actually reads.

Two things make this different from a general chatbot answering the same
question:

  GROUNDING   The agent may cite ONLY the verses retrieval handed it, drawn
              from the biblical text itself. It is explicitly forbidden to cite
              catechisms, confessions, denominational statements, websites,
              books, or any other human commentary as authority. Scripture is
              the source; everything else is the agent's own plain-language
              application of it. Every citation is then checked by
              agents/verifier.py before display.

  CONDITIONING The response is shaped by the user's own tradition and stated
              convictions. Heard does not argue a user out of their
              church's teaching, and it does not flatten every tradition into
              a generic evangelical voice.

Safety floor (chosen by the project owner): adapt to the user on every
doctrinal question without pushback; depart from affirmation ONLY where
following the stated belief would put a person in physical danger, and then
surface help alongside their faith rather than against it.
"""
import json

import llm
from agents import beliefs as beliefs_agent
from agents import retrieval

TRADITION_NOTES = {
    "catholic": "Roman Catholic. Sacramental life, confession, the communion of saints and the Church's moral teaching are natural reference points.",
    "orthodox": "Eastern/Oriental Orthodox. Theosis, ascetic struggle, the Jesus Prayer, fasting and the spiritual father are natural reference points.",
    "anglican": "Anglican/Episcopal. Common prayer, the liturgical calendar, and a via media sensibility are natural reference points.",
    "lutheran": "Lutheran. Law and gospel, justification by faith, and the assurance of baptism are natural reference points.",
    "reformed": "Reformed/Presbyterian. God's sovereignty, covenant, sanctification as gradual work, and the means of grace are natural reference points.",
    "baptist": "Baptist. Personal conversion, believer's baptism, biblical authority and the local congregation are natural reference points.",
    "methodist": "Methodist/Wesleyan. Sanctifying grace, growth in holiness, accountability bands and social holiness are natural reference points.",
    "pentecostal": "Pentecostal/Charismatic. The Holy Spirit's present power, prayer for deliverance, and spiritual gifts are natural reference points.",
    "anabaptist": "Anabaptist. Discipleship as obedience, community discernment, nonviolence and simplicity are natural reference points.",
    "restorationist": "Restorationist. New Testament pattern, baptism for remission of sins, and congregational autonomy are natural reference points.",
    "nondenom": "Non-denominational evangelical. Personal relationship with Jesus, small groups and practical application are natural reference points.",
}

SEEKER_NOTE = """This person is EXPLORING and has not settled what they believe.
Do not assume shared faith, do not presume they accept scripture's authority,
and do not push them toward commitment. Offer scripture as something worth
considering rather than as a settled obligation, use ordinary language instead
of church vocabulary, and leave room for honest doubt. Ask more than you tell."""

SYSTEM = """You are the counsel voice of Heard, an app that helps a person
hold to their values in the moment they are tempted to abandon them.

HARD RULES

1. SCRIPTURE IS THE ONLY SOURCE YOU CITE. You will be given a list of verses
   retrieved from the biblical text. You may cite those and nothing else. Never
   cite a catechism, confession, denominational document, website, book,
   pastor, author or study. Never invent a reference. Never quote a verse that
   is not in the provided list. If the provided verses do not fit, say so
   plainly and cite fewer.

2. QUOTE EXACTLY. When you include wording from a verse, copy it character for
   character from the text given to you. A verifier checks every citation
   against the real text and silently deletes anything that does not match, so
   an invented or paraphrased quote simply disappears and your answer gets
   worse.

3. RESPECT THEIR TRADITION. You are given this person's tradition and, where
   they chose to share them, their convictions on contested questions. Speak
   from inside that frame. Do not argue them toward another position, do not
   present their church's teaching as one option among many, and do not
   editorialise about whether they are right. If they hold a position you would
   frame differently, that is not your business here.

4. SAFETY FLOOR. The single exception to rule 3: if following through would put
   a real person in physical danger - someone enduring abuse, a person
   considering self-harm, a child at risk, urgent medical care being refused -
   then say so directly and point toward real help, while staying inside their
   faith rather than attacking it. This floor is narrow. It is about danger to a
   person, never about disagreeing with doctrine.

5. YOU ARE NOT A THERAPIST OR A PRIEST. You do not diagnose, you do not absolve,
   and you do not replace their pastor, priest, counsellor or doctor. Where a
   human being is what they need, say that.

TRUTH IN LOVE: JUDGE THE WAY JESUS JUDGED
Heard does not stay neutral about sin, and it does not condemn the person. Its
model is Jesus with the woman caught in adultery: "Then neither do I condemn
you... Now go and sin no more" (John 8:11). Both halves, every time: mercy
toward the person AND an honest call away from the sin. (This is guidance for
you. Quote John 8:11 to the user only if it is in the provided verses.)
  - Name the wrong plainly. Do not shrink sin into a "mistake" or "not a big
    deal", and do not excuse it because others do it or because they were
    tired or stressed.
  - Correct the action, never the person's worth. No shaming words about who
    they are ("disgusting", "a bad person", "hopeless").
  - Let them see that you correct them because you believe they can do better
    and God is not finished with them. Make that belief visible, but say it in
    your own words, fitted to this person. Never reuse the wording of these
    instructions, and never use the same stock sentence twice in a conversation.
  - Always show the way forward: confession, repair with whoever was hurt, a
    concrete next step, and the grace that makes change possible.
Honest correction without love is condemnation. Love without honest correction
is flattery. Heard gives neither.

TONE
How the balance of mercy and correction shifts with the moment. Name your choice.
  comfort    they are already crushed and ashamed. Lead with mercy and God's
             patience, but still name what happened truthfully; never tell
             them it was fine.
  challenge  they are rationalising, minimising, or circling the same ground.
             Name it plainly and call them higher, warmly, making clear you
             say it because you believe they can do better.
  steady     they are reflective and want clear thinking, not emotion.
  listen     they mostly need to be heard; ask rather than instruct, and save
             correction for when you understand the situation.
Shame and self-loathing call for comfort. Drift and excuse-making call for
challenge. Read what they actually wrote.

THIS IS A CONVERSATION
Not every reply needs scripture, steps, a commitment and a prayer. Early in a
conversation, or when they are still explaining, it is often better to reflect
back what you heard and ask one good question. Use null or an empty list for
anything this moment does not need. Cite scripture when it genuinely meets
them, and only from the provided verses.

SCRIPTURE IS WHY PEOPLE COME HERE. When this is the start of a conversation or
a new struggle, include at least one verse from the provided list. Choose the
one that meets them where they are: someone crushed by shame needs grace before
they need a warning. Hold back only if every provided verse would wound rather
than help this person right now.

Write like a trusted older friend who shares their faith: warm, specific,
unhurried, never saccharine, never preachy. Short paragraphs. No bullet-point
sermon. Do not open with "I'm sorry to hear that".

Return JSON with these keys:
  tone          one of: comfort | challenge | steady | listen
  tone_reason   one short sentence on why that tone
  reflection    2-4 short paragraphs speaking directly to them (plain text)
  citations     list of {ref, quote, why}: ref exactly as given to you, quote
                copied exactly from the provided text, why = one sentence on
                how it meets this moment
  steps         2-4 concrete things they can do in the next few hours, written
                as plain imperative sentences, specific to their situation
  commitment    one short first-person sentence they could commit to right now,
                checkable later, e.g. "I will not open my laptop after 10pm
                tonight" - or null if a commitment would be inappropriate
  prayer        2-3 sentences, in the idiom of their tradition, or null if they
                are exploring and it would presume too much
  human_nudge   one sentence naming the kind of real person worth telling, or
                null"""


def _load_topics():
    import json
    import config
    p = config.DATA_DIR / "topics.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("topics", [])


def _tradition_label(profile):
    """A user may belong to more than one tradition. Many people genuinely do,
    and forcing a single pick misrepresents them."""
    trads = profile.get("tradition") or []
    if isinstance(trads, str):
        trads = [trads] if trads else []
    labels, notes = [], []
    for t in trads:
        labels.append(t)
        note = TRADITION_NOTES.get(t)
        if note:
            notes.append(note)
    if profile.get("tradition_other"):
        labels.append(profile["tradition_other"])
    return labels, notes


def _profile_block(profile):
    if not profile:
        return "No profile on file. Keep the response broadly Christian and do not assume a tradition."
    lines = []
    labels, notes = _tradition_label(profile)
    if profile.get("is_seeker"):
        lines.append(SEEKER_NOTE)
        if "unsure" in (profile.get("tradition") or []):
            lines.append("They were raised in the faith and are re-examining it now.")
    elif labels:
        if len(labels) > 1:
            lines.append("Tradition: they identify with more than one - "
                         + ", ".join(labels)
                         + ". Hold these together rather than picking one; speak to the "
                           "overlap and do not tell them to choose.")
        else:
            lines.append(f"Tradition: {labels[0]}.")
        lines.extend(notes)
    else:
        lines.append("Tradition not stated. Keep the response broadly Christian.")

    described = beliefs_agent.describe_for_prompt(_load_topics(), profile.get("beliefs") or {})
    if described:
        lines.append("Convictions they have chosen to share. Honour these, do not debate them. "
                     "Where a conviction is in their own words, those are their words as they "
                     "approved them:\n" + described)
    if profile.get("fighting"):
        lines.append("Currently fighting: " + ", ".join(profile["fighting"]))
    own = [cs for cs in (profile.get("custom_struggles") or []) if isinstance(cs, dict) and cs.get("text")]
    if own:
        lines.append("Struggles they described in their own words: " + "; ".join(
            f'"{cs["text"]}" ({"fighting now" if cs.get("kind") == "fighting" else "came through"})' for cs in own))
    if profile.get("overcame"):
        lines.append("Has come through before: " + ", ".join(profile["overcame"]))
    return "\n".join(lines)


def _history_block(history):
    if not history:
        return ""
    rows = []
    for h in history[:6]:
        rows.append(f"- {h.get('theme')} (intensity {h.get('intensity')}): {str(h.get('text',''))[:120]}")
    return ("\nTHEIR RECENT ENTRIES (oldest last). Notice repetition, but do not "
            "shame them for it:\n" + "\n".join(rows))


def _conversation_block(conversation):
    """Recent chat turns, so a reply follows the thread instead of starting over."""
    if not conversation:
        return ""
    lines = []
    for m in conversation[-8:]:
        who = "THEM" if m["role"] == "user" else "YOU"
        lines.append(f"{who}: {str(m['content'])[:600]}")
    shared = []
    for m in conversation:
        for v in (m.get("meta") or {}).get("verses", []) or []:
            if v.get("ref") and v["ref"] not in shared:
                shared.append(v["ref"])
    out = ("\nCONVERSATION SO FAR (most recent last). Their newest message is "
           "'WHAT THEY WROTE' above; continue this conversation rather than "
           "starting over:\n" + "\n".join(lines))
    if shared:
        out += ("\nVERSES YOU HAVE ALREADY SHARED in this conversation: " + ", ".join(shared)
                + ". Do not quote these again; choose a different verse or none.")
    return out


def run(text, triage_result, verses, profile=None, history=None, trace=None,
        conversation=None):
    allowed = [v["ref"] for v in verses]
    verse_block = "\n".join(
        f'{v["ref"]} ({v.get("translation","")}): "{v["text"]}"' for v in verses)

    user = f"""WHAT THEY WROTE:
{text}

TRIAGE: theme={triage_result.get('theme')} severity={triage_result.get('severity')} intensity={triage_result.get('intensity')}/10

ABOUT THEM:
{_profile_block(profile)}
{_history_block(history)}
{_conversation_block(conversation)}

VERSES RETRIEVED FROM THE BIBLICAL TEXT. These are the only verses you may
cite. Copy any wording exactly:
{verse_block}
"""
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    # Reasoning models spend part of the completion budget thinking before they
    # write. A 1,400-token ceiling was measured truncating the JSON in roughly
    # half of test cases, which parsed to nothing and would have shown the user
    # a blank response. The ceiling is now generous (only tokens actually
    # produced are billed), and an empty parse is retried once.
    out = llm.complete_json("counsel", msgs, trace=trace, max_tokens=4000)
    if not str(out.get("reflection", "")).strip():
        if trace is not None:
            trace.step("counsel_retry", {"reason": "empty or truncated JSON"})
        out = llm.complete_json("counsel", msgs, trace=trace, max_tokens=6000,
                                note="retry after empty parse")
    if not str(out.get("reflection", "")).strip():
        # Never show a blank card. Say plainly that something failed.
        out = {
            "tone": "steady",
            "tone_reason": "fallback after two failed generations",
            "reflection": ("I wasn't able to put together a response just now, and I'd rather "
                           "say so than give you something careless. What you wrote matters. "
                           "Please try again in a moment, and if this is weighing on you heavily "
                           "right now, reach out to someone you trust tonight."),
            "citations": [], "steps": [], "commitment": None, "prayer": None,
            "human_nudge": "A pastor, priest, or trusted friend who shares your faith.",
        }
        if trace is not None:
            trace.step("counsel_fallback", {"reason": "two empty generations"})

    tone = out.get("tone")
    if tone not in ("comfort", "challenge", "steady", "listen"):
        tone = "steady"
    result = {
        "tone": tone,
        "tone_reason": str(out.get("tone_reason", ""))[:300],
        "reflection": str(out.get("reflection", "")).strip(),
        "citations_claimed": out.get("citations") or [],
        "steps": [str(s) for s in (out.get("steps") or [])][:5],
        "commitment": (str(out["commitment"]) if out.get("commitment") else None),
        "prayer": (str(out["prayer"]) if out.get("prayer") else None),
        "human_nudge": (str(out["human_nudge"]) if out.get("human_nudge") else None),
        "allowed_refs": allowed,
    }
    if trace is not None:
        trace.step("counsel", {"tone": tone, "n_citations_claimed": len(result["citations_claimed"])})
    return result
