"""Read-only endpoints for call transcripts and (mock) appointments.

These back the dashboard and the "call recording / transcript" bonus.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.envelope import BadRequestError, NotFoundError, ok
from app.db.models import Appointment, Call
from app.db.session import get_db
from app.schemas.patient import to_utc_iso
from app.services.appointment_service import AppointmentService
from app.services.call_service import CallService
from app.services.patient_service import PatientService

router = APIRouter(tags=["calls"])


def serialize_call(call: Call) -> dict:
    return {
        "call_id": call.call_id,
        "patient_id": str(call.patient_id) if call.patient_id else None,
        "caller_number": call.caller_number,
        "status": call.status,
        "ended_reason": call.ended_reason,
        "started_at": to_utc_iso(call.started_at),
        "ended_at": to_utc_iso(call.ended_at),
        "duration_seconds": call.duration_seconds,
        "cost_usd": call.cost_usd,
        "summary": call.summary,
        "transcript": call.transcript,
        "recording_url": call.recording_url,
        "created_at": to_utc_iso(call.created_at),
        "updated_at": to_utc_iso(call.updated_at),
    }


def serialize_appointment(appt: Appointment) -> dict:
    return {
        "appointment_id": str(appt.appointment_id),
        "patient_id": str(appt.patient_id),
        "scheduled_for": to_utc_iso(appt.scheduled_for),
        "provider_name": appt.provider_name,
        "reason": appt.reason,
        "status": appt.status,
        "created_at": to_utc_iso(appt.created_at),
    }


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise BadRequestError("patient id must be a valid UUID") from None


@router.get("/calls", summary="Recent calls handled by the voice agent")
def list_calls(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    calls = CallService(db).list_calls(limit=limit)
    return ok([serialize_call(c) for c in calls])


@router.get("/calls/{call_id}", summary="One call with its transcript")
def get_call(call_id: str, db: Session = Depends(get_db)):
    call = CallService(db).get_call(call_id)
    if call is None:
        raise NotFoundError("call not found")
    return ok(serialize_call(call))


@router.get("/patients/{patient_id}/calls", summary="Calls linked to a patient")
def patient_calls(patient_id: str, db: Session = Depends(get_db)):
    pid = _parse_uuid(patient_id)
    if PatientService(db).get_patient(pid, include_deleted=True) is None:
        raise NotFoundError("patient not found")
    return ok([serialize_call(c) for c in CallService(db).list_calls(patient_id=pid)])


@router.get("/appointments", summary="Booked (mock) appointments")
def list_appointments(db: Session = Depends(get_db)):
    return ok([serialize_appointment(a) for a in AppointmentService(db).list_all()])


@router.get("/patients/{patient_id}/appointments", summary="Appointments for a patient")
def patient_appointments(patient_id: str, db: Session = Depends(get_db)):
    pid = _parse_uuid(patient_id)
    if PatientService(db).get_patient(pid, include_deleted=True) is None:
        raise NotFoundError("patient not found")
    return ok([serialize_appointment(a) for a in AppointmentService(db).list_for_patient(pid)])
