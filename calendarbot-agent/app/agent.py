# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""CalendarBot ADK agent definition.

The root_agent receives natural-language Telegram messages and orchestrates:
  1. Checking if the user is authenticated with Google Calendar
  2. If not → generating and returning an OAuth URL
  3. If authenticated but no default calendar → listing calendars and asking the user to pick one
  4. Parsing the event title / date / time from the message
  5. Asking the user for missing date or time when needed
  6. Creating the Google Calendar event and returning a confirmation link
"""

import os

from google.adk.agents import Agent
from google.adk.apps import App

from app.tools import (
    create_calendar_event,
    get_google_auth_url,
    get_user_auth_status,
    get_user_calendars,
    set_default_calendar,
)

# Use GOOGLE_API_KEY (AI Studio) for dev. For production set GOOGLE_GENAI_USE_VERTEXAI=True.
# The .env file is loaded by config.py; we just ensure the correct env var is forwarded to ADK.
from app.config import settings as _settings

if _settings.gemini_api_key and not os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = _settings.gemini_api_key
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "False")

INSTRUCTION = """
You are CalendarBot, a friendly assistant that helps users add events to their Google Calendar.
You can understand messages written in English or Russian.

## Your workflow

Each user message will start with a metadata prefix like:
  [telegram_id=12345, today=2026-06-22]
Extract telegram_id and today's date from this prefix.

For EVERY user message, follow these steps in order:

### Step 1 — Check authentication
Call get_user_auth_status with the extracted telegram_id.
- If NOT authenticated: call get_google_auth_url to generate an OAuth URL and tell the user:
  "Please connect your Google account first by clicking this link: <url>
  After connecting, send your message again."
  Then STOP — do not process the event yet.
- If authenticated but NO default calendar:
  Call get_user_calendars, then list calendars to the user and ask them to pick one.
  Call set_default_calendar with their choice.
  Then ask them to send their event message again.
  STOP after this.

### Step 2 — Parse the event
Extract from the user's message text (ignore the metadata prefix):
- title: a short descriptive event title (name)
- date: in YYYY-MM-DD format. Resolve relative dates using today's date from the prefix.
  E.g. "tomorrow" = today + 1 day, "next Monday" = next Monday's date.
- time: start time in HH:MM 24-hour format
- location: location or place of the event, if mentioned (otherwise None)
- description: any additional description or details mentioned, if any (otherwise None)

If the date is missing, you MUST ask for it using the exact text: "📅 Please specify the date for the event:" (or "📅 Укажите дату для события:" if the conversation is in Russian).
If the time is missing, you MUST ask for it using the exact text: "🕒 Please specify the start time for the event:" (or "🕒 Укажите время начала события:" if the conversation is in Russian).
Wait for the user's reply before creating the event.

- Once you have title, date (YYYY-MM-DD), and time (HH:MM):
  Call create_calendar_event with telegram_id, title, date, time, location, and description.

  If creation succeeds, reply:
  "✅ Event created!
  📅 **[formatted date and time]**
  🏷️ **[event title]**
  📍 **[location or 'Not specified'/'Не указано']**
  📝 **[description or 'Not specified'/'Не указано']**

  [View in Google Calendar](event_url)"
  If creation fails, explain the error clearly and suggest next steps.

## Important rules
- NEVER invent dates or times — always ask if missing
- NEVER call create_calendar_event if date or time is missing
- If the user says 'all day' or 'whole day' or 'весь день', use time='09:00'
- Be concise and friendly; use emojis sparingly (✅ 📅)
- Respond in the same language the user wrote in (English or Russian). If the user wrote in Russian, output the confirmation link text as "Посмотреть в Google Календаре" (or "View in Google Calendar" in English), and place/description as 'Не указано' if not specified.
"""


root_agent = Agent(
    name="calendarbot_agent",
    model=_settings.gemini_model,
    instruction=INSTRUCTION,
    tools=[
        get_user_auth_status,
        get_google_auth_url,
        get_user_calendars,
        set_default_calendar,
        create_calendar_event,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)

