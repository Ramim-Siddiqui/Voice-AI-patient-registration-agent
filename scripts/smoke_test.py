#!/usr/bin/env python
"""End-to-end smoke test against a running deployment (local or cloud).

    python scripts/smoke_test.py http://127.0.0.1:8000
    python scripts/smoke_test.py https://your-app.vercel.app

Creates a throw-away patient, reads it back through every endpoint, updates it,
soft-deletes it and simulates one Vapi tool call. Exits non-zero on failure.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Same .env as the app / setup script, so VAPI_WEBHOOK_SECRET and API_KEY are
# picked up automatically (shell environment variables still take precedence).
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API_KEY = os.getenv("API_KEY")
SECRET = os.getenv("VAPI_WEBHOOK_SECRET")


def main(base_url: str) -> int:
    base = base_url.rstrip("/")
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    client = httpx.Client(base_url=base, timeout=30, headers=headers)
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures += 1

    r = client.get("/health")
    check("GET /health", r.status_code == 200 and r.json()["data"]["status"] == "ok", r.text)

    phone = "555" + uuid.uuid4().hex[:7].translate(str.maketrans("abcdef", "123456"))
    payload = {
        "first_name": "Smoke",
        "last_name": "Test",
        "date_of_birth": "01/02/1990",
        "sex": "Other",
        "phone_number": phone,
        "address_line_1": "1 Test Way",
        "city": "Testville",
        "state": "Texas",
        "zip_code": "73301",
    }
    r = client.post("/patients", json=payload)
    check("POST /patients -> 201", r.status_code == 201, r.text)
    patient = r.json().get("data") or {}
    pid = patient.get("patient_id")
    check("state normalised to TX", patient.get("state") == "TX")

    r = client.get(f"/patients/{pid}")
    check("GET /patients/{id}", r.status_code == 200 and r.json()["data"]["patient_id"] == pid, r.text)

    r = client.get("/patients", params={"phone_number": phone})
    check("GET /patients?phone_number=", r.status_code == 200 and r.json()["meta"]["total"] == 1, r.text)

    r = client.post("/patients", json={**payload, "phone_number": "123"})
    check("POST invalid phone -> 422", r.status_code == 422, r.text)

    r = client.put(f"/patients/{pid}", json={"city": "Austin"})
    check("PUT partial update", r.status_code == 200 and r.json()["data"]["city"] == "Austin", r.text)

    tool_call = {
        "message": {
            "type": "tool-calls",
            "call": {"id": f"smoke-{uuid.uuid4().hex[:8]}", "customer": {"number": "+1" + phone}},
            "toolCallList": [{"id": "tc_smoke", "type": "function", "function": {"name": "lookup_patient", "arguments": {}}}],
        }
    }
    r = client.post("/vapi/webhook", json=tool_call, headers={"x-vapi-secret": SECRET} if SECRET else {})
    ok = r.status_code == 200 and json.loads(r.json()["results"][0]["result"]).get("status") == "found"
    check("POST /vapi/webhook lookup_patient (caller id)", ok, r.text)

    r = client.delete(f"/patients/{pid}")
    check("DELETE soft delete", r.status_code == 200 and r.json()["data"]["deleted_at"], r.text)
    r = client.get(f"/patients/{pid}")
    check("GET after delete -> 404", r.status_code == 404, r.text)

    r = client.get("/")
    check("GET / dashboard", r.status_code == 200 and "text/html" in r.headers.get("content-type", ""))

    print("\nALL PASSED" if failures == 0 else f"\n{failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
