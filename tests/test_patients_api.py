"""Integration tests for the REST API (envelope, status codes, validation, soft delete)."""

from __future__ import annotations

import uuid


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "ok"
    assert r.json()["error"] is None


def test_create_patient_normalises_and_returns_201(client, valid_patient):
    r = client.post("/patients", json=valid_patient)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["error"] is None
    data = body["data"]
    uuid.UUID(data["patient_id"])  # valid UUID
    assert data["first_name"] == "Jane"
    assert data["last_name"] == "O'Connor-Smith"
    assert data["date_of_birth"] == "1985-03-15"
    assert data["sex"] == "Female"
    assert data["phone_number"] == "5551234567"
    assert data["email"] == "jane.oconnor@example.com"
    assert data["state"] == "IL"
    assert data["zip_code"] == "62704-1234"
    assert data["preferred_language"] == "English"
    assert data["address_line_2"] is None
    assert data["created_at"].endswith("Z") and data["updated_at"].endswith("Z")
    assert data["deleted_at"] is None


def test_get_patient_roundtrip(client, valid_patient):
    created = client.post("/patients", json=valid_patient).json()["data"]
    r = client.get(f"/patients/{created['patient_id']}")
    assert r.status_code == 200
    assert r.json()["data"] == created


def test_get_patient_not_found_and_bad_uuid(client):
    r = client.get(f"/patients/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json() == {"data": None, "error": {"code": "NOT_FOUND", "message": "patient not found"}}
    r = client.get("/patients/not-a-uuid")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BAD_REQUEST"


def test_list_with_filters(client, valid_patient):
    client.post("/patients", json=valid_patient)
    other = {**valid_patient, "last_name": "Rivera", "phone_number": "5550009999", "date_of_birth": "1978-11-02"}
    client.post("/patients", json=other)

    r = client.get("/patients")
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 2
    assert len(r.json()["data"]) == 2

    assert client.get("/patients", params={"last_name": "o'connor-smith"}).json()["meta"]["total"] == 1
    assert client.get("/patients", params={"date_of_birth": "03/15/1985"}).json()["meta"]["total"] == 1
    assert client.get("/patients", params={"date_of_birth": "1985-03-15"}).json()["meta"]["total"] == 1
    assert client.get("/patients", params={"phone_number": "(555) 000-9999"}).json()["meta"]["total"] == 1
    assert client.get("/patients", params={"last_name": "nobody"}).json()["data"] == []

    # pagination
    page = client.get("/patients", params={"limit": 1, "offset": 1}).json()
    assert len(page["data"]) == 1 and page["meta"] == {"total": 2, "limit": 1, "offset": 1}


def test_list_rejects_bad_query_params(client):
    assert client.get("/patients", params={"phone_number": "12"}).status_code == 400
    assert client.get("/patients", params={"date_of_birth": "13/45/1985"}).status_code == 400
    assert client.get("/patients", params={"limit": "abc"}).status_code == 400
    assert client.get("/patients", params={"limit": 0}).status_code == 400


def test_validation_errors_are_422_with_field_details(client, valid_patient):
    bad = {
        **valid_patient,
        "phone_number": "123",
        "date_of_birth": "01/01/2999",
        "state": "ZZ",
        "zip_code": "1234",
        "first_name": "J0hn",
        "sex": "robot",
    }
    r = client.post("/patients", json=bad)
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    fields = {d["field"]: d["message"] for d in error["details"]}
    assert {"phone_number", "date_of_birth", "state", "zip_code", "first_name", "sex"} <= set(fields)
    assert fields["phone_number"] == "must be a valid 10-digit U.S. phone number"
    assert fields["date_of_birth"] == "cannot be in the future"


def test_missing_required_fields_422(client):
    r = client.post("/patients", json={"email": "x@example.com"})
    assert r.status_code == 422
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert {"first_name", "last_name", "date_of_birth", "sex", "phone_number", "address_line_1", "city", "state", "zip_code"} <= fields


def test_unknown_field_rejected(client, valid_patient):
    r = client.post("/patients", json={**valid_patient, "ssn": "123-45-6789"})
    assert r.status_code == 422
    assert r.json()["error"]["details"] == [{"field": "ssn", "message": "is not a recognised field"}]


def test_malformed_json_is_400(client):
    r = client.post("/patients", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BAD_REQUEST"


def test_partial_update(client, valid_patient):
    created = client.post("/patients", json=valid_patient).json()["data"]
    pid = created["patient_id"]

    r = client.put(f"/patients/{pid}", json={"city": "chicago", "email": None, "phone_number": "555 987 6543"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["city"] == "chicago"
    assert data["email"] is None
    assert data["phone_number"] == "5559876543"
    assert data["first_name"] == "Jane"  # untouched
    assert data["updated_at"] >= created["updated_at"]

    assert client.put(f"/patients/{pid}", json={}).status_code == 422
    assert client.put(f"/patients/{pid}", json={"first_name": None}).status_code == 422
    assert client.put(f"/patients/{pid}", json={"ssn": "1"}).status_code == 422
    assert client.put(f"/patients/{pid}", json={"zip_code": "12"}).status_code == 422
    assert client.put(f"/patients/{uuid.uuid4()}", json={"city": "X"}).status_code == 404
    assert client.put("/patients/bad-id", json={"city": "X"}).status_code == 400


def test_soft_delete(client, valid_patient):
    pid = client.post("/patients", json=valid_patient).json()["data"]["patient_id"]

    r = client.delete(f"/patients/{pid}")
    assert r.status_code == 200
    assert r.json()["data"]["patient_id"] == pid
    assert r.json()["data"]["deleted_at"] is not None

    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["meta"]["total"] == 0
    listed = client.get("/patients", params={"include_deleted": "true"}).json()["data"]
    assert len(listed) == 1 and listed[0]["deleted_at"] is not None

    assert client.delete(f"/patients/{pid}").status_code == 404  # already deleted
    assert client.delete(f"/patients/{uuid.uuid4()}").status_code == 404
    assert client.put(f"/patients/{pid}", json={"city": "X"}).status_code == 404


def test_api_key_protects_writes_when_configured(make_client, valid_patient):
    client = make_client(API_KEY="super-secret")
    assert client.post("/patients", json=valid_patient).status_code == 401
    assert client.get("/patients").status_code == 200  # reads stay open
    r = client.post("/patients", json=valid_patient, headers={"X-API-Key": "super-secret"})
    assert r.status_code == 201
    pid = r.json()["data"]["patient_id"]
    r = client.put(f"/patients/{pid}", json={"city": "Y"}, headers={"Authorization": "Bearer super-secret"})
    assert r.status_code == 200


def test_seed_data(make_client):
    client = make_client(SEED_DEMO_DATA="true")
    body = client.get("/patients").json()
    assert body["meta"]["total"] == 2
    assert {p["last_name"] for p in body["data"]} == {"Doe", "Rivera"}


def test_unknown_route_uses_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["data"] is None
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
