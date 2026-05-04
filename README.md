# Capgemini Invent — Consulting Recruitment Assessment

A Streamlit prototype for assessing consulting candidates across three layers:

1. **Cognitive Assessment** — 30 timed reasoning questions (logical, numerical, verbal)
2. **Firm Simulation** — 8-week continuous resource management game with cash, reputation, fatigue, and a mid-simulation trade-off
3. **AI-Led Interview** — 5 voice-recorded questions with live AI-generated follow-ups

Candidates receive personalized feedback on completion. Per-layer scores are not shown to candidates between layers — only the full breakdown after all three are done. A password-protected recruiter dashboard lets the hiring team filter, review, and export results.

---

## Quick start

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure your API key

```bash
cp .env.example .env
# Then open .env and paste your OpenAI API key
```

The prototype uses Capgemini's Azure OpenAI resource (`jt-learning-openai-7382` in `swedencentral`). The deployment names, region, and API version are hard-coded in `assessment_logic/llm_client.py` (`CAPSTONE_CONFIG`). Specifically:

- `gpt-4-1-mini-qc` (gpt-4.1-mini) for interview follow-ups, rubric scoring, and feedback generation.
- `capstone-transcribe` (gpt-4o-mini-transcribe) for voice answer transcription.
- The Layer 3 questions are also read out loud to the candidate using browser-side Web Speech (no Azure call required).

Only the API key needs to live in secrets. There is no longer any deployment-name configuration to set.

### 3. Run the app

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. First launch initializes the SQLite database and prints the default recruiter credentials to the console.

### Recruiter login

The recruiter password is read from `RECRUITER_PASSWORD` in your Streamlit secrets (or `.env` for local dev). The username is always `recruiter`.

If `RECRUITER_PASSWORD` is not set, the app falls back to `changeme-local-dev` for local development only.

The password is synced to the database on every app start, so rotating the secret is enough — no manual DB edits needed.

---

## Customizing content

### Layer 1 question banks

The three themes live in `data/questions/`:

- `logical.xlsx`
- `numerical.xlsx`
- `verbal.xlsx`

Each file needs these columns (case-sensitive):

| question_id | question_text | option_a | option_b | option_c | option_d | correct_answer |
|-------------|---------------|----------|----------|----------|----------|----------------|

`correct_answer` must be `A`, `B`, `C`, or `D`. You can include as many questions as you want; the app samples 10 per theme per candidate using a deterministic seed based on the candidate ID.

Optional: to attach a chart or image to a question, place it at `data/charts/{question_id}.png`. The app will auto-display it.

The placeholder banks currently ship with 20 questions per theme. Replace them with your real content whenever you're ready. There's a generator script at the project root (`generate_placeholder_questions.py`) if you want to rebuild the placeholders.

### Layer 2 scenario

Edit `data/layer2_scenario.json`. The file has four top-level keys:

- `starting_state` — initial cash, reputation, total weeks
- `consultants` — the 6 consultants with skills, seniority, daily rates
- `projects` — the project pool with availability windows, durations, weekly burn, revenue, deadlines
- `weekly_events` — pre-scripted disruptions (sick leave, budget cuts, new project alerts, trade-off trigger)
- `tradeoff` — the Week 6 trade-off scenario with 4 options and their scores
- `scoring_constants` — fatigue rates, quality penalties, reputation effects

Keep the IDs stable (`C1-C6`, `P1-P8`). The scoring code uses them.

### Layer 3 questions

Edit `data/interview_questions.txt`. One question per line. The app samples 5 per candidate with a deterministic seed.

---

## Scoring

**Layer weights in the overall score:**
- Layer 1: 30%
- Layer 2: 35%
- Layer 3: 35%

**Layer 2 score (deterministic, no AI):**
- Outcome score: 70% (final cash, reputation, projects completed, fatigue management)
- Process score: 30% (constraint compliance and skill match across all 8 weeks)

**Top Fit flag** (shown only to recruiters) requires all three:
- Overall score ≥ 70
- No single layer below 60
- At least 2 competencies ≥ 75

All scoring logic is in `assessment_logic/`. Regression tests in `tests/test_scoring.py` cover 29 test cases including the simulation engine.

**Candidates do not see scores between layers.** They see "Layer N complete, moving on" and only the full breakdown after all three layers are done.

---

## Running tests

```bash
pytest tests/
```

These tests cover pure scoring logic only. They do not call the OpenAI API or touch the database.

---

## Known limitations

- **Single concurrent session per candidate.** If the same candidate opens two tabs, the last write wins. Built for a pilot, not for scale.
- **Layer 2 mid-simulation resume restarts from Week 1.** The firm sim is continuous and intra-layer state isn't checkpointed weekly. Once a candidate finishes Layer 2, their final result is persisted and they can resume into Layer 3. To support mid-Layer-2 resume, add weekly state writes to a new DB table.
- **Layer 3 audio is not persisted.** Only the transcript is saved. If you need audio files for training or review, wire up file storage in `views/layer3.py`.
- **No PDF export.** The dashboard exports CSV only. PDF candidate reports would need something like `reportlab` bolted on to `views/candidate_results.py`.
- **Microphone permission required.** Browsers will ask for mic access on Layer 3. A typed fallback is always available.
- **Desktop-first.** Mobile layouts work but are not the priority.
- **LLM scoring is approximately reproducible.** Even at `temperature=0`, OpenAI has minor infrastructure-level variance in outputs. The rubric scoring includes a JSON-extraction fallback for robustness.
- **Password hashing is SHA-256, not bcrypt.** Fine for a pilot with one recruiter account. Swap for bcrypt before real deployment.
- **Per-question timer uses polling.** `streamlit-autorefresh` re-renders every second, which causes a visible flicker. Acceptable for a pilot; for production consider a JS component.

---

## Project layout

```
recruitment_prototype/
├── app.py                      # entry point, routing
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── generate_placeholder_questions.py
├── recruitment.db              # SQLite, created at first run
│
├── database/
│   ├── __init__.py
│   ├── db.py                   # CRUD helpers, auth
│   └── schema.sql              # 6 tables
│
├── assessment_logic/
│   ├── __init__.py
│   ├── llm_client.py           # OpenAI wrapper + logging
│   ├── layer1_logic.py         # question selection, theme scoring
│   ├── layer2_logic.py         # constraint/optimization/adaptability scoring
│   ├── layer3_logic.py         # follow-up generation, rubric scoring
│   ├── scoring_matrix.py       # overall score, top-fit classification
│   └── feedback_generator.py   # candidate + recruiter LLM summaries
│
├── views/
│   ├── __init__.py
│   ├── state.py                # session state + DB-backed resume
│   ├── landing.py              # candidate/recruiter choice
│   ├── candidate_intro.py      # welcome page
│   ├── layer1.py               # cognitive assessment UI
│   ├── layer2.py               # staffing simulation UI
│   ├── layer3.py               # voice interview UI
│   ├── candidate_results.py    # final feedback
│   └── recruiter_dashboard.py  # table, filters, deep-dive
│
├── data/
│   ├── layer2_scenario.json
│   ├── interview_questions.txt
│   ├── questions/
│   │   ├── logical.xlsx
│   │   ├── numerical.xlsx
│   │   └── verbal.xlsx
│   └── charts/                 # optional chart images
│
├── logs/
│   └── llm_calls.log           # JSON-per-line log of every LLM call
│
├── recordings/                 # reserved for audio if you wire persistence
│
└── tests/
    ├── __init__.py
    └── test_scoring.py         # 24 regression tests
```

---

## Credits

Built as part of the Capgemini Invent capstone at HSG, Group 4: Isabella Albertoni, Inés Frank, Dmytro Makukha, Richard, Mina Simic. Supervising faculty: Prof. Ursula Knorr. Capgemini Invent contacts: Jakob and Nicolas.
