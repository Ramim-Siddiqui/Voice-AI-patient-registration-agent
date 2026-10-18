# Voice AI Patient Registration Agent

A phone-based AI intake coordinator. Callers dial a real U.S. number, have a natural
conversation with "Riley", and end up with a validated patient record in a persistent
database that is exposed through a REST API and a web dashboard.

| | |
|---|---|
| **Phone number** | **+1 (775) 490-0370** (U.S., inbound) |
| **API base URL** | https://voice-patient-registration-seven.vercel.app |
| **Dashboard** | https://voice-patient-registration-seven.vercel.app/ |
| **Interactive API docs** | https://voice-patient-registration-seven.vercel.app/docs |
| **Agent prompt (live)** | https://voice-patient-registration-seven.vercel.app/vapi/assistant-config |
| **Source** | https://github.com/Ramim-Siddiqui/Voice-AI-patient-registration-agent |

Everything runs on free tiers with no credit card: **Vapi** (telephony + STT/LLM/TTS
orchestration, free U.S. number, $5 starter credit), **Vercel Hobby** (FastAPI backend),
**Neon** (PostgreSQL). Locally it runs on SQLite with zero setup.

> Demo data only. This is a technical assessment, not a HIPAA-compliant system; do not
> register real patients.

---

## 1. Architecture

```
          PSTN call                 HTTPS webhooks (JSON)                SQLAlchemy
Caller ───────────────► Vapi ─────────────────────────────► FastAPI ───────────────► PostgreSQL (Neon)
                        • Deepgram Nova-3 STT                 app/voice   tool calls         or SQLite (local)
                        • OpenAI gpt-4.1-mini (tools)         app/api     REST /patients
                        • Deepgram Aura-2 TTS                 app/services business rules
                        • turn-taking / interruptions         app/db      models + constraints
                                                                  │
                                                     Dashboard (/) + OpenAPI docs (/docs)
```

**Separation of concerns**

| Layer | Where | Responsibility |
|---|---|---|
| Telephony / speech | Vapi (configured by `scripts/vapi_setup.py`) | Phone number, STT, TTS, endpointing, barge-in, silence handling |
| Conversation logic | `app/voice/prompt.py` (system prompt), `app/voice/tools.py` (tool schemas) | What to collect, how to confirm, how to recover |
| Voice ↔ backend | `app/voice/webhook.py`, `app/voice/handlers.py` | Parses Vapi `tool-calls` / `end-of-call-report`, runs tools, returns results |
| REST API | `app/api/*` | `/patients` CRUD, `/calls`, `/appointments`, consistent envelope, status codes |
| Validation | `app/schemas/patient.py` | One Pydantic schema set used by **both** the API and the voice tools |
| Business logic | `app/services/*` | Create/update/soft-delete, duplicate lookup, call transcripts, mock scheduling |
| Persistence | `app/db/models.py`, `app/db/session.py` | SQLAlchemy models with CHECK constraints; SQLite or PostgreSQL |
| Dashboard | `app/dashboard/` | Static HTML/JS consuming the public API |

**Call flow**

1. Vapi answers and speaks the greeting (`firstMessage`), then runs the LLM with the system prompt.
2. The agent collects the required fields one question at a time; after the phone number it calls
   `lookup_patient` (duplicate detection, caller-ID aware).
3. The agent reads everything back; only after an explicit "yes" it calls `register_patient`
   (or `update_patient` for a returning caller).
4. The backend validates with the same rules as `POST /patients`, writes the row, logs the final
   payload, and returns a `status` + an instruction for the model (`created`, `validation_error`
   with the exact fields, `duplicate`, or `error`). The model relays the outcome honestly.
5. Optional: first-appointment booking (mock slots). The agent says goodbye and calls `endCall`.
6. Vapi posts `end-of-call-report`; the transcript, summary, recording link and cost are stored on
   the `calls` row linked to the patient (visible on the dashboard).

---

## 2. Tech stack and why

| Choice | Why (and the trade-off) |
|---|---|
| **Vapi** for telephony + voice pipeline | Fastest path to a dialable number with a real voice stack. One free U.S. number without a card; $5 starter credit (~60–70 minutes at ~$0.07/min). Inbound only, which is all this needs. Alternatives: Twilio trial (callers must be pre-verified and hear a trial preamble), Retell (number costs $2/mo). |
| **Deepgram Nova-3** (STT) + **Aura-2 "thalia"** (TTS) | Low latency, cheap through Vapi, and a free $200 Deepgram credit can be plugged in as your own key to stretch the Vapi credit further. |
| **OpenAI gpt-4.1-mini** | Reliable tool calling with short latency at ~$0.005/min; configurable via `VAPI_MODEL`. |
| **FastAPI + Pydantic v2** | Typed validation, automatic OpenAPI docs, async-friendly, tiny cold start. |
| **SQLAlchemy 2 + SQLite / PostgreSQL** | Same models and CHECK constraints locally and in the cloud. SQLite = zero-setup dev + tests; Neon = persistent, free, scales to zero (resumes in ~1 s). |
| **Vercel Hobby** | Free, no card, no "sleep for 15 minutes then take 60 s to wake" behaviour (which would break mid-call tool calls on Render's free tier). Cold start ≈ 1–3 s, well under Vapi's 20 s tool timeout. |
| **Neon free Postgres** | Permanent free plan, no card, data never deleted for inactivity (AWS regions). Supabase was rejected because free projects pause after 7 idle days; Render Postgres expires after 30 days. |

---

## 3. Project layout

```
voice-patient-registration/
├── main.py                     # Vercel / uvicorn entrypoint (exposes `app`)
├── app/
│   ├── main.py                 # FastAPI factory: routers, CORS, access log, error envelope
│   ├── config.py               # Settings from environment (.env supported)
│   ├── logging_utils.py        # stdout logging + JSON event lines
│   ├── api/                    # REST layer
│   │   ├── envelope.py         #   {data, error} envelope + exception handlers (400/401/404/422/500)
│   │   ├── patients.py         #   GET/POST /patients, GET/PUT/DELETE /patients/{id}
│   │   ├── calls.py            #   /calls, /patients/{id}/calls, /appointments
│   │   ├── health.py           #   /health
│   │   └── security.py         #   optional X-API-Key for writes
│   ├── schemas/patient.py      # Pydantic schemas + normalisers (phone, DOB, state, ZIP, sex, names)
│   ├── services/               # patient_service, call_service, appointment_service
│   ├── db/                     # models (CHECK constraints, indexes), session (SQLite/Postgres), seed
│   ├── voice/
│   │   ├── prompt.py           # ★ system prompt + design notes
│   │   ├── tools.py            # ★ tool definitions sent to Vapi
│   │   ├── handlers.py         # tool-call handlers (lookup/register/update/slots/book)
│   │   └── webhook.py          # POST /vapi/webhook, GET /vapi/assistant-config
│   └── dashboard/              # GET / (patients, calls & transcripts, appointments)
├── scripts/
│   ├── vapi_setup.py           # provisions assistant + tools + free phone number (idempotent)
│   └── smoke_test.py           # end-to-end check against any deployment
├── tests/                      # 101 pytest tests (API, validation, webhook/tool flows, review regressions)
├── requirements.txt, .env.example, .python-version, .vercelignore, render.yaml
```

---

## 4. Run locally (5 minutes)

Prerequisites: Python 3.11+ (3.12 recommended).

```powershell
# Windows (PowerShell) - no activation needed, so it works under the default execution policy
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env            # defaults use SQLite + demo seed data
.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
# tests / smoke test:  .venv\Scripts\python.exe -m pytest     .venv\Scripts\python.exe scripts\smoke_test.py http://127.0.0.1:8000
```

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000/> (dashboard) and <http://127.0.0.1:8000/docs> (API docs).
On Windows you can also double-click `start-local.bat`.

```bash
pytest -q                                   # run the test suite
python scripts/smoke_test.py http://127.0.0.1:8000   # exercise every endpoint
```

To let Vapi reach a local server, expose it with ngrok (`ngrok http 8000`) and use the
`https://…ngrok…` URL as `PUBLIC_BASE_URL` in step 5.3.

---

## 5. Deploy for free (no credit card)

### 5.1 Database – Neon
1. Sign up at <https://neon.com> (free plan), create a project in an **AWS us-east-1** region.
2. Copy the **pooled** connection string (host contains `-pooler`), e.g.
   `postgresql://user:pass@ep-xxx-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`

### 5.2 Backend – Vercel
1. Push this repo to GitHub.
2. <https://vercel.com> → *Add New Project* → import the repo. Vercel auto-detects FastAPI
   (root `main.py` exposes `app`); no build settings needed.
3. Environment variables (Settings → Environment Variables):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon pooled string |
   | `ENVIRONMENT` | `production` |
   | `VAPI_WEBHOOK_SECRET` | a long random string (same value used in 5.3) |
   | `SEED_DEMO_DATA` | `true` (two demo patients) or `false` |
   | `PUBLIC_BASE_URL` | optional: `https://<your-app>.vercel.app` so `/vapi/assistant-config` shows the real webhook URL (falls back to Vercel's own URL variables) |
   | `CLINIC_NAME` / `AGENT_NAME` | optional persona override; if set, use the identical values in the local `.env` used by `scripts/vapi_setup.py`, which is what the phone agent actually speaks |

4. Deploy → note the production URL, e.g. `https://voice-patient-registration.vercel.app`.
5. Verify. The webhook check needs the same `VAPI_WEBHOOK_SECRET` you set in step 3; the
   script reads it from your local `.env` (or the shell environment):

   ```powershell
   # Windows: put VAPI_WEBHOOK_SECRET=<same value> in .env, then
   .venv\Scripts\python.exe scripts\smoke_test.py https://<your-app>.vercel.app
   ```
   ```bash
   # macOS / Linux
   VAPI_WEBHOOK_SECRET=<same value> python scripts/smoke_test.py https://<your-app>.vercel.app
   ```

Tables are created automatically on the first request (`AUTO_INIT_DB=true`), so there is no
migration step.

### 5.3 Voice agent – Vapi
1. Sign up at <https://vapi.ai> (free; $5 credit, one free U.S. number without a payment method).
2. Dashboard → *Vapi API Keys* → copy the **private** key.
3. In your local `.env` set `VAPI_API_KEY`, `PUBLIC_BASE_URL=https://<your-app>.vercel.app`,
   `VAPI_WEBHOOK_SECRET` (same as on Vercel) and optionally `VAPI_AREA_CODE`.
4. Run the provisioning script (idempotent; re-run after any prompt/tool change):

   ```bash
   python scripts/vapi_setup.py --dry-run   # inspect the assistant JSON first (optional)
   python scripts/vapi_setup.py             # creates/updates assistant, tools, phone number
   ```

   It prints the phone number and stores the ids in `.vapi-state.json`. The number can take a
   few minutes to become `active`. If the dashboard insists on a payment method for the number,
   try another area code or create it manually (Phone Numbers → Create → *Free Vapi Number*) and
   attach the assistant under *Inbound Settings*. In that case copy the number's **ID** (the UUID
   on the number's page, not the digits) into `VAPI_PHONE_NUMBER_ID` in `.env`, or re-run with
   `python scripts/vapi_setup.py --skip-phone`; otherwise every re-run tries to request a new
   number again.
5. Call the number. Watch the dashboard and Vercel logs (`vercel logs` or the web UI).

**Stretching the free credit.** Add your own Deepgram key (free $200 credit, no card) under
Vapi → *Integrations*; STT and TTS then stop drawing from the Vapi balance, leaving only the
$0.05/min platform fee + a fraction of a cent for the LLM (~90 minutes from $5).

### Hosting alternative – Render
`render.yaml` deploys the same app to a Render free web service. Render sleeps after 15 idle
minutes and takes ~1 minute to wake, which would make the first tool call of a call fail, so pair
it with a free pinger (UptimeRobot / cron-job.org hitting **`/healthz`** every 5 minutes).
Do not point the pinger at `/health`: that route runs `SELECT 1`, which would keep the Neon
compute awake around the clock and exhaust the free plan's 100 CU-hours in about two weeks.
`/healthz` (and `/`) never touch the database, so Neon can still scale to zero between calls.

---

## 6. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/patients.db` | SQLAlchemy URL. `postgres://`/`postgresql://` are routed to the psycopg 3 driver automatically. |
| `AUTO_INIT_DB` | `true` | Create tables lazily on first request. |
| `SEED_DEMO_DATA` | `true` | Insert Jane Doe / Carlos Rivera when the table is empty. |
| `API_KEY` | *(empty = open)* | When set, `POST/PUT/DELETE /patients` require `X-API-Key` (or `Authorization: Bearer`). |
| `VAPI_WEBHOOK_SECRET` | *(empty = not enforced)* | Must match the `x-vapi-secret` header Vapi sends. |
| `CLINIC_NAME`, `AGENT_NAME` | Riverside Family Clinic, Riley | Persona in the prompt and dashboard. |
| `ENVIRONMENT`, `LOG_LEVEL` | development, INFO | Reported by `/health`; logging verbosity. |
| `PUBLIC_BASE_URL` | – | Deployment URL (used by the setup script and `/vapi/assistant-config`). |
| `VAPI_API_KEY`, `VAPI_AREA_CODE`, `VAPI_MODEL`, `VAPI_VOICE_PROVIDER`, `VAPI_VOICE_ID`, `VAPI_MULTILINGUAL`, `VAPI_ASSISTANT_ID`, `VAPI_PHONE_NUMBER_ID` | see `.env.example` | Only used by `scripts/vapi_setup.py`. |

No key is hard-coded anywhere; `.env` is git-ignored.

---

## 7. REST API

All responses use one envelope:

```json
{ "data": { ... }, "error": null }                       // success
{ "data": null, "error": { "code": "VALIDATION_ERROR", "message": "Validation failed",
                           "details": [ { "field": "phone_number", "message": "must be a valid 10-digit U.S. phone number" } ] } }
```

`GET /patients` additionally returns `"meta": { "total", "limit", "offset" }` for pagination; the
other list endpoints (`/calls`, `/appointments`, `/patients/{id}/calls`,
`/patients/{id}/appointments`) return a bare array in `data`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness + DB check |
| `GET` | `/patients` | Filters: `last_name` (case-insensitive exact), `date_of_birth` (`MM/DD/YYYY` or `YYYY-MM-DD`), `phone_number` (any U.S. format), `include_deleted`, `limit` (≤200), `offset` |
| `GET` | `/patients/{id}` | 404 if unknown or soft-deleted, 400 if not a UUID |
| `POST` | `/patients` | 201 with the created record; 422 with per-field details |
| `PUT` | `/patients/{id}` | Partial update; unknown fields → 422; `null` clears optional fields |
| `DELETE` | `/patients/{id}` | Soft delete (`deleted_at`); record stays in the DB |
| `GET` | `/calls`, `/calls/{call_id}`, `/patients/{id}/calls` | Call transcripts / summaries / recordings |
| `GET` | `/appointments`, `/patients/{id}/appointments` | Mock bookings made during calls |
| `POST` | `/vapi/webhook` | Vapi server URL (tool calls, end-of-call reports) |
| `GET` | `/vapi/assistant-config` | Prompt + tool definitions currently provisioned |
| `GET` | `/`, `/dashboard`, `/docs` | Dashboard, Swagger UI |

Status codes: `200`, `201`, `400` (malformed JSON, bad UUID, bad query param), `401` (API key
configured but missing), `404`, `422` (field validation), `500` (logged, generic message).

```bash
BASE=https://<your-app>.vercel.app
curl -s -X POST $BASE/patients -H 'Content-Type: application/json' -d '{
  "first_name":"Jane","last_name":"Davis","date_of_birth":"03/15/1985","sex":"Female",
  "phone_number":"(555) 123-4567","address_line_1":"123 Maple Street","address_line_2":"Apt 4B",
  "city":"Springfield","state":"Illinois","zip_code":"62704","email":"jane@example.com"}'
curl -s "$BASE/patients?last_name=Davis"
curl -s "$BASE/patients?phone_number=5551234567"
curl -s -X PUT $BASE/patients/<uuid> -H 'Content-Type: application/json' -d '{"city":"Chicago"}'
curl -s -X DELETE $BASE/patients/<uuid>
```

**Input normalisation (server-side, same for voice and API):** phone numbers → 10 digits
(`+1 (555) 123-4567` → `5551234567`); dates accept `MM/DD/YYYY`, ISO and spoken forms and are
stored/returned as `YYYY-MM-DD`; state names → USPS codes (`Illinois` → `IL`); ZIP+4 is
normalised to `12345-6789`; sex synonyms map to the four enum values; `"n/a"`, `""`, `"none"` on
optional fields become `null`; names allow letters, hyphens, apostrophes (and spaces).

---

## 8. Data model

`patients` (SQLAlchemy model in `app/db/models.py`, enforced by CHECK constraints in the DB):

| Column | Type | Rule |
|---|---|---|
| `patient_id` | UUID (PK) | auto-generated |
| `first_name`, `last_name` | varchar(50) | 1–50 chars, letters/hyphens/apostrophes |
| `date_of_birth` | date | valid, not in the future, ≥ 1900 |
| `sex` | varchar(20) | `Male`, `Female`, `Other`, `Decline to Answer` |
| `phone_number` | varchar(10) | exactly 10 digits (indexed) |
| `email` | varchar(254), null | valid email, lower-cased |
| `address_line_1` | varchar(200) | required |
| `address_line_2` | varchar(200), null | |
| `city` | varchar(100) | 1–100 chars |
| `state` | char(2) | USPS abbreviation (50 states + DC + territories) |
| `zip_code` | varchar(10) | `12345` or `12345-6789` |
| `insurance_provider`, `insurance_member_id` | varchar, null | member id alphanumeric |
| `preferred_language` | varchar(50) | default `English` |
| `emergency_contact_name`, `emergency_contact_phone` | varchar, null | phone = 10 digits |
| `created_at`, `updated_at` | timestamptz | UTC, auto |
| `deleted_at` | timestamptz, null | soft delete marker (indexed) |

`calls` stores one row per phone call (`call_id`, `patient_id` FK, caller number, status, ended
reason, timestamps, duration, cost, summary, transcript, recording URL). `appointments` stores the
mock bookings. Phone numbers are intentionally **not** unique (family members share phones);
duplicate detection is a conversational prompt, not a constraint.

---

## 9. The voice agent

### Prompt engineering
The full system prompt lives in [`app/voice/prompt.py`](app/voice/prompt.py) with a docstring
explaining each design decision. Highlights:

* **Voice-first output rules** – short turns, one question at a time, no lists/markdown, digits
  spoken in groups, dates spoken, names spelled back letter by letter.
* **Ordered but flexible collection** – required fields in a fixed order, yet whatever the caller
  volunteers is captured and only the missing pieces are asked for (handles out-of-order answers).
* **Optional fields are offered once as a group** (email, insurance, emergency contact,
  preferred language), exactly as the brief asks.
* **Mandatory read-back + explicit confirmation** before `register_patient` is ever called.
* **Field-specific re-prompts** – validation results list the offending fields with reasons;
  the prompt instructs the model to re-ask only those.
* **Corrections / start over / stop** – rules for "actually, it's D-A-V-I-S", "let's start over",
  and "never mind".
* **Honesty on failure** – if the write fails the model must say so and may retry once; it is
  forbidden from claiming success.
* **Live context** – Vapi substitutes today's date and the caller ID (`{{customer.number}}`) so
  the agent can offer "Is the number you're calling from the best one to reach you?".
* **Spanish** – with `VAPI_MULTILINGUAL=true` the transcriber becomes multilingual and the prompt
  switches to Spanish on request. In the default English-only mode the agent instead says, in
  Spanish, that registration can only be completed in English on this line and offers to continue
  slowly in English (it never promises what the speech pipeline cannot deliver).

### Tools (function calling)
| Tool | Purpose |
|---|---|
| `lookup_patient(phone_number?)` | Duplicate detection; without an argument it uses the caller ID and reports the number used |
| `register_patient(...)` | Validates + creates; returns `created` / `validation_error` (fields) / `duplicate` / `error` |
| `update_patient(patient_id, ...)` | Partial update for returning callers |
| `get_appointment_slots()` / `book_appointment(patient_id, slot_id)` | Mock first-appointment scheduling |
| `endCall` (Vapi built-in) | Hang up after the goodbye |

Each tool carries a Vapi-side `request-response-delayed` message (spoken if the backend is slow)
and a `request-failed` **system hint**: when Vapi cannot reach the backend at all, the model is
told that nothing was saved and composes an honest reply with an offer to retry, instead of a
canned sentence that would end the turn. `update_patient` additionally requires the date of
birth on file (`verified_date_of_birth`), enforced server-side, so nothing stored is read out
or changed for an unverified caller.

### Vapi configuration (`scripts/vapi_setup.py`)
Deepgram Nova-3 with `smartFormat` + `numerals` + clinic/insurance key-terms; LiveKit smart
endpointing plus a custom endpointing rule that holds for one second whenever the caller's
speech ends in a digit (phone numbers, dates and ZIPs are read in chunks); barge-in enabled
(`stopSpeakingPlan`); two `customer.speech.timeout` hooks ("are you still there?" at 15 s, polite
hang-up at 60 s); no platform `endCallMessage` (the model's own personalised goodbye is the only
one); 15-minute max duration; recordings + transcripts on; every webhook carries the
`x-vapi-secret` header (configured via `server.headers`).

### Example call
```
Riley:  Hi, thanks for calling Riverside Family Clinic! This is Riley. … could I have your first and last name?
Caller: Jane Davies.
Riley:  Thanks Jane. Is that D-A-V-I-E-S?
Caller: Actually it's D-A-V-I-S.
Riley:  Got it, Davis, D-A-V-I-S. What's your date of birth?
Caller: March 15th 2030.
Riley:  That date is in the future, so I must have misheard. Could you give me the month, day and year again?
…
Riley:  Let me make sure I have everything right. Jane Davis, D-A-V-I-S, born March fifteenth nineteen eighty-five, female,
        phone five five five, one two three, four five six seven, 123 Maple Street, apartment 4B, Springfield, Illinois,
        six two seven zero four. Is all of that correct?
Caller: Yes.
Riley:  Perfect, let me save that for you. … You're all set, Jane! Would you like to schedule your first appointment while you're on the line?
```

---

## 10. Edge cases and resilience

| Scenario | Behaviour |
|---|---|
| Invalid date of birth (future / Feb 30) | Rejected by the prompt first, and again server-side (`422` / `validation_error`); the agent re-asks only the date |
| 3-digit phone number | Same; the agent asks for the full 10-digit number |
| Caller corrects a field mid-way | Prompt rule: replace the value, confirm it, continue without restarting |
| Caller answers out of order / several at once | Captured; only missing items are asked |
| "Start over" | Everything discarded, flow restarts at the name |
| Database write fails | Tool returns `status: error`; the agent tells the caller it could not save, offers one retry, never claims success. Backend unreachable/timeouts → Vapi hands the model a `request-failed` system hint and the model says so honestly and offers a retry |
| Call drops mid-conversation | Nothing is written until confirmation, so no partial records; the `calls` row records `ended_reason` and transcript |
| Silence | "Are you still there?" after 15 s (×2), polite hang-up after 60 s; hard cap 15 min |
| Same phone number calls again | `lookup_patient` finds the record → "It looks like we already have a record for … update instead?"; the stored details are never read out, and `update_patient` only proceeds when the stated date of birth matches the record (`identity_mismatch` otherwise) |
| Bad JSON / unknown field / bad UUID at the API | `400` / `422` / `400` with the envelope; unknown routes return a JSON 404 |
| Webhook from an unknown source | `401` when `VAPI_WEBHOOK_SECRET` is set |

---

## 11. Observability

* Every request is access-logged (`METHOD path -> status (ms)`).
* Conversation events are single-line JSON on stdout: `tool_call`, `tool_result`,
  **`patient_registered`** (the final collected payload), `patient_updated`,
  `appointment_booked`, `end_of_call_report` (summary + transcript).
* Transcripts, summaries, recording links and cost are persisted per call and shown on the
  dashboard (Calls tab) – no need to open the Vapi console.

---

## 12. Security

* No secrets in code; all configuration via environment variables.
* Input sanitisation: strict Pydantic schemas (`extra="forbid"`), whitespace normalisation,
  length limits, enum/regex checks, parameterised SQL via SQLAlchemy, HTML-escaped dashboard.
* Optional `X-API-Key` for mutating endpoints and `x-vapi-secret` for the webhook, both compared
  in constant time. Left open by default so reviewers can test freely.
* Soft delete only; nothing is hard-deleted through the API.

---

## 13. Tests

```bash
pytest           # 101 tests, ~1 s, in-memory SQLite
```

Coverage: envelope and status codes for every endpoint, all normalisation rules, partial
updates, soft delete, API-key protection, seed data, Vapi tool-call parsing (object and JSON-string
arguments), duplicate → update flow, validation feedback, database-failure relay, appointment
flow, end-of-call-report storage, webhook secret enforcement.

---

## 14. Bonus features implemented

* **Duplicate detection** by phone number (caller-ID aware) with "update instead?" flow, gated
  by a server-enforced date-of-birth check before anything on the record is changed.
* **Appointment scheduling** (mock slots, persisted, shown on the dashboard).
* **Call transcript / summary / recording** stored per call and linked to the patient.
* **Dashboard** at `/` (patients, calls with transcripts, appointments, search).
* **Automated tests** for the API and webhook layers.
* **Multi-language**: full Spanish conversation when provisioned with `VAPI_MULTILINGUAL=true`;
  in the default English-only mode the agent explains the limitation in Spanish and continues in
  English rather than mis-transcribing a Spanish caller.

---

## 15. Known limitations and trade-offs

* **Credit budget.** The free Vapi balance covers roughly an hour of calls; when it reaches $0
  the number stops answering. Keep test calls short or add a Deepgram key (see 5.3).
* **Cold starts.** Vercel Hobby has no pre-warming: the first request after a quiet period costs
  ~1–3 s (plus ~1 s if Neon was idle). Tool timeouts are 20 s, so this is safe but noticeable.
* **English-first transcription.** Multilingual mode (`VAPI_MULTILINGUAL=true`) is opt-in
  because it slightly reduces English accuracy on names and digits; in the default mode a
  Spanish-speaking caller is told, in Spanish, that this line completes registration in English.
* **Phone validation is "10 digits"** (as specified); full NANP rules (area code cannot start with
  0/1) were deliberately not enforced to avoid rejecting reviewers' test numbers.
* **Schema creation via `create_all`** instead of migrations – fine for a fresh project; Alembic
  would be the next step.
* **Not HIPAA-compliant** (no BAA, recordings stored by Vapi) – intentional for an assessment.
* Appointment slots are generated, not backed by a real calendar.

## 16. Next steps

1. Alembic migrations and a `patients.phone_number` partial index for faster duplicate checks.
2. Structured-output extraction at call end (Vapi `artifactPlan.structuredOutputs`) to
   cross-check what the model saved against the transcript.
3. Outbound confirmation SMS / email after registration.
4. Rate limiting and per-client API keys for the public API.
5. Eval suite: scripted callers (Vapi test suites) that replay tricky conversations (spelling
   corrections, interruptions, Spanish) and assert on the stored record.
