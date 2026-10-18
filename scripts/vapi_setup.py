#!/usr/bin/env python
"""Provision (or update) the Vapi assistant, its tools and the free phone number.

Usage (from the project root, inside the venv):

    python scripts/vapi_setup.py                 # create/update assistant + phone number
    python scripts/vapi_setup.py --dry-run       # print the assistant JSON, call nothing
    python scripts/vapi_setup.py --skip-phone    # assistant only
    python scripts/vapi_setup.py --print-prompt  # show the system prompt and exit

Required environment (``.env`` is loaded automatically):
    VAPI_API_KEY        private key from Dashboard -> Vapi API Keys
    PUBLIC_BASE_URL     where this backend is reachable, e.g. https://xyz.vercel.app

Optional:
    VAPI_WEBHOOK_SECRET shared secret; also set it on the backend (same value)
    VAPI_ASSISTANT_ID / VAPI_PHONE_NUMBER_ID   reuse existing objects (else .vapi-state.json).
                        If you created the number in the dashboard, put its ID (the UUID on the
                        number's page, not the digits) in VAPI_PHONE_NUMBER_ID or run --skip-phone.
    VAPI_AREA_CODE      3-digit US area code for the free number (default 415)
    VAPI_MODEL          OpenAI model id on Vapi (default gpt-4.1-mini)
    VAPI_VOICE_PROVIDER / VAPI_VOICE_ID   default deepgram / thalia (Aura-2); built-in defaults
                        exist for deepgram, vapi and openai - other providers need VAPI_VOICE_ID
    VAPI_MULTILINGUAL   "true" switches the transcriber to multilingual (English + Spanish)
    CLINIC_NAME / AGENT_NAME               persona used in the prompt

The script is idempotent: ids are stored in ``.vapi-state.json`` so re-running
updates the same assistant instead of creating duplicates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.voice.prompt import build_first_message, build_system_prompt  # noqa: E402
from app.voice.tools import build_tools  # noqa: E402

API_BASE = "https://api.vapi.ai"
STATE_FILE = ROOT / ".vapi-state.json"

# Vocabulary hints for Deepgram Nova-3 (improves recognition of these phrases).
KEYTERMS = [
    "ZIP code", "date of birth", "apartment", "suite", "emergency contact", "member ID",
    "Blue Cross Blue Shield", "Aetna", "Cigna", "UnitedHealthcare", "Humana", "Kaiser",
    "Medicare", "Medicaid", "Anthem", "decline to answer",
]

# Sensible default voice per provider (VAPI_VOICE_ID overrides).
DEFAULT_VOICE_IDS = {"deepgram": "thalia", "vapi": "Elliot", "openai": "alloy"}


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


class Config:
    def __init__(self) -> None:
        self.api_key = env("VAPI_API_KEY")
        self.base_url = (env("PUBLIC_BASE_URL") or "").rstrip("/")
        self.secret = env("VAPI_WEBHOOK_SECRET")
        self.assistant_id = env("VAPI_ASSISTANT_ID")
        self.phone_number_id = env("VAPI_PHONE_NUMBER_ID")
        self.area_code = env("VAPI_AREA_CODE", "415")
        self.model = env("VAPI_MODEL", "gpt-4.1-mini")
        self.voice_provider = env("VAPI_VOICE_PROVIDER", "deepgram")
        self.voice_id = env("VAPI_VOICE_ID", DEFAULT_VOICE_IDS.get(self.voice_provider))
        self.multilingual = truthy(env("VAPI_MULTILINGUAL", "false"))
        self.clinic_name = env("CLINIC_NAME", "Riverside Family Clinic")
        self.agent_name = env("AGENT_NAME", "Riley")


def build_voice(cfg: Config) -> dict:
    voice: dict = {"provider": cfg.voice_provider, "voiceId": cfg.voice_id}
    if cfg.voice_provider == "deepgram":
        voice["model"] = "aura-2"
    elif cfg.voice_provider == "vapi":
        voice["version"] = 2  # multilingual Vapi voices with automatic language detection
        voice["language"] = "auto"
    elif cfg.voice_provider == "openai":
        voice["model"] = "gpt-4o-mini-tts"
    return voice


def build_assistant(cfg: Config) -> dict:
    """The full assistant definition (see app/voice/prompt.py for the prompt rationale)."""
    webhook_url = f"{cfg.base_url}/vapi/webhook"
    server: dict = {"url": webhook_url, "timeoutSeconds": 20}
    if cfg.secret:
        server["headers"] = {"x-vapi-secret": cfg.secret}

    # Includes the "LIVE CALL CONTEXT" block with Liquid variables ({{ customer.number }} etc.)
    # that Vapi substitutes when the call starts.
    prompt = build_system_prompt(
        cfg.clinic_name, cfg.agent_name, include_live_context=True, spanish_enabled=cfg.multilingual
    )

    transcriber: dict = {
        "provider": "deepgram",
        "model": "nova-3",
        "language": "multi" if cfg.multilingual else "en",
        "smartFormat": True,  # "five five five one two three" -> digits, better for the LLM
        "numerals": True,
        "keyterm": KEYTERMS,
    }

    return {
        "name": f"{cfg.clinic_name} Registration"[:40],
        "firstMessage": build_first_message(cfg.clinic_name, cfg.agent_name),
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": cfg.model,
            "temperature": 0.3,
            "maxTokens": 300,
            "messages": [{"role": "system", "content": prompt}],
            "tools": build_tools(webhook_url, cfg.secret),
        },
        "transcriber": transcriber,
        "voice": build_voice(cfg),
        "server": server,
        "serverMessages": ["tool-calls", "end-of-call-report"],
        "maxDurationSeconds": 900,
        "backgroundSound": "off",
        # No endCallMessage: the prompt already has the model say a personalised
        # goodbye before calling endCall, so a platform-level message would be a
        # second goodbye on every call.
        # Silence handling (replaces the removed silenceTimeoutSeconds setting).
        "hooks": [
            {
                "name": "silence_check",
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 15, "triggerMaxCount": 2, "triggerResetMode": "onUserSpeech"},
                # "exact" text: a generated line ("prompt") made the model narrate its instructions aloud.
                "do": [{"type": "say", "exact": "Are you still there? Take your time."}],
            },
            {
                "name": "silence_hangup",
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 60, "triggerMaxCount": 1},
                "do": [
                    {"type": "say", "exact": "I haven't heard anything for a little while, so I'll let you go for now. Feel free to call back any time. Goodbye!"},
                    {"type": "tool", "tool": {"type": "endCall"}},
                ],
            },
        ],
        # Post-call summary shown on the dashboard (field is marked deprecated in the
        # API spec but still honoured; transcripts are stored regardless).
        "analysisPlan": {"summaryPlan": {"enabled": True}},
        # Turn-taking: LiveKit endpointing is best for English; Vapi's own works for any language.
        "startSpeakingPlan": {
            "waitSeconds": 0.5,
            "smartEndpointingPlan": {"provider": "vapi" if cfg.multilingual else "livekit"},
            # Callers read phone numbers / dates / ZIPs / member IDs in chunks.
            # transcriptionEndpointingPlan is ignored while smartEndpointingPlan is set,
            # but customEndpointingRules take precedence over both: hold for 1 s whenever
            # the caller's live transcript ends in a digit (spoken or numeric).
            "customEndpointingRules": [
                {
                    "type": "customer",
                    "regex": r"(\d|zero|one|two|three|four|five|six|seven|eight|nine|oh)[\s.,;:!?)-]*$",
                    "regexOptions": [{"type": "ignore-case", "enabled": True}],
                    "timeoutSeconds": 1.0,
                }
            ],
        },
        "stopSpeakingPlan": {"numWords": 0, "voiceSeconds": 0.2, "backoffSeconds": 1},
        "artifactPlan": {
            "recordingEnabled": True,
            "recordingFormat": "mp3",
            "transcriptPlan": {"enabled": True, "assistantName": cfg.agent_name, "userName": "Caller"},
        },
    }


# ---------------------------------------------------------------------------
# Vapi API helpers
# ---------------------------------------------------------------------------


class Vapi:
    def __init__(self, api_key: str):
        self.client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=60,
        )

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        response = self.client.request(method, path, json=body)
        if response.status_code >= 400:
            print(f"\n[vapi] {method} {path} -> HTTP {response.status_code}")
            try:
                print(json.dumps(response.json(), indent=2))
            except ValueError:
                print(response.text)
            raise SystemExit(1)
        return response.json() if response.content else {}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_assistant(vapi: Vapi, cfg: Config, state: dict) -> str:
    body = build_assistant(cfg)
    assistant_id = cfg.assistant_id or state.get("assistant_id")
    if assistant_id:
        print(f"[vapi] updating assistant {assistant_id} ...")
        vapi.request("PATCH", f"/assistant/{assistant_id}", body)
    else:
        print("[vapi] creating assistant ...")
        created = vapi.request("POST", "/assistant", body)
        assistant_id = created["id"]
        print(f"[vapi] created assistant {assistant_id}")
    state["assistant_id"] = assistant_id
    save_state(state)
    return assistant_id


def ensure_phone_number(vapi: Vapi, cfg: Config, state: dict, assistant_id: str) -> dict | None:
    phone_id = cfg.phone_number_id or state.get("phone_number_id")
    if phone_id:
        print(f"[vapi] attaching assistant to phone number {phone_id} ...")
        number = vapi.request("PATCH", f"/phone-number/{phone_id}", {"assistantId": assistant_id})
    else:
        print(f"[vapi] requesting a free Vapi phone number (area code {cfg.area_code}) ...")
        number = vapi.request(
            "POST",
            "/phone-number",
            {
                "provider": "vapi",
                "assistantId": assistant_id,
                "numberDesiredAreaCode": cfg.area_code,
                "name": f"{cfg.clinic_name} line"[:40],
            },
        )
        phone_id = number["id"]
    state["phone_number_id"] = phone_id

    # Free numbers take a few minutes to activate.
    deadline = time.time() + 240
    while number.get("status") not in ("active", "blocked") and time.time() < deadline:
        print(f"[vapi] number status: {number.get('status')} ... waiting")
        time.sleep(10)
        number = vapi.request("GET", f"/phone-number/{phone_id}")

    state["phone_number"] = number.get("number")
    state["phone_number_status"] = number.get("status")
    save_state(state)
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the assistant JSON and exit")
    parser.add_argument("--skip-phone", action="store_true", help="do not create/attach a phone number")
    parser.add_argument("--print-prompt", action="store_true", help="print the system prompt and exit")
    args = parser.parse_args()

    cfg = Config()

    if args.print_prompt:
        print(build_system_prompt(cfg.clinic_name, cfg.agent_name))
        return

    if not cfg.base_url:
        sys.exit("PUBLIC_BASE_URL is required (e.g. https://your-app.vercel.app)")
    if not cfg.base_url.startswith("https://"):
        sys.exit("PUBLIC_BASE_URL must be an https:// URL reachable from the internet")
    if cfg.base_url.startswith("https://your-app."):
        sys.exit("PUBLIC_BASE_URL still holds the .env.example placeholder; set it to your real deployment URL")
    if not cfg.voice_id:
        sys.exit(
            f"VAPI_VOICE_ID is required when VAPI_VOICE_PROVIDER={cfg.voice_provider!r} "
            f"(built-in defaults exist only for: {', '.join(DEFAULT_VOICE_IDS)})"
        )

    if args.dry_run:
        print(json.dumps(build_assistant(cfg), indent=2))
        return

    if not cfg.api_key:
        sys.exit("VAPI_API_KEY is required (Dashboard -> Vapi API Keys -> private key)")

    vapi = Vapi(cfg.api_key)
    state = load_state()

    assistant_id = ensure_assistant(vapi, cfg, state)

    number = None
    if not args.skip_phone:
        number = ensure_phone_number(vapi, cfg, state, assistant_id)

    print("\n================ DONE ================")
    print(f"Assistant id   : {assistant_id}")
    print(f"Webhook        : {cfg.base_url}/vapi/webhook")
    print(f"Model / voice  : {cfg.model} / {cfg.voice_provider}:{cfg.voice_id}")
    if number:
        print(f"Phone number   : {number.get('number') or '(pending)'}  status={number.get('status')}")
        if number.get("status") != "active":
            print("  The number is still activating; check Dashboard -> Phone Numbers in a few minutes.")
    else:
        print("Phone number   : skipped (attach one in Dashboard -> Phone Numbers -> Inbound Settings)")
    print(f"Dashboard      : {cfg.base_url}/")
    print("State saved to : .vapi-state.json (git-ignored)")


if __name__ == "__main__":
    main()
