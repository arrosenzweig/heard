# Heard

> *"I don't understand, but I want to understand. That's why I need you God."*

Heard is a companion for the moment you are tempted to abandon what you believe. You tell it what is pulling at you; it answers from inside **your own** Christian tradition, using **only the text of the Bible**, checks every verse it quotes against the real text, and connects you with someone who has **come through the same struggle**, the way a sponsor walks with someone in recovery.

Heard is not a counselor, pastor, or crisis service. When someone describes real danger, it stops and points them to people who can help.

MGT 409 final project · Alexandria Rosenzweig · [github.com/arrosenzweig](https://github.com/arrosenzweig)

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
| `ANTHROPIC_API_KEY` | `claude-opus-5-5` |
| `PORTKEY_API_KEY` (course key) | `gpt-6-luna` |
| `OPENAI_API_KEY` | `gpt-4o-mini` |

The app looks for `.env` in the repo folder first, then in the folder above it, so a class-wide `.env` one level up also works.

```bash
python app.py
```

Open **http://127.0.0.1:5000**.

The first launch loads clearly labelled demo profiles (Raleigh, NC) so the People and Churches tabs are not empty. The full Bible (three translations, about 98,000 verses) ships in the repo, so there is nothing else to download.

### Try it

1. **Setup (first launch only).** Heard asks your name and city, your tradition (one or more, or "exploring"), where you stand on contested questions (all optional; pick *"Let me say it in my own words"* to type or speak, then approve Heard's restatement), the struggles you're fighting or have come through, and how often to check in. It never asks about faith again; change anything in **Settings**.
2. **Talk** - a conversation. Ask a hard question or describe what's pulling at you. Replies come from inside your tradition, quote verses marked ✓ verified, and may offer a commitment and people who came through the same thing. 🔊 reads a reply aloud.
3. **Check-ins** - three quick questions per struggle on your schedule, plus an optional note. Heard names the trend (overcoming, holding, deeper) and charts it. Two "deeper" in a row and it points you to people and a church. Commitments from Talk are followed up here too.
4. **Fellowship** - people who came through your struggle rank first. Contact details unlock only after both of you accept.
5. **Churches** - find a church by city, or register one.
6. **Settings → Under the hood** - measured token counts and a verse checker. Try `Hezekiah 3:5`: it does not exist, and the verifier says so. **Start over as a new user** lets you run setup again.

Every text box has a 🎙 button. Voice uses the browser's built-in speech recognition (Chrome or Edge); in Chrome, audio is sent to Google to be transcribed.

---

## How it works

```
entry ─▶ TRIAGE ──crisis?──▶ STOP · real help (988, DV hotline, 911)
           │
           ▼
        RETRIEVAL      BM25 over the full Bible             (no model call)
           │
           ▼
        COUNSEL        your tradition, your convictions, adaptive tone
           │
           ▼
        VERIFIER       every citation checked against the text (no model call)
           │
           ▼
        MATCHER        people who came through this struggle (no model call)
```

Only **triage** and **counsel** call a model. Retrieval, verification and matching are deterministic code, which keeps cost down and keeps the safety-critical steps auditable.

| File | Role |
|---|---|
| `agents/triage.py` | Classifies theme, severity and scope; translates slang into biblical vocabulary for search; halts on crisis |
| `agents/retrieval.py` | BM25 search across KJV, BSB, and Douay-Rheims (Catholic canon, with deuterocanonical books) |
| `agents/counsel.py` | Writes the response from inside the user's tradition, citing only retrieved verses |
| `agents/verifier.py` | Removes citations that do not exist or whose wording does not match the text |
| `agents/beliefs.py` | Restates own-words convictions for the user to approve before they are used |
| `agents/accountability.py` | Scheduled check-ins per struggle; names the trend; escalates to people after two "deeper" in a row |
| `agents/matcher.py` | Ranks companions by lived experience of the same struggle, then tradition and city |
| `agents/pipeline.py` | Wires the steps together and records a trace of every call |

### Data

- **Scripture:** public-domain translations from [scrollmapper/bible_databases](https://github.com/scrollmapper/bible_databases), normalized by `build_bible.py` into `data/bible/`. The Bible is the only content source; Heard does not draw on commentary, websites or devotional writing.
- **Anchor verses:** `data/verses.json`, 154 hand-picked verses for common struggles (fetched with `build_corpus.py`), used only as a search-ranking prior.
- **Demo people and churches:** `seed_demo.py`. Every one is synthetic, marked `demo` in the interface, and uses 555-01xx fictional phone numbers. None is a real person or congregation.

### Reproduce the evaluations

```bash
python eval_safety.py     # crisis routing + verifier vs fabricated citations -> docs/safety_eval.json
python eval_compare.py    # plain chatbot vs Heard, and retrieval-depth ablation -> docs/compare_eval.json
python build_report.py    # rebuilds report.html and slides.html from those files and the database
```

Both make real model calls and cost real tokens.

---

## Safety design

- **Crisis halts the pipeline.** Suicidal thoughts, self-harm, abuse, a child in danger, or a medical emergency get hand-written guidance and real US hotlines. No verse, no advice, no peer matches.
- **The safety floor is narrow on purpose.** Heard adapts to every doctrinal position without arguing. It departs from affirmation only when following a belief would put a person in physical danger.
- **Every quoted verse is verified** against the biblical text before display, and the app always shows the real text, never the model's version of it.
- **Own-words beliefs need approval.** A paraphrase the user has not confirmed is never used.
- **Contact details unlock only after both people agree.** Browsing a profile never exposes a phone number. Location is city-level only.
- **Churches register themselves.** Heard never invents a congregation.

## Limits

See the report for the full list. In short: crisis resources are US-only; the safety evaluation uses written test cases rather than real crisis messages; there is no moderation of user-to-user contact beyond accept, decline and block; and the outcome data comes from a single user.

## Never commit

`.env`, or any real API key. `.gitignore` already excludes `.env` and the local database.
