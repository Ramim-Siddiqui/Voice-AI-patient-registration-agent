"""Regression tests for defects found in code review."""

from __future__ import annotations

import json

from tests.conftest import VALID_PATIENT
from tests.test_vapi_webhook import CALL, tool_call_message, tool_result


def test_blank_tokens_clear_every_optional_field(client, valid_patient):
    payload = {
        **valid_patient,
        "email": "",
        "address_line_2": "No apartment",
        "insurance_provider": "n/a",
        "insurance_member_id": " ",
        "emergency_contact_name": "none",
        "emergency_contact_phone": "I don't have one",
        "preferred_language": None,
    }
    r = client.post("/patients", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    for field in ("email", "address_line_2", "insurance_provider", "insurance_member_id",
                  "emergency_contact_name", "emergency_contact_phone"):
        assert data[field] is None, field
    assert data["preferred_language"] == "English"


def test_update_with_blank_clears_optional_field(client, valid_patient):
    pid = client.post("/patients", json={**valid_patient, "address_line_2": "Apt 4B"}).json()["data"]["patient_id"]
    r = client.put(f"/patients/{pid}", json={"address_line_2": "nope", "preferred_language": ""})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["address_line_2"] is None
    assert r.json()["data"]["preferred_language"] == "English"


def test_end_of_call_report_without_duration_field(client):
    """Real Vapi reports carry no duration; it must be derived, also on SQLite (naive datetimes)."""
    tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))
    report = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "call": CALL,
            "startedAt": "2026-10-17T15:00:00.000Z",
            "endedAt": "2026-10-17T15:04:30.500Z",
            "cost": 0.31,
            "analysis": {"summary": "Registered."},
            "artifact": {"transcript": "Riley: Hi\nCaller: Hello", "recording": {"stereoUrl": "https://example.com/stereo.mp3"}},
        }
    }
    r = client.post("/vapi/webhook", json=report)
    assert r.status_code == 200, r.text
    call = client.get("/calls/call_123").json()["data"]
    assert call["duration_seconds"] == 270.5
    assert call["cost_usd"] == 0.31
    assert call["recording_url"] == "https://example.com/stereo.mp3"
    assert call["status"] == "ended"


def test_non_ascii_credentials_are_401_not_500(make_client, valid_patient):
    client = make_client(API_KEY="secret", VAPI_WEBHOOK_SECRET="s3cret")
    odd = "sécrét".encode("latin-1")  # raw non-ASCII bytes on the wire, as a hostile client could send
    r = client.post("/patients", json=valid_patient, headers={"X-API-Key": odd})
    assert r.status_code == 401
    r = client.post("/vapi/webhook", json=tool_call_message("lookup_patient", {}), headers={"x-vapi-secret": odd})
    assert r.status_code == 401


def test_update_requires_identity_verification(client):
    created = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))
    pid = created["patient_id"]

    found = tool_result(client.post("/vapi/webhook", json=tool_call_message("lookup_patient", {"phone_number": "5551234567"})))
    assert found["status"] == "found"
    assert "address_line_1" not in json.dumps(found)  # stored details are never disclosed by lookup
    assert found["on_file"] == {"email": True, "insurance": False, "emergency_contact": False, "preferred_language": "English"}

    missing = tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {"patient_id": pid, "city": "Chicago"})))
    assert missing["status"] == "error" and "verified_date_of_birth" in missing["message"]

    wrong = tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {"patient_id": pid, "verified_date_of_birth": "01/01/1990", "city": "Chicago"})))
    assert wrong["status"] == "identity_mismatch"
    assert client.get(f"/patients/{pid}").json()["data"]["city"] == "Springfield"

    bad_dob = tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {"patient_id": pid, "verified_date_of_birth": "not a date", "city": "Chicago"})))
    assert bad_dob["status"] == "validation_error"

    ok = tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {"patient_id": pid, "verified_date_of_birth": "March 15, 1985", "city": "Chicago"})))
    assert ok["status"] == "updated" and ok["updated_fields"] == ["city"]
    assert client.get(f"/patients/{pid}").json()["data"]["city"] == "Chicago"


def test_healthz_has_no_database_dependency(client, monkeypatch):
    from app.db import session as db_session

    def boom():
        raise AssertionError("/healthz must not open a database session")

    monkeypatch.setattr(db_session, "get_session_factory", boom)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["data"]["status"] == "ok"


def test_appointment_time_is_spoken_in_clinic_time(client):
    pid = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))["patient_id"]
    slots = tool_result(client.post("/vapi/webhook", json=tool_call_message("get_appointment_slots", {})))["slots"]
    chosen = slots[0]
    booked = tool_result(client.post("/vapi/webhook", json=tool_call_message("book_appointment", {"patient_id": pid, "slot_id": chosen["slot_id"]})))
    assert booked["status"] == "booked"
    assert booked["scheduled_for_spoken"] == chosen["spoken"]  # 9:30 AM slot is confirmed as 9:30 AM, not UTC
    stored = client.get(f"/patients/{pid}/appointments").json()["data"][0]["scheduled_for"]
    assert stored.endswith("Z") and stored[:16] != chosen["when"][:16]  # persisted as UTC, not wall-clock


def test_lookup_failure_message_does_not_claim_a_failed_save(client, monkeypatch):
    from app.services import patient_service

    def boom(self, phone):
        raise RuntimeError("db down")

    monkeypatch.setattr(patient_service.PatientService, "find_by_phone", boom)
    result = tool_result(client.post("/vapi/webhook", json=tool_call_message("lookup_patient", {"phone_number": "5551234567"})))
    assert result["status"] == "error"
    assert "could not save" not in result["message"] and "Continue" in result["message"]


def test_assistant_config_uses_vercel_url_fallback(make_client, monkeypatch):
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "demo.vercel.app")
    client = make_client(PUBLIC_BASE_URL="")
    tools = client.get("/vapi/assistant-config").json()["data"]["tools"]
    assert tools[0]["server"]["url"] == "https://demo.vercel.app/vapi/webhook"
    failed = [m for m in tools[0]["messages"] if m["type"] == "request-failed"][0]
    assert failed["role"] == "system"
