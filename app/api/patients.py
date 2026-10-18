"""REST API: ``/patients`` resource."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.envelope import BadRequestError, NotFoundError, ok
from app.api.security import require_api_key
from app.db.session import get_db
from app.schemas.patient import (
    PatientCreate,
    PatientOut,
    PatientUpdate,
    normalize_us_phone,
    parse_date_of_birth,
)
from app.services.patient_service import PatientNotFoundError, PatientService

router = APIRouter(prefix="/patients", tags=["patients"])


def _serialize(patient) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise BadRequestError("patient id must be a valid UUID") from None


@router.get("", summary="List patients (optionally filtered)")
def list_patients(
    last_name: str | None = Query(None, description="Case-insensitive exact match"),
    date_of_birth: str | None = Query(None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: str | None = Query(None, description="Any U.S. format, e.g. (555) 123-4567"),
    include_deleted: bool = Query(False, description="Include soft-deleted records"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        dob = parse_date_of_birth(date_of_birth) if date_of_birth else None
    except ValueError as exc:
        raise BadRequestError(f"query parameter date_of_birth {exc}") from None
    try:
        phone = normalize_us_phone(phone_number) if phone_number else None
    except ValueError as exc:
        raise BadRequestError(f"query parameter phone_number {exc}") from None

    patients, total = PatientService(db).list_patients(
        last_name=last_name,
        date_of_birth=dob,
        phone_number=phone,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    return ok(
        [_serialize(p) for p in patients],
        meta={"total": total, "limit": limit, "offset": offset},
    )


@router.get("/{patient_id}", summary="Get one patient by UUID")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = PatientService(db).get_patient(_parse_uuid(patient_id))
    if patient is None:
        raise NotFoundError("patient not found")
    return ok(_serialize(patient))


@router.post(
    "",
    status_code=201,
    summary="Create a patient",
    dependencies=[Depends(require_api_key)],
)
def create_patient(payload: PatientCreate = Body(...), db: Session = Depends(get_db)):
    patient = PatientService(db).create_patient(payload)
    return ok(_serialize(patient), status_code=201)


@router.put(
    "/{patient_id}",
    summary="Update a patient (partial updates allowed)",
    dependencies=[Depends(require_api_key)],
)
def update_patient(
    patient_id: str, payload: PatientUpdate = Body(...), db: Session = Depends(get_db)
):
    try:
        patient = PatientService(db).update_patient(_parse_uuid(patient_id), payload)
    except PatientNotFoundError:
        raise NotFoundError("patient not found") from None
    return ok(_serialize(patient))


@router.delete(
    "/{patient_id}",
    summary="Soft-delete a patient",
    dependencies=[Depends(require_api_key)],
)
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        patient = PatientService(db).soft_delete_patient(_parse_uuid(patient_id))
    except PatientNotFoundError:
        raise NotFoundError("patient not found") from None
    return ok(
        {
            "patient_id": str(patient.patient_id),
            "deleted_at": PatientOut.model_validate(patient).model_dump(mode="json")["deleted_at"],
        }
    )
