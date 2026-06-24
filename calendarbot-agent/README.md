# CalendarBot — ADK Agent

A Telegram bot that adds events to Google Calendar from natural-language messages. Built with **Google Agent Development Kit (ADK)** and deployed on Cloud Run.

## Architecture

```
Telegram → POST /webhook → FastAPI → aiogram dispatcher
                                           │
                                    ADK Runner (in-memory sessions)
                                           │
                                    calendarbot_agent (Gemini Flash)
                                    ┌──────────────────────────────┐
                                    │ Tools:                       │
                                    │  • get_user_auth_status      │
                                    │  • get_google_auth_url       │
                                    │  • get_user_calendars        │
                                    │  • set_default_calendar      │
                                    │  • create_calendar_event     │
                                    └──────────────────────────────┘
                                           │
                                    Google Calendar API (OAuth 2.0)
                                    SQLite (Fernet-encrypted tokens)
                                    Redis (OAuth state + FSM storage)

Browser → GET /oauth2callback → FastAPI → store encrypted tokens → notify Telegram
```

## Quick Start

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) package manager
- Redis (local or hosted)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Google Cloud project with Calendar API enabled
- Google OAuth 2.0 credentials (Web application type)
- Gemini API key (from [Google AI Studio](https://aistudio.google.com/))

### Setup

1. **Copy and fill in environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your values
   ```

2. **Install dependencies:**
   ```bash
   agents-cli install
   # or: uv sync
   ```

3. **Run locally (requires Redis):**
   ```bash
   uv run python -m app.fast_api_app
   ```

   Use [ngrok](https://ngrok.com/) to expose the webhook:
   ```bash
   ngrok http 8080
   # Set BASE_URL=https://xxxx.ngrok.io in .env
   ```

4. **Test with the ADK playground (no Telegram required):**
   ```bash
   agents-cli playground
   ```

## Development

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing via ADK web UI |
| `agents-cli run "your prompt"` | Quick smoke test |
| `agents-cli eval generate` | Run agent over eval dataset |
| `agents-cli eval grade` | Grade agent traces |
| `agents-cli lint` | Check code quality |
| `agents-cli deploy` | Deploy to Cloud Run (requires approval) |

## Project Structure

```
calendarbot-agent/
├── app/
│   ├── agent.py          # ADK root_agent definition
│   ├── tools.py          # Calendar tools (auth, create event, etc.)
│   ├── config.py         # Settings from .env
│   ├── database.py       # SQLAlchemy + Fernet-encrypted credentials
│   ├── redis_client.py   # Async Redis client
│   ├── telegram_app.py   # aiogram bot (Telegram bridge → ADK)
│   └── fast_api_app.py   # FastAPI app (webhook + OAuth callback + ADK endpoints)
├── tests/
│   └── eval/
│       ├── eval_config.yaml
│       └── datasets/
│           └── calendar_bot_evals.json
├── .env                  # Your secrets (never commit!)
├── .env.example          # Template
└── pyproject.toml        # Dependencies
```

## User Flow

1. User sends `/start` to the bot
2. If not authenticated → bot sends Google OAuth URL
3. User clicks the URL → Google consent screen → redirect to `/oauth2callback`
4. Tokens encrypted with Fernet and stored in SQLite
5. Bot sends confirmation + asks user to pick a default calendar
6. User selects calendar via inline buttons
7. User sends any message like *"Meeting with Bob tomorrow at 5pm"*
8. ADK agent parses the message → asks for missing info if needed → creates event
9. Bot replies with a link to the created Google Calendar event

## Environment Variables

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `GOOGLE_API_KEY` | Gemini API key (AI Studio, for dev) |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 2.0 client secret |
| `ENCRYPTION_KEY` | Fernet key for token encryption at rest |
| `BASE_URL` | Public URL of this service (for webhook + OAuth redirect) |
| `DATABASE_URL` | SQLite URL (default: `sqlite:///./data/calendar_bot.db`) |
| `REDIS_URL` | Redis connection URL |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID (for Vertex AI in production) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` for production (Vertex AI), `False` for dev |

## Deployment to Cloud Run

```bash
# Add deployment configuration
agents-cli scaffold enhance . --deployment-target cloud_run

# Deploy (requires explicit approval)
agents-cli deploy
```

## OAuth Setup (Google Cloud Console)

1. Go to **APIs & Services → Credentials**
2. Create an **OAuth 2.0 Client ID** (Web application type)
3. Add authorized redirect URI: `https://YOUR-SERVICE.run.app/oauth2callback`
4. Download credentials and set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
5. Enable the **Google Calendar API** in your project
