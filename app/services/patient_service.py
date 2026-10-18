"""Patient business logic / data access.

Both the REST API and the voice agent's tool handlers go through this service,
so there is exactly one code path that writes patient records.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Patient, utcnow
from app.schemas.patient import PatientCreate, PatientUpdate


class PatientNotFoundError(Exception):
    def __init__(self, patient_id: uuid.UUID | str):
        super().__init__(f"patient {patient_id} not found")
        self.patient_id = patient_id


class PatientService:
    def __init__(self, db: Session):
        self.db = db

    # --- queries -----------------------------------------------------------

    def list_patients(
        self,
        *,
        last_name: str | None = None,
        date_of_birth: date | None = None,
        phone_number: str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Patient], int]:
        stmt = select(Patient)
        if not include_deleted:
            stmt = stmt.where(Patient.deleted_at.is_(None))
        if last_name:
            stmt = stmt.where(func.lower(Patient.last_name) == last_name.strip().lower())
        if date_of_birth:
            stmt = stmt.where(Patient.date_of_birth == date_of_birth)
        if phone_number:
            stmt = stmt.where(Patient.phone_number == phone_number)

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.db.scalars(
            stmt.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
        ).all()
        return list(rows), int(total)

    def get_patient(self, patient_id: uuid.UUID, *, include_deleted: bool = False) -> Patient | None:
        patient = self.db.get(Patient, patient_id)
        if patient is None:
            return None
        if patient.deleted_at is not None and not include_deleted:
            return None
        return patient

    def find_by_phone(self, phone_number: str) -> list[Patient]:
        """Active patients registered with this (normalised, 10-digit) phone number."""
        stmt = (
            select(Patient)
            .where(Patient.phone_number == phone_number, Patient.deleted_at.is_(None))
            .order_by(Patient.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    # --- commands ----------------------------------------------------------

    def create_patient(self, data: PatientCreate) -> Patient:
        patient = Patient(**data.model_dump())
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)
        return patient

    def update_patient(self, patient_id: uuid.UUID, data: PatientUpdate) -> Patient:
        patient = self.get_patient(patient_id)
        if patient is None:
            raise PatientNotFoundError(patient_id)
        for field, value in data.changes().items():
            setattr(patient, field, value)
        patient.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(patient)
        return patient

    def soft_delete_patient(self, patient_id: uuid.UUID) -> Patient:
        patient = self.get_patient(patient_id)
        if patient is None:
            raise PatientNotFoundError(patient_id)
        now = utcnow()
        patient.deleted_at = now
        patient.updated_at = now
        self.db.commit()
        self.db.refresh(patient)
        return patient
