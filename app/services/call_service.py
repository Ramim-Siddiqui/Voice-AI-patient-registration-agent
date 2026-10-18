"""Call records: link each phone call to the patient it created/updated and
store the transcript + summary that the voice platform sends when the call ends.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Call, utcnow


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class CallService:
    def __init__(self, db: Session):
        self.db = db

    def touch(
        self,
        call_id: str,
        *,
        caller_number: str | None = None,
        patient_id: uuid.UUID | None = None,
        started_at: str | None = None,
    ) -> Call:
        """Create the call row on first contact; attach the patient once known."""
        call = self.db.get(Call, call_id)
        if call is None:
            call = Call(call_id=call_id, status="in-progress")
            self.db.add(call)
        if caller_number and not call.caller_number:
            call.caller_number = caller_number
        if patient_id is not None:
            call.patient_id = patient_id
        if started_at and call.started_at is None:
            call.started_at = _parse_ts(started_at)
        call.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(call)
        return call

    def finalize(
        self,
        call_id: str,
        *,
        caller_number: str | None,
        ended_reason: str | None,
        started_at: str | None,
        ended_at: str | None,
        summary: str | None,
        transcript: str | None,
        recording_url: str | None,
        cost_usd: float | None = None,
    ) -> Call:
        call = self.touch(call_id, caller_number=caller_number, started_at=started_at)
        call.status = "ended"
        call.ended_reason = (ended_reason or None) and str(ended_reason)[:120]
        ended = _parse_ts(ended_at)
        call.ended_at = ended
        # Vapi sends no duration field; derive it from the timestamps.  SQLite
        # returns naive datetimes (always UTC here), so re-attach the zone.
        duration_seconds: float | None = None
        started = call.started_at or _parse_ts(started_at)
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is not None and ended is not None:
            duration_seconds = round((ended - started).total_seconds(), 1)
        call.duration_seconds = duration_seconds
        call.summary = summary
        call.transcript = transcript
        call.recording_url = recording_url
        call.cost_usd = cost_usd
        call.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(call)
        return call

    def list_calls(self, *, patient_id: uuid.UUID | None = None, limit: int = 50) -> list[Call]:
        stmt = select(Call).order_by(Call.created_at.desc()).limit(limit)
        if patient_id is not None:
            stmt = stmt.where(Call.patient_id == patient_id)
        return list(self.db.scalars(stmt).all())

    def get_call(self, call_id: str) -> Call | None:
        return self.db.get(Call, call_id)
