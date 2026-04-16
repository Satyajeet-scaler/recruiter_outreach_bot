## Recruiter Outreach Bot

Automates LinkedIn outreach:
- **1st-degree**: send a direct message
- **2nd/3rd-degree**: send a connection request with a note
- If **Pending** is detected for 2nd/3rd-degree, the profile is **skipped** (invite already sent)

This repo supports:
- Local CLI runs via `run_outreach.py`
- Railway deployment via FastAPI (`main.py`) + Docker (`Dockerfile`)

---

## Prerequisites

- **Python 3.11+**
- **Git**
- A browser automation environment:
  - Local: Google Chrome/Chromium installed
  - Docker/Railway: already handled by `Dockerfile` (Chromium + xvfb)

---

## Setup (Linux)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip

git clone <your-repo-url>
cd recruiter_outreach_bot

python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt

# Needed for manual LinkedIn login script
python -m playwright install chromium

mkdir -p data
```

---

## Setup (macOS)

```bash
# Git (Xcode Command Line Tools)
xcode-select --install

# (Optional) Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python + Chrome
brew install python@3.12
brew install --cask google-chrome

git clone <your-repo-url>
cd recruiter_outreach_bot

python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt

# Needed for manual LinkedIn login script
python -m playwright install chromium

mkdir -p data
```

---

## Create LinkedIn session (cookies)

Run the manual login helper (opens a real browser window; you complete login/2FA):

```bash
python linkedin_manual_login.py
```

### Where it saves

- Default local path (unless you set `LINKEDIN_STORAGE_PATH`): `data/longin_storage.json`
- You can also save to a specific file:

```bash
python linkedin_manual_login.py "data/linkedin_storage.json"
```

---

## Run outreach locally (no server)

Prepare an `items.json`:

```json
[
  {
    "profile_url": "https://www.linkedin.com/in/example/",
    "message_text": "Hi, would love to connect regarding opportunities."
  }
]
```

Run:

```bash
python run_outreach.py items.json
```

Optional debug mode:

```bash
python run_outreach.py items.json --debug
```

---

## Run API locally (FastAPI)

```bash
export INTERNAL_TRIGGER_TOKEN="your-token"
uvicorn main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl "http://localhost:8000/health"
```

Upload a LinkedIn session JSON to the server:

```bash
curl -X POST "http://localhost:8000/internal/linkedin-session" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Trigger-Token: your-token" \
  --data-binary @"data/longin_storage.json"
```

Trigger outreach:

```bash
curl -X POST "http://localhost:8000/internal/run-outreach" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Trigger-Token: your-token" \
  -d '{
    "debug": false,
    "items": [
      {
        "profile_url": "https://www.linkedin.com/in/example/",
        "message_text": "Hi, would love to connect regarding opportunities."
      }
    ]
  }'
```

---

## Deploy on Railway

This repo includes:
- `Dockerfile` (runs `uvicorn` under `xvfb-run`)
- `railway.toml` (Dockerfile builder + `/health` check)

### Required Railway env vars

- `INTERNAL_TRIGGER_TOKEN`

### Recommended (Railway Volume) for session persistence

Mount a volume at `/data`, then set:

- `LINKEDIN_STORAGE_PATH=/data/longin_storage.json`

Upload your saved session to Railway using the API endpoint:
- `POST /internal/linkedin-session`

Or configure `linkedin_manual_login.py` to auto-upload after saving:

```bash
export LINKEDIN_SESSION_UPLOAD_URL="https://<your-app>.up.railway.app/internal/linkedin-session"
export INTERNAL_TRIGGER_TOKEN="<token>"
python linkedin_manual_login.py
```

