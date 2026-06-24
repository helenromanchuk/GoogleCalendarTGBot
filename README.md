# Google Calendar Telegram Bot (GCalendarTGBot)

An agentic Telegram Bot that schedules, manages, and views events on Google Calendar using natural language messages (supporting both English and Russian). 

Built using the **Google Agent Development Kit (ADK 2.0)**, Gemini Models (via Google GenAI/Vertex AI), **aiogram v3**, and FastAPI.

---

## Key Features

1. **Natural Language Event Scheduling**: Parses dates, times, titles, locations, and descriptions from conversational messages.
2. **Interactive Conflict Detection**: Timezone-aware verification of overlapping events. If a conflict is found, the bot prompts the user with inline choices: `[Add anyway]` or `[Choose another time]`.
3. **On-Start Calendar Setup**: When running `/start`, the bot automatically checks if the user has authenticated and selected a default calendar. If not, it directs them through the OAuth consent screen and prompts them to pick a Google Calendar.
4. **Session Expiry Recovery**: If the user revokes Google Account permissions, the bot detects this during API calls, clears credentials, and prompts the user to re-connect.
5. **Secure Credential Storage**: User Google OAuth refresh tokens are encrypted at rest using **Fernet encryption** (symmetric cryptography) and stored in SQLite.
6. **Fully Tested**: Features 55 unit and integration tests covering security boundaries, tool execution, session lifetimes, and command callbacks.

---

## System Architecture

```
Telegram Client 
      │
      ▼
[HTTPS Webhook]
      │
      ▼
FastAPI Web Server (app/fast_api_app.py) 
      │
      ├─► POST /webhook ──► aiogram Dispatcher (app/telegram_app.py)
      │                               │
      │                       ADK Runner (in-memory sessions)
      │                               │
      │                       ADK Agent (app/agent.py) 
      │                       (Gemini 2.0 Flash Agent)
      │                               │
      │                               ├──► Tools: (app/tools.py)
      │                               │     • get_user_auth_status
      │                               │     • get_google_auth_url
      │                               │     • get_user_calendars
      │                               │     • set_default_calendar
      │                               │     • create_calendar_event
      │                               │     • get_upcoming_events
      │                               │
      │                               └──► API / DB Integrations:
      │                                     • Google Calendar API (OAuth 2.0)
      │                                     • SQLite (Encrypted Credentials)
      │                                     • Redis (OAuth state + FSM storage)
      │
      └─► GET /oauth2callback ──► Google OAuth 2.0 Redirect ──► Store Tokens
```

---

## Directory Structure

```
.
├── calendarbot-agent/            # Main ADK bot project
│   ├── app/                      # Source code
│   │   ├── agent.py              # ADK agent configurations & instructions
│   │   ├── tools.py              # Calendar API tools & conflict detection
│   │   ├── config.py             # Settings & Environment variables
│   │   ├── database.py           # SQLite db schema & encryption helper
│   │   ├── redis_client.py       # Redis client configuration
│   │   ├── telegram_app.py       # aiogram handlers & bot callback handlers
│   │   └── fast_api_app.py       # Webhooks, OAuth redirection & server endpoints
│   ├── deployment/               # Deployment scripts & Terraform templates
│   ├── tests/                    # Tests
│   │   ├── integration/          # E2E server and agent flow tests
│   │   ├── unit/                 # Model, database, helper & UI tests
│   │   └── eval/                 # Dataset & config for ADK agent evaluations
│   ├── Dockerfile                # Deployment container configuration
│   ├── pyproject.toml            # Project dependencies and tool settings
│   └── uv.lock                   # Lockfile for reproducible builds
├── .gitignore                    # Git exclusions
├── .agents-cli-spec.md           # ADK specification
└── README.md                     # Root project documentation (this file)
```

---

## Quick Start

### 1. Prerequisites
- **Python 3.11+**
- [`uv`](https://docs.astral.sh/uv/) package manager
- **Redis** server (local or hosted)
- **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))
- **Google Cloud Project** with the Google Calendar API enabled
- **Google OAuth 2.0 Web Application Credentials**
- **Gemini API Key** (from [Google AI Studio](https://aistudio.google.com/))

### 2. Configuration
Copy the configuration template and fill in the required environment variables:
```bash
cd calendarbot-agent
cp .env.example .env
```
Key variables to configure in `.env`:
* `BOT_TOKEN`: Telegram bot API token.
* `GOOGLE_API_KEY`: Gemini API key (for development).
* `GOOGLE_CLIENT_ID` & `GOOGLE_CLIENT_SECRET`: Your Google OAuth 2.0 client credentials.
* `ENCRYPTION_KEY`: A 32-byte Fernet URL-safe base64-encoded key. (Generate one with `cryptography.fernet.Fernet.generate_key()`).
* `BASE_URL`: Public address of your bot webhook handler (e.g., your ngrok URL or Cloud Run service URL).

### 3. Install Dependencies
Run `uv` to download and sync dependencies:
```bash
uv sync
```

### 4. Running Locally
Ensure Redis is running, then start the FastAPI application:
```bash
uv run python -m app.fast_api_app
```
To receive Telegram webhook events locally, expose port `8080` using **ngrok**:
```bash
ngrok http 8080
```
Update the `BASE_URL` in `.env` to your ngrok forwarding address.

### 5. Running Tests
Execute the automated test suite using `pytest`:
```bash
uv run pytest
```

---

## Deployment (Google Cloud Run)

To deploy the CalendarBot onto Google Cloud Run, run:
```bash
uv run agents-cli deploy --no-confirm-project
```
This builds the Docker container from the `Dockerfile`, pushes it to Artifact Registry, and creates or updates a Cloud Run service serving traffic.
