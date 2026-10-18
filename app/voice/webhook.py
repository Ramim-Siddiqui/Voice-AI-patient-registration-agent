"""Vapi server-URL webhook.

Vapi POSTs every server event as ``{"message": {"type": ..., ...}}``.  We handle

* ``tool-calls``          -> run the requested function tools and answer with
                             ``{"results": [{"toolCallId", "result"}]}``
* ``end-of-call-report``  -> persist transcript / summary / recording, log the report

Everything else is acknowledged with an empty 200 so Vapi never retries.

Security: when ``VAPI_WEBHOOK_SECRET`` is configured, the ``x-vapi-secret``
header must match.  Vapi's current ``Server`` schema has no ``secret`` field;
the header arrives because ``scripts/vapi_setup.py`` puts
``{"x-vapi-secret": <secret>}`` into ``server.headers`` on the assistant and on
every function tool (if you configure the assistant by hand, add the same
header under the tool/assistant server settings).
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.envelope import BadRequestError, UnauthorizedError, ok
from app.config import get_settings
from app.db.session import get_db
from app.logging_utils import log_event
from app.services.call_service import CallService
from app.voice.handlers import CallContext, handle_tool_call
from app.voice.prompt import build_first_message, build_system_prompt
from app.voice.tools import build_tools

log = logging.getLogger("app.voice")

router = APIRouter(prefix="/vapi", tags=["voice"])


def _verify_secret(request: Request) -> None:
    expected = get_settings().vapi_webhook_secret
    if not expected:
        return
    provided = request.headers.get("x-vapi-secret", "")
    # Compare bytes so a non-ASCII header value cannot raise (-> 500) instead of 401.
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise UnauthorizedError("invalid webhook secret")


def _call_context(message: dict[str, Any]) -> CallContext:
    call = message.get("call") or {}
    customer = call.get("customer") or message.get("customer") or {}
    return CallContext(
        call_id=call.get("id") or message.get("callId"),
        caller_number=customer.get("number"),
        started_at=call.get("startedAt") or call.get("createdAt"),
    )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Vapi sends ``function.arguments`` as an object, but be tolerant of a JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _handle_tool_calls(message: dict[str, Any], db: Session) -> dict[str, Any]:
    ctx = _call_context(message)
    tool_calls = message.get("toolCallList") or message.get("toolCalls") or []
    results = []
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function") or item
        name = function.get("name") or ""
        args = _parse_arguments(function.get("arguments"))
        result = handle_tool_call(name, args, ctx, db)
        # Vapi requires `result` to be a flat, single-line string (JSON text is fine).
        text = json.dumps(result, ensure_ascii=False).replace("\n", " ").replace("\r", " ")
        results.append({"name": name, "toolCallId": item.get("id"), "result": text})
    return {"results": results}


def _recording_url(artifact: dict[str, Any], message: dict[str, Any], recording: dict[str, Any]) -> str | None:
    """Vapi has moved the recording URL around between versions; try each known spot."""
    mono = recording.get("mono") if isinstance(recording.get("mono"), dict) else {}
    for candidate in (
        artifact.get("recordingUrl"),
        artifact.get("stereoRecordingUrl"),
        message.get("recordingUrl"),
        message.get("stereoRecordingUrl"),
        recording.get("stereoUrl"),
        mono.get("combinedUrl"),
    ):
        if isinstance(candidate, str) and candidate:
            return candidate[:500]
    return None


def _handle_end_of_call(message: dict[str, Any], db: Session) -> dict[str, Any]:
    ctx = _call_context(message)
    call = message.get("call") or {}
    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    recording = artifact.get("recording") or {}

    transcript = artifact.get("transcript") or message.get("transcript")
    summary = analysis.get("summary") or message.get("summary")
    recording_url = _recording_url(artifact, message, recording)
    cost = message.get("cost", call.get("cost"))

    duration_seconds = None
    if ctx.call_id:
        stored = CallService(db).finalize(
            ctx.call_id,
            caller_number=ctx.caller_number,
            ended_reason=message.get("endedReason"),
            started_at=message.get("startedAt") or call.get("startedAt"),
            ended_at=message.get("endedAt") or call.get("endedAt"),
            summary=summary,
            transcript=transcript,
            recording_url=recording_url,
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        )
        duration_seconds = stored.duration_seconds  # derived from startedAt/endedAt

    # Observability requirement: summary + transcript of every call go to the logs.
    log_event(
        log,
        "end_of_call_report",
        call_id=ctx.call_id,
        caller_number=ctx.caller_number,
        ended_reason=message.get("endedReason"),
        duration_seconds=duration_seconds,
        cost_usd=cost,
        summary=summary,
        transcript=transcript,
    )
    return {}


@router.post("/webhook", summary="Vapi server URL (tool calls + end-of-call reports)")
def vapi_webhook(request: Request, payload: Any = Body(...), db: Session = Depends(get_db)):
    # Plain ``def`` on purpose: the handlers do blocking database I/O, so FastAPI
    # runs this in its threadpool instead of blocking the event loop.
    _verify_secret(request)
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        raise BadRequestError("missing 'message' object")

    message_type = message.get("type")
    if message_type == "tool-calls":
        return _handle_tool_calls(message, db)
    if message_type == "end-of-call-report":
        return _handle_end_of_call(message, db)

    log_event(log, "vapi_message_ignored", type=message_type, call_id=_call_context(message).call_id)
    return {}


@router.get("/assistant-config", summary="The prompt + tool definitions provisioned on Vapi (no secrets)")
def assistant_config():
    settings = get_settings()
    base = settings.base_url or "https://<your-deployment>"
    return ok(
        {
            "first_message": build_first_message(settings.clinic_name, settings.agent_name),
            "system_prompt": build_system_prompt(
                settings.clinic_name, settings.agent_name, spanish_enabled=settings.vapi_multilingual
            ),
            "tools": build_tools(f"{base.rstrip('/')}/vapi/webhook", None),
        }
    )
