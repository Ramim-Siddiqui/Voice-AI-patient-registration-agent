"""Tool-call handlers for the voice agent.

Each handler receives the arguments the LLM produced plus the call context and
returns a plain dict.  Every result carries

* ``status``  - machine-readable outcome (``created``, ``found``, ``validation_error`` ...)
* ``message`` - a short instruction written *for the LLM* telling it what to say/do next

Validation always goes through the same Pydantic schemas as the REST API, so
the voice channel can never store something the API would reject.

Privacy: ``lookup_patient`` only reveals the name on an existing record, and
``update_patient`` refuses to touch a record unless the caller states the date
of birth on file (``verified_date_of_birth``).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import Patient
from app.logging_utils import log_event
from app.schemas.patient import (
    PatientCreate,
    PatientOut,
    PatientUpdate,
    format_validation_errors,
    normalize_us_phone,
    parse_date_of_birth,
)
from app.services.appointment_service import AppointmentService, spoken_time
from app.services.call_service import CallService
from app.services.patient_service import PatientNotFoundError, PatientService
from app.voice.tools import (
    PATIENT_FIELD_NAMES,
    TOOL_BOOK,
    TOOL_LOOKUP,
    TOOL_REGISTER,
    TOOL_SLOTS,
    TOOL_UPDATE,
)

log = logging.getLogger("app.voice")


@dataclass
class CallContext:
    call_id: str | None = None
    caller_number: str | None = None
    started_at: str | None = None


def handle_tool_call(name: str, args: dict[str, Any], ctx: CallContext, db: Session) -> dict[str, Any]:
    """Dispatch one tool call; never raises (errors become a spoken-friendly result)."""
    handler = _HANDLERS.get(name)
    log_event(log, "tool_call", call_id=ctx.call_id, tool=name, arguments=args)
    if handler is None:
        result: dict[str, Any] = {"status": "error", "message": f"Unknown tool '{name}'. Do not retry it."}
    else:
        try:
            result = handler(args or {}, ctx, db)
        except SQLAlchemyError:
            log.exception("database error while handling tool %s", name)
            db.rollback()
            result = _failure(name)
        except Exception:  # noqa: BLE001 - last line of defence: the caller must never hear silence
            log.exception("unexpected error while handling tool %s", name)
            db.rollback()
            result = _failure(name)
    log_event(log, "tool_result", call_id=ctx.call_id, tool=name, status=result.get("status"))
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_SAVE_FAILED = (
    "The records system failed to save. Tell the caller honestly that you could not save their "
    "information right now and ask whether they would like you to try once more. If it fails "
    "again, apologise and suggest calling back later. Do NOT say it was saved."
)
_FAILURE_MESSAGES = {
    TOOL_LOOKUP: (
        "The records lookup failed; nothing was saved. Continue with the new registration as "
        "normal - the save step will still detect an existing record."
    ),
    TOOL_REGISTER: _SAVE_FAILED,
    TOOL_UPDATE: _SAVE_FAILED,
    TOOL_SLOTS: (
        "Scheduling is unavailable right now. The caller's registration is already saved - say so, "
        "tell them the front desk can book their appointment, and continue."
    ),
    TOOL_BOOK: (
        "The booking could not be completed. The caller's registration is already saved - say so, "
        "tell them the front desk can book their appointment, and continue."
    ),
}


def _failure(tool_name: str) -> dict[str, Any]:
    return {"status": "error", "message": _FAILURE_MESSAGES.get(tool_name, _SAVE_FAILED)}


def _patient_summary(patient: Patient) -> dict[str, Any]:
    data = PatientOut.model_validate(patient).model_dump(mode="json")
    data["date_of_birth_spoken"] = patient.date_of_birth.strftime("%B %d, %Y").replace(" 0", " ")
    return data


def _validation_result(errors: list[dict[str, str]]) -> dict[str, Any]:
    problems = "; ".join(f"{e['field']}: {e['message']}" for e in errors)
    return {
        "status": "validation_error",
        "errors": errors,
        "message": (
            f"Not saved. Invalid: {problems}. First check whether you can fix a value yourself from "
            "what the caller already said (for example reformat a date as MM/DD/YYYY or a phone "
            "number as ten digits). Only re-ask the caller for details that were truly invalid, "
            "explaining in plain words what was wrong, then confirm and call the tool again."
        ),
    }


def _patient_args(args: dict[str, Any]) -> dict[str, Any]:
    """Keep only known patient fields; drop empty values so defaults apply."""
    return {k: v for k, v in args.items() if k in PATIENT_FIELD_NAMES and v is not None}


def _parse_patient_id(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _found_result(patient: Patient, phone: str, source: str) -> dict[str, Any]:
    name = f"{patient.first_name} {patient.last_name}"
    return {
        "status": "found",
        "phone_number": phone,
        "phone_number_source": source,
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "on_file": {
            "email": patient.email is not None,
            "insurance": patient.insurance_provider is not None,
            "emergency_contact": patient.emergency_contact_name is not None,
            "preferred_language": patient.preferred_language,
        },
        "message": (
            f"An existing record was found for {name}. Say: \"It looks like we already have a record for "
            f"{name}. Would you like to update your information instead?\" If yes: ask for their date of "
            "birth (do not read anything from the record before that), ask what changed, read the changes "
            "back, then call update_patient with this patient_id, verified_date_of_birth and only the "
            "changed fields. If nothing needs changing, say the record is already up to date and save "
            "nothing. If the caller is a different person, continue a new registration and call "
            "register_patient with allow_duplicate_phone=true."
        ),
    }


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def lookup_patient(args: dict[str, Any], ctx: CallContext, db: Session) -> dict[str, Any]:
    raw_phone = args.get("phone_number") or ctx.caller_number
    source = "provided" if args.get("phone_number") else "caller_id"
    if ctx.call_id:
        CallService(db).touch(ctx.call_id, caller_number=ctx.caller_number, started_at=ctx.started_at)

    if not raw_phone:
        return {
            "status": "no_caller_id",
            "message": "Caller ID is not available. Ask the caller for their 10-digit phone number, then call lookup_patient with it.",
        }
    try:
        phone = normalize_us_phone(raw_phone)
    except ValueError as exc:
        if source == "caller_id":
            return {
                "status": "no_caller_id",
                "message": "The caller ID is not a usable U.S. number. Ask the caller for their 10-digit phone number, then call lookup_patient with it.",
            }
        return _validation_result([{"field": "phone_number", "message": str(exc)}])

    matches = PatientService(db).find_by_phone(phone)
    spoken = f"{phone[:3]}-{phone[3:6]}-{phone[6:]}"
    if not matches:
        return {
            "status": "not_found",
            "phone_number": phone,
            "phone_number_source": source,
            "message": f"No existing record for {spoken}. Continue the new registration.",
        }
    return _found_result(matches[0], phone, source)


def register_patient(args: dict[str, Any], ctx: CallContext, db: Session) -> dict[str, Any]:
    allow_duplicate = bool(args.get("allow_duplicate_phone", False))
    try:
        data = PatientCreate(**_patient_args(args))
    except ValidationError as exc:
        return _validation_result(format_validation_errors(exc))

    service = PatientService(db)
    if not allow_duplicate:
        existing = service.find_by_phone(data.phone_number)
        if existing:
            patient = existing[0]
            return {
                "status": "duplicate",
                "patient_id": str(patient.patient_id),
                "first_name": patient.first_name,
                "last_name": patient.last_name,
                "message": (
                    f"Not saved: a record for {patient.first_name} {patient.last_name} already uses this "
                    "phone number. Ask whether they want to update that record instead (verify their date "
                    "of birth, then call update_patient with this patient_id). If they are a different "
                    "person, call register_patient again with allow_duplicate_phone=true."
                ),
            }

    patient = service.create_patient(data)
    if ctx.call_id:
        CallService(db).touch(ctx.call_id, caller_number=ctx.caller_number, patient_id=patient.patient_id, started_at=ctx.started_at)

    # Observability requirement: the final collected payload goes to the logs.
    log_event(log, "patient_registered", call_id=ctx.call_id, patient=_patient_summary(patient))
    return {
        "status": "created",
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "message": (
            f"Registration saved successfully. Tell the caller: \"You're all set, {patient.first_name}!\" "
            "Then offer, once, to schedule a first appointment."
        ),
    }


def update_patient(args: dict[str, Any], ctx: CallContext, db: Session) -> dict[str, Any]:
    patient_id = _parse_patient_id(args.get("patient_id"))
    if patient_id is None:
        return {
            "status": "error",
            "message": "patient_id is missing or invalid. Call lookup_patient again and pass the patient_id it returns.",
        }
    verified_raw = args.get("verified_date_of_birth")
    if not verified_raw:
        return {
            "status": "error",
            "message": "verified_date_of_birth is required: ask the caller for their date of birth first, then call update_patient again with it.",
        }
    try:
        verified_dob = parse_date_of_birth(verified_raw)
    except ValueError as exc:
        return _validation_result([{"field": "verified_date_of_birth", "message": str(exc)}])

    service = PatientService(db)
    patient = service.get_patient(patient_id)
    if patient is None:
        return {
            "status": "not_found",
            "message": "No active record has that patient_id. Call lookup_patient again to get the correct id; only register a new patient if the lookup finds nothing.",
        }
    if patient.date_of_birth != verified_dob:
        log_event(log, "identity_mismatch", call_id=ctx.call_id, patient_id=str(patient_id))
        return {
            "status": "identity_mismatch",
            "message": (
                "The date of birth does not match the record, so it cannot be updated. Do not reveal "
                "anything from the record. Tell the caller you are unable to update it over the phone "
                "and offer to register them as a new patient instead (register_patient with "
                "allow_duplicate_phone=true)."
            ),
        }

    changes = _patient_args(args)
    if not changes:
        return {"status": "error", "message": "No fields to update were provided. Ask the caller what they would like to change."}
    try:
        data = PatientUpdate(**changes)
    except ValidationError as exc:
        return _validation_result(format_validation_errors(exc))

    try:
        patient = service.update_patient(patient_id, data)
    except PatientNotFoundError:
        return {"status": "not_found", "message": "That patient record no longer exists. Call lookup_patient again."}

    if ctx.call_id:
        CallService(db).touch(ctx.call_id, caller_number=ctx.caller_number, patient_id=patient.patient_id, started_at=ctx.started_at)
    log_event(log, "patient_updated", call_id=ctx.call_id, updated_fields=sorted(data.changes()), patient=_patient_summary(patient))
    return {
        "status": "updated",
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "updated_fields": sorted(data.changes()),
        "message": f"Changes saved. Confirm to the caller that their record is updated, {patient.first_name}.",
    }


def get_appointment_slots(args: dict[str, Any], ctx: CallContext, db: Session) -> dict[str, Any]:
    slots = AppointmentService(db).available_slots()
    return {
        "status": "ok",
        "slots": slots,
        "message": "Offer two or three of these options naturally (say the 'spoken' form). Book the chosen one with book_appointment using its slot_id.",
    }


def book_appointment(args: dict[str, Any], ctx: CallContext, db: Session) -> dict[str, Any]:
    patient_id = _parse_patient_id(args.get("patient_id"))
    if patient_id is None or PatientService(db).get_patient(patient_id) is None:
        return {"status": "error", "message": "A valid patient_id is required; the patient must be registered first."}
    try:
        appointment = AppointmentService(db).book(
            patient_id=patient_id, slot_id=str(args.get("slot_id", "")), reason=args.get("reason")
        )
    except ValueError as exc:
        return {"status": "error", "message": f"{exc}. Call get_appointment_slots again and offer the current options."}

    spoken = spoken_time(appointment.scheduled_for)
    log_event(log, "appointment_booked", call_id=ctx.call_id, patient_id=str(patient_id), scheduled_for=appointment.scheduled_for.isoformat(), provider=appointment.provider_name)
    return {
        "status": "booked",
        "appointment_id": str(appointment.appointment_id),
        "scheduled_for_spoken": spoken,
        "provider_name": appointment.provider_name,
        "message": f"Booked. Confirm: {spoken} with {appointment.provider_name}.",
    }


_HANDLERS = {
    TOOL_LOOKUP: lookup_patient,
    TOOL_REGISTER: register_patient,
    TOOL_UPDATE: update_patient,
    TOOL_SLOTS: get_appointment_slots,
    TOOL_BOOK: book_appointment,
}
