# APPSC Group‑1 Spaced‑Repetition Telegram Bot

A production‑ready Telegram bot that delivers **SM‑2 (Anki‑style) spaced
repetition** for APPSC Group‑1 exam prep. Your **Google Sheet is the
database** — no extra infrastructure. The bot sends due questions every
morning, forces active recall, grades your answer, and reschedules each card
with the SM‑2 algorithm.

---

##  Features

- **SM‑2 scheduling** with `Again / Hard / Good / Easy` grading.
- **Daily 07:00 IST delivery** of due cards, one message at a time (throttled).
- **Active recall first**: a `Show Facts` button reveals the answer only after
  you've tried to recall it.
- **Google Sheet = source of truth**: adds 7 SM‑2 columns on first run and
  migrates legacy rows automatically.
- **Commands**: `/today /review /weak /stats /search /add /help /start`.
- **Resilient**: retry‑with‑backoff on Google API errors, per‑run caching,
  batched writes, stale‑message re‑fetch, idempotent daily job.
- **Zero‑maintenance**: deploy once as a worker; runs 24/7.

---

## 🗂 Project structure

```
appsc-bot/
├── main.py            # entrypoint: logging, handlers, scheduler, polling
├── config.py          # pydantic-settings (.env)
├── sheets.py          # gspread wrapper: read/write/migrate/retry
├── srs.py             # SM-2 algorithm (pure, unit-tested)
├── services.py        # app state, chat registry, throttled delivery
├── scheduler.py       # APScheduler 07:00 IST job
├── messages.py        # MarkdownV2 formatters + escaping + keyboards
├── handlers/
│   ├── commands.py    # /start /today /stats /add ...
│   └── callbacks.py   # inline buttons + grading
├── tests/test_srs.py  # pytest for SM-2 correctness
├── requirements.txt
├── .env.example
├── Procfile           # `worker: python main.py`
└── README.md
```

---

## 📊 The Google Sheet contract

Existing columns **A–L** are read **by header name** (never by index), so you
can reorder them safely:

| Col | Header |
|-----|--------|
| A | Session No |
| B | Date |
| C | Syllabus Node |
| D | Question Text |
| E | Key Facts & Gist *(multi‑line bullets)* |
| F | Model Answer Keywords / Dimensions *(pipe‑separated)* |
| G | Probability Rating |
| H–K | Spaced Repetition Day 1/3/7/21 *(human reference only)* |
| L | Recall Status (✅/❌) |

On first run the bot **appends** these SM‑2 columns if missing and seeds
defaults for any blank rows (setting `Next Review Date = today` so legacy
rows enter the queue):

| Col | Header | Default |
|-----|--------|---------|
| M | Ease Factor | 2.5 |
| N | Interval Days | 0 |
| O | Repetitions | 0 |
| P | **Next Review Date** | today |
| Q | Last Reviewed | "" |
| R | Total Reviews | 0 |
| S | Correct Count | 0 |

> Scheduling is driven entirely by **column P (Next Review Date)**. Columns
> H–K remain as a static, human‑readable reference.

---

## 🧮 The SM‑2 algorithm

Grades map to quality scores: `Again=2, Hard=3, Good=4, Easy=5`.

```
if q < 3:           repetitions = 0; interval = 1
else:
    if repetitions == 0:   interval = 1
    elif repetitions == 1: interval = 3
    else:                  interval = round(interval * ease_factor)
    repetitions += 1
ease_factor = max(1.3, ease_factor + (0.1 - (5-q)*(0.08 + (5-q)*0.02)))
next_review_date = today + interval days
```

Each review updates M, N, O, P, Q, `Total Reviews` (+1), `Correct Count`
(+1 if q≥3), and sets L to ✅ (q≥3) or ❌. A card is **mastered** at
interval ≥ 21 days.

Run the tests:

```bash
pip install pytest
pytest          # 19 tests covering all four grades across multiple reps
```

---

## 🚀 Setup (≈10 minutes)

### 1. Create the Telegram bot
1. Message [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Copy the **token** it gives you → goes in `TELEGRAM_BOT_TOKEN`.
3. Message [@userinfobot](https://t.me/userinfobot) to get **your chat ID**
   (for `AUTHORIZED_CHAT_IDS`, optional but recommended).

### 2. Create a Google Service Account
1. Go to [Google Cloud Console](https://console.cloud.google.com/) →
   create/select a project.
2. **APIs & Services → Library** → enable **Google Sheets API** *and*
   **Google Drive API**.
3. **APIs & Services → Credentials → Create Credentials → Service Account**.
4. Open the service account → **Keys → Add Key → Create new key → JSON**.
   Download it and save as `appsc-bot/service_account.json`.
5. Copy the service‑account **email** (looks like
   `something@project.iam.gserviceaccount.com`).

### 3. Share the sheet with the service account
- Open your Google Sheet → **Share** → paste the service‑account email →
  give it **Editor** access → Send.
  *(Without this the bot can't read/write the sheet.)*

### 4. Configure environment
```bash
cd appsc-bot
cp .env.example .env
# edit .env: set TELEGRAM_BOT_TOKEN, confirm SHEET_ID, set AUTHORIZED_CHAT_IDS
```

### 5. Install & run locally
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```
You should see `Bot is up. Polling for updates…`. In Telegram, open your bot
and send `/start`, then `/today`.

---

## ✅ First‑run checklist (verify in < 10 minutes)

1. `python main.py` starts and logs **"Connected to sheet …"** and
   **"Schema migration complete"** — confirms credentials + sheet sharing.
2. Open your sheet: columns **M–S now exist** and legacy rows have
   `Next Review Date = today`.
3. In Telegram send `/start` → you get the welcome message (chat registered).
4. Send `/today` → it reports the due count and sends cards one at a time.
5. Tap **Show Facts** on a card → the message edits to reveal Key Facts +
   Keywords and shows the grading row.
6. Tap **Good** → you get "Saved ✅"; check the sheet row: M–S and L updated,
   `Next Review Date` moved forward.
7. Send `/stats` → dashboard renders (totals, streak, mastered, per‑node).
8. Send `/add` → walk through the 6 prompts → new row appears in the sheet,
   queued for today.

---

## ☁️ Deploy to Railway (24/7 worker)

> The same steps work on Render (create a **Background Worker**, build with
> `pip install -r requirements.txt`, start with `python main.py`).

1. Push this folder to a GitHub repo (the `.gitignore` keeps secrets out).
2. On [Railway](https://railway.app): **New Project → Deploy from GitHub repo**.
3. Railway detects the `Procfile` → it runs the `worker` process.
4. **Variables** tab — add every value from your `.env`:
   - `TELEGRAM_BOT_TOKEN`, `SHEET_ID`, `WORKSHEET_NAME`, `TIMEZONE`,
     `DAILY_REVIEW_HOUR`, `AUTHORIZED_CHAT_IDS`.
5. **Service account JSON** — the file can't be committed. Two options:
   - **Option A (recommended):** add a variable
     `GOOGLE_SERVICE_ACCOUNT_JSON` containing the *contents* of the JSON, then
     add a tiny boot step. Easiest path: set `GOOGLE_SERVICE_ACCOUNT_FILE` to a
     writable path and add a **pre‑start** command:
     ```bash
     printenv GOOGLE_SERVICE_ACCOUNT_JSON > service_account.json && python main.py
     ```
     (Put that as the worker start command and add `GOOGLE_SERVICE_ACCOUNT_JSON`
     as a variable.)
   - **Option B:** use Railway's "Files" / secret‑file feature (if available)
     to mount `service_account.json` at the app root.
6. Deploy. Watch logs for **"Bot is up. Polling for updates…"**.
7. The daily job fires at `DAILY_REVIEW_HOUR` in `TIMEZONE` (07:00 IST by
   default). It's idempotent — a restart won't double‑send.

### Keeping it alive on free tiers
This bot uses **long polling** (no inbound URL needed), so a background worker
is all you need. Railway's free tier sleeps inactive *web* services, but
**worker** processes with an active outbound connection stay running; if your
plan sleeps the worker, upgrade to the hobby tier or use Render's free worker.

---

## 🔧 Commands reference

| Command | Description |
|---------|-------------|
| `/start` | Register this chat for daily delivery |
| `/today` | Cards due today (count + drip) |
| `/review` | Rapid‑fire every card currently marked ❌ |
| `/weak` | Topics ranked by lowest recall %, by Syllabus Node |
| `/stats` | Dashboard: totals, recall %, streak, mastered, per‑node |
| `/search <kw>` | Find questions by keyword in Q text / node |
| `/add` | Guided flow to add a new question row to the sheet |
| `/help` | List all commands |

---

## 🛟 Troubleshooting

- **`gspread ... PermissionError / 403`** → you didn't share the sheet with the
  service‑account email, or the Drive/Sheets API isn't enabled.
- **`SpreadsheetNotFound`** → wrong `SHEET_ID` or wrong Google project.
- **Nothing arrives at 07:00** → check `TIMEZONE`/`DAILY_REVIEW_HOUR`, confirm
  you ran `/start`, and that some row has `Next Review Date <= today`.
- **"This bot is private"** → your chat ID isn't in `AUTHORIZED_CHAT_IDS`.
  Add it (comma‑separated) or leave the variable blank to allow all `/start`ers.
- **MarkdownV2 errors** → all dynamic text is escaped in `messages.py`; if you
  add new strings, run them through `messages.escape()`.
