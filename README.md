# Heard

> *"I don't understand, but I want to understand. That's why I need you God."*
> (motto from "Why God" by Austin French)

Heard is a Christian companion app. After a one-time setup about the user's tradition and beliefs, it offers a chat for hard questions that answers using **only the text of the Bible** and checks every verse it quotes; an **SOS button** for the moment of temptation; **check-ins** that remember what you said; an **Awareness** reflection built from your own history; **fellowship** with people who have come through the same struggle; and a **church finder**.

Heard is not a counselor, pastor, or crisis service. When someone describes real danger, it stops and points them to people who can help.

MGT 409 final project · Alexandria Rosenzweig · public repository: https://github.com/arrosenzweig/heard

---

## Run it (about 3 minutes)

Needs **Python 3.10+**.

```bash
git clone https://github.com/arrosenzweig/heard.git
cd heard
python -m venv .venv
```

Activate the environment:

```bash
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

Install and add a key:

```bash
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in **one** key:

| Key | Model used |
|---|---|
| `PORTKEY_API_KEY` (course key) | `gpt-6-luna` |
| `ANTHROPIC_API_KEY` | `claude-opus-5-5` (written, not yet measured) |
| `OPENAI_API_KEY` | `gpt-4o-mini` |

The app looks for `.env` in the repo folder first, then in the folder above it.

```bash
python app.py
```

Open **http://127.0.0.1:5000** in **Chrome or Edge** (needed for voice; Edge has the most natural voices).

On every start Heard makes sure the labelled demo data exists: **14 demo people and 8 demo churches** in Raleigh, NC. All are fictional and marked "demo". The full Bible (three public-domain translations, about 98,000 verses) ships in the repo.

### Try it

1. **Setup** asks once: *I'm here for myself* or *I'm setting up a church*. A person gives name, city, **age (required)**, optional gender, marital status and years as a Christian, one or more traditions (or "exploring"), the 14 contested questions **one at a time** (or *say it in your own words*, then approve Heard's restatement), the struggles they're fighting or came through (plus *Something else?* in their own words, by voice), and how often to check in.
2. **Talk** is home. Ask a hard question or describe what's pulling at you. 🔊 reads a reply aloud; **🎧 Voice mode** makes it a spoken conversation.
3. **I'm being tempted right now** (the red button) shows verses instantly, asks what's going on, and answers with one thing to do in the next ten minutes.
4. **Check-ins**: *Talk it through* (a short conversation that recalls what you said) or *Quick check-in* (three taps). Trends are charted.
5. **Awareness**: a reflective profile from your own history. It needs a few conversations or check-ins to become personal.
6. **Fellowship**: *Suggested fellowships · Find people · Your connections*, with filters. In *Your connections*, **Try with demo connections** adds two demo connections (Call / Text) and one demo request to accept. Fellowship is adults only.
7. **Churches**: ranked by match %, with the reasons shown.
8. **Settings**: edit your profile, choose Heard's voice (▶ Listen, Select) and speaking speed, and **Start over as a new user**. *Under the hood* has a verse checker: try `Hezekiah 3:5`.

**Install it:** in Chrome or Edge, use *Install app*. Right-click (desktop) or long-press (Android) the icon for the **"I'm tempted"** shortcut, which opens SOS directly. Phones need Heard hosted online to reach it.

---

## How it works

Only these steps call a model: **triage** (every Talk, SOS and check-in message), **counsel**, **accountability** conversations, **Awareness**, and **belief restatement**. Retrieval, citation verification, people matching and church matching are deterministic code.

| File | Role |
|---|---|
| `agents/triage.py` | Theme, severity, scope; slang → biblical search terms; halts on crisis |
| `agents/retrieval.py` | BM25 over KJV, BSB, and Douay-Rheims (Catholic canon) |
| `agents/counsel.py` | Replies from inside the user's tradition; correction with love; brief SOS mode |
| `agents/verifier.py` | Removes citations that don't exist or don't match the text |
| `agents/accountability.py` | Check-ins: personal opener grounded in the user's own words, conversation, confirm-to-log, quick mode |
| `agents/awareness.py` | Reflective profile; evidence and privacy guards |
| `agents/beliefs.py` | Restates own-words convictions for the user to approve |
| `agents/matcher.py` | Fellowship matching, filters, adults-only gate; church match % |
| `agents/pipeline.py` | Wires triage → retrieval → counsel → verifier → matcher |

### Reproduce the evaluations

```bash
python eval_safety.py     # crisis routing, verifier, guard tests        -> docs/safety_eval.json
python eval_compare.py    # plain chatbot vs Heard, retrieval depth       -> docs/compare_eval.json
python eval_costs.py      # measured cost of each feature                 -> docs/feature_costs.json
python build_report.py    # rebuilds report.html and slides.html from those files and the database
```

The first three make real model calls.

## Never commit

`.env`, or any real API key. `.gitignore` already excludes `.env` and the local database.
