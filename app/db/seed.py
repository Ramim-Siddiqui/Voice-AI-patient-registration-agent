"""Demonstration seed data (fictional people; no real patient data)."""

from __future__ import annotations

from datetime import date

DEMO_PATIENTS: list[dict] = [
    {
        "first_name": "Jane",
        "last_name": "Doe",
        "date_of_birth": date(1985, 3, 15),
        "sex": "Female",
        "phone_number": "5550101234",
        "email": "jane.doe@example.com",
        "address_line_1": "123 Maple Street",
        "address_line_2": "Apt 4B",
        "city": "Springfield",
        "state": "IL",
        "zip_code": "62704",
        "insurance_provider": "Blue Cross Blue Shield",
        "insurance_member_id": "BCB123456789",
        "preferred_language": "English",
        "emergency_contact_name": "John Doe",
        "emergency_contact_phone": "5550105678",
    },
    {
        "first_name": "Carlos",
        "last_name": "Rivera",
        "date_of_birth": date(1978, 11, 2),
        "sex": "Male",
        "phone_number": "5550109876",
        "email": None,
        "address_line_1": "987 Ocean Avenue",
        "address_line_2": None,
        "city": "San Diego",
        "state": "CA",
        "zip_code": "92101-1234",
        "insurance_provider": None,
        "insurance_member_id": None,
        "preferred_language": "Spanish",
        "emergency_contact_name": None,
        "emergency_contact_phone": None,
    },
]
