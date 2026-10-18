"""Mock first-appointment scheduling (bonus feature).

Slots are generated, not pulled from a real scheduling system: the next three
business days, each with a morning and an afternoon opening, in the clinic's
local time zone.  Bookings are persisted so they show on the dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Appointment

CLINIC_TZ = ZoneInfo("America/New_York")
PROVIDERS = ("Dr. Patel", "Dr. Nguyen", "Dr. Okafor")
_SLOT_TIMES = (time(9, 30), time(14, 0))


def spoken_time(value: datetime) -> str:
    """Clinic-local spoken form of a stored (UTC) appointment time.

    SQLite hands back naive datetimes; everything we store is UTC, so a naive
    value is re-labelled as UTC before converting to the clinic's zone.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(CLINIC_TZ)
    return local.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")


def _next_business_days(start: datetime, count: int) -> list[datetime]:
    days: list[datetime] = []
    cursor = start
    while len(days) < count:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # Monday..Friday
            days.append(cursor)
    return days


class AppointmentService:
    def __init__(self, db: Session):
        self.db = db

    def available_slots(self, *, now: datetime | None = None) -> list[dict]:
        now_local = (now or datetime.now(timezone.utc)).astimezone(CLINIC_TZ)
        slots: list[dict] = []
        for i, day in enumerate(_next_business_days(now_local, 3)):
            for j, slot_time in enumerate(_SLOT_TIMES):
                when = datetime.combine(day.date(), slot_time, tzinfo=CLINIC_TZ)
                slots.append(
                    {
                        "slot_id": when.strftime("%Y-%m-%dT%H:%M"),
                        "when": when.isoformat(),
                        "spoken": when.strftime("%A, %B %d at %I:%M %p").replace(" 0", " "),
                        "provider_name": PROVIDERS[(i + j) % len(PROVIDERS)],
                    }
                )
        return slots

    def book(
        self, *, patient_id: uuid.UUID, slot_id: str, reason: str | None = None
    ) -> Appointment:
        slot = next((s for s in self.available_slots() if s["slot_id"] == slot_id), None)
        if slot is None:
            raise ValueError("that slot is not available; ask for the current list of slots")
        appointment = Appointment(
            patient_id=patient_id,
            # Store UTC like every other timestamp (SQLite would otherwise keep the
            # New York wall-clock time and the API would re-label it as UTC).
            scheduled_for=datetime.fromisoformat(slot["when"]).astimezone(timezone.utc),
            provider_name=slot["provider_name"],
            reason=(reason or None) and str(reason)[:200],
        )
        self.db.add(appointment)
        self.db.commit()
        self.db.refresh(appointment)
        return appointment

    def list_for_patient(self, patient_id: uuid.UUID) -> list[Appointment]:
        stmt = (
            select(Appointment)
            .where(Appointment.patient_id == patient_id)
            .order_by(Appointment.scheduled_for)
        )
        return list(self.db.scalars(stmt).all())

    def list_all(self, limit: int = 100) -> list[Appointment]:
        stmt = select(Appointment).order_by(Appointment.scheduled_for).limit(limit)
        return list(self.db.scalars(stmt).all())
