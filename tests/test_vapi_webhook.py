"""Tests for the Vapi webhook: tool calls, duplicate detection, validation
feedback, database-failure handling and end-of-call reports."""

from __future__ import annotations

import json

import pytest

from tests.conftest import VALID_PATIENT

CALL = {
    "id": "call_123",
    "startedAt": "2026-10-17T15:00:00.000Z",
    "customer": {"number": "+15551234567"},
}


def tool_call_message(name: str, arguments: dict, *, call: dict | None = None, tool_call_id: str = "tc_1") -> dict:
    return {
        "message": {
            "type": "tool-calls",
            "call": call or CALL,
            "toolCallList": [
                {"id": tool_call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
            ],
        }
    }


def tool_result(response) -> dict:
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["toolCallId"] == "tc_1"
    return json.loads(results[0]["result"])


def test_lookup_uses_caller_id_when_no_phone_given(client):
    result = tool_result(client.post("/vapi/webhook", json=tool_call_message("lookup_patient", {})))
    assert result["status"] == "not_found"
    assert result["phone_number"] == "5551234567"
    assert result["phone_number_source"] == "caller_id"


def test_lookup_without_caller_id(client):
    msg = tool_call_message("lookup_patient", {}, call={"id": "call_x", "customer": {}})
    assert tool_result(client.post("/vapi/webhook", json=msg))["status"] == "no_caller_id"


def test_register_validation_error_lists_fields(client):
    args = {**VALID_PATIENT, "phone_number": "123", "date_of_birth": "01/01/2999"}
    result = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", args)))
    assert result["status"] == "validation_error"
    assert {e["field"] for e in result["errors"]} == {"phone_number", "date_of_birth"}
    assert client.get("/patients").json()["meta"]["total"] == 0


def test_register_then_duplicate_then_update_flow(client):
    # 1. register
    result = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))
    assert result["status"] == "created"
    patient_id = result["patient_id"]
    stored = client.get(f"/patients/{patient_id}").json()["data"]
    assert stored["first_name"] == "Jane" and stored["phone_number"] == "5551234567"

    # the call is linked to the patient
    calls = client.get(f"/patients/{patient_id}/calls").json()["data"]
    assert [c["call_id"] for c in calls] == ["call_123"]
    assert calls[0]["caller_number"] == "+15551234567"

    # 2. lookup finds the record (duplicate detection bonus)
    found = tool_result(client.post("/vapi/webhook", json=tool_call_message("lookup_patient", {"phone_number": "(555) 123-4567"})))
    assert found["status"] == "found"
    assert found["patient_id"] == patient_id
    assert "already have a record for Jane O'Connor-Smith" in found["message"]

    # 3. registering again with the same phone is refused unless explicitly allowed
    dup = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))
    assert dup["status"] == "duplicate" and dup["patient_id"] == patient_id
    forced = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", {**VALID_PATIENT, "first_name": "John", "allow_duplicate_phone": True})))
    assert forced["status"] == "created" and forced["patient_id"] != patient_id

    # 4. update only the changed fields (after the caller's date of birth is verified)
    verified = {"patient_id": patient_id, "verified_date_of_birth": "03/15/1985"}
    upd = tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {**verified, "city": "Chicago", "zip_code": "60601"})))
    assert upd["status"] == "updated" and upd["updated_fields"] == ["city", "zip_code"]
    stored = client.get(f"/patients/{patient_id}").json()["data"]
    assert stored["city"] == "Chicago" and stored["zip_code"] == "60601" and stored["first_name"] == "Jane"

    # update validation + bad ids
    bad = tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {**verified, "zip_code": "1"})))
    assert bad["status"] == "validation_error"
    assert tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {"patient_id": "nope", "verified_date_of_birth": "03/15/1985", "city": "X"})))["status"] == "error"
    assert tool_result(client.post("/vapi/webhook", json=tool_call_message("update_patient", {"patient_id": "00000000-0000-0000-0000-000000000000", "verified_date_of_birth": "03/15/1985", "city": "X"})))["status"] == "not_found"


def test_arguments_may_arrive_as_json_string(client):
    msg = tool_call_message("lookup_patient", {})
    msg["message"]["toolCallList"][0]["function"]["arguments"] = json.dumps({"phone_number": "5559998888"})
    result = tool_result(client.post("/vapi/webhook", json=msg))
    assert result["status"] == "not_found" and result["phone_number"] == "5559998888"


def test_unknown_tool_is_reported_not_crashed(client):
    result = tool_result(client.post("/vapi/webhook", json=tool_call_message("launch_rocket", {})))
    assert result["status"] == "error"


def test_database_failure_is_relayed_honestly(client, monkeypatch):
    from app.services import patient_service

    def boom(self, data):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(patient_service.PatientService, "create_patient", boom)
    result = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))
    assert result["status"] == "error"
    assert "could not save" in result["message"]


def test_appointment_flow(client):
    pid = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))["patient_id"]
    slots = tool_result(client.post("/vapi/webhook", json=tool_call_message("get_appointment_slots", {})))
    assert slots["status"] == "ok" and len(slots["slots"]) == 6
    chosen = slots["slots"][0]
    booked = tool_result(client.post("/vapi/webhook", json=tool_call_message("book_appointment", {"patient_id": pid, "slot_id": chosen["slot_id"]})))
    assert booked["status"] == "booked" and booked["provider_name"] == chosen["provider_name"]
    appts = client.get(f"/patients/{pid}/appointments").json()["data"]
    assert len(appts) == 1 and appts[0]["status"] == "scheduled"
    assert tool_result(client.post("/vapi/webhook", json=tool_call_message("book_appointment", {"patient_id": pid, "slot_id": "bogus"})))["status"] == "error"


def test_end_of_call_report_is_stored(client):
    pid = tool_result(client.post("/vapi/webhook", json=tool_call_message("register_patient", VALID_PATIENT)))["patient_id"]
    report = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "assistant-ended-call",
            "call": CALL,
            "startedAt": "2026-10-17T15:00:00.000Z",
            "endedAt": "2026-10-17T15:04:30.000Z",
            "durationSeconds": 270.0,
            "analysis": {"summary": "Caller registered as a new patient."},
            "artifact": {"transcript": "AI: Hi...\nUser: Hi, my name is Jane...", "recordingUrl": "https://example.com/rec.wav"},
        }
    }
    r = client.post("/vapi/webhook", json=report)
    assert r.status_code == 200 and r.json() == {}
    call = client.get("/calls/call_123").json()["data"]
    assert call["patient_id"] == pid
    assert call["status"] == "ended"
    assert call["summary"] == "Caller registered as a new patient."
    assert call["transcript"].startswith("AI: Hi")
    assert call["recording_url"] == "https://example.com/rec.wav"
    assert call["duration_seconds"] == 270.0
    assert call["ended_at"] == "2026-10-17T15:04:30.000Z"
    assert client.get("/calls").json()["data"][0]["call_id"] == "call_123"


def test_other_message_types_are_acknowledged(client):
    r = client.post("/vapi/webhook", json={"message": {"type": "status-update", "status": "in-progress", "call": CALL}})
    assert r.status_code == 200 and r.json() == {}


def test_bad_payloads(client):
    assert client.post("/vapi/webhook", json={"nope": 1}).status_code == 400
    assert client.post("/vapi/webhook", content="x", headers={"Content-Type": "application/json"}).status_code == 400


def test_secret_enforced_when_configured(make_client):
    client = make_client(VAPI_WEBHOOK_SECRET="s3cret")
    msg = tool_call_message("lookup_patient", {})
    assert client.post("/vapi/webhook", json=msg).status_code == 401
    assert client.post("/vapi/webhook", json=msg, headers={"x-vapi-secret": "wrong"}).status_code == 401
    assert client.post("/vapi/webhook", json=msg, headers={"x-vapi-secret": "s3cret"}).status_code == 200


def test_assistant_config_endpoint(client):
    data = client.get("/vapi/assistant-config").json()["data"]
    assert "register_patient" in data["system_prompt"]
    names = [t["function"]["name"] for t in data["tools"] if t["type"] == "function"]
    assert names == ["lookup_patient", "register_patient", "update_patient", "get_appointment_slots", "book_appointment"]
    assert data["tools"][-1] == {"type": "endCall"}
