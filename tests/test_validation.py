"""Unit tests for the normalisation / validation rules shared by API and voice agent."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.patient import (
    PatientCreate,
    PatientUpdate,
    blank_to_none,
    format_validation_errors,
    normalize_member_id,
    normalize_name,
    normalize_sex,
    normalize_state,
    normalize_us_phone,
    normalize_zip,
    parse_date_of_birth,
)


@pytest.mark.parametrize(
    "raw",
    ["(555) 123-4567", "+1 555 123 4567", "1-555-123-4567", "555.123.4567", "5551234567", " 555 123 4567 "],
)
def test_phone_accepts_common_us_formats(raw):
    assert normalize_us_phone(raw) == "5551234567"


@pytest.mark.parametrize("raw", ["123", "555-1234", "+44 20 7946 0958", "12345678901234", "", "abc", None])
def test_phone_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_us_phone(raw)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("03/15/1985", date(1985, 3, 15)),
        ("1985-03-15", date(1985, 3, 15)),
        ("3/5/1985", date(1985, 3, 5)),
        ("March 15, 1985", date(1985, 3, 15)),
        ("March 15th 1985", date(1985, 3, 15)),
        ("15 March 1985", date(1985, 3, 15)),
        (date(2000, 1, 1), date(2000, 1, 1)),
    ],
)
def test_dob_accepts_many_formats(raw, expected):
    assert parse_date_of_birth(raw) == expected


def test_dob_rejects_future_impossible_and_ancient():
    tomorrow = date.today() + timedelta(days=2)
    with pytest.raises(ValueError, match="future"):
        parse_date_of_birth(tomorrow.strftime("%m/%d/%Y"))
    for bad in ("02/30/1985", "13/01/1985", "yesterday", "", "01/01/1850"):
        with pytest.raises(ValueError):
            parse_date_of_birth(bad)


@pytest.mark.parametrize("raw, expected", [("62704", "62704"), ("62704-1234", "62704-1234"), ("627041234", "62704-1234"), ("62704 1234", "62704-1234")])
def test_zip_ok(raw, expected):
    assert normalize_zip(raw) == expected


@pytest.mark.parametrize("raw", ["1234", "ABCDE", "62704-12", "123456", ""])
def test_zip_bad(raw):
    with pytest.raises(ValueError):
        normalize_zip(raw)


@pytest.mark.parametrize("raw, expected", [("CA", "CA"), ("ca", "CA"), ("California", "CA"), ("new york", "NY"), ("Washington DC", "DC"), ("Puerto Rico", "PR")])
def test_state_ok(raw, expected):
    assert normalize_state(raw) == expected


@pytest.mark.parametrize("raw", ["ZZ", "Cali", "", "Californ"])
def test_state_bad(raw):
    with pytest.raises(ValueError):
        normalize_state(raw)


@pytest.mark.parametrize(
    "raw, expected",
    [("m", "Male"), ("FEMALE", "Female"), ("non-binary", "Other"), ("prefer not to say", "Decline to Answer"), ("Decline to Answer", "Decline to Answer")],
)
def test_sex_synonyms(raw, expected):
    assert normalize_sex(raw) == expected


def test_sex_bad():
    with pytest.raises(ValueError):
        normalize_sex("robot")


@pytest.mark.parametrize("raw, expected", [("jane", "Jane"), ("O'Brien", "O'Brien"), ("mary-jane", "Mary-Jane"), ("José", "José"), ("McDonald", "McDonald"), ("ANNA", "Anna")])
def test_name_ok(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw", ["J0hn", "", "-Dash", "a" * 51, "John!", None])
def test_name_bad(raw):
    with pytest.raises(ValueError):
        normalize_name(raw)


def test_blank_tokens_become_none():
    for raw in ("N/A", " ", "none", "null", "", "not provided"):
        assert blank_to_none(raw) is None
    assert blank_to_none("Aetna") == "Aetna"


def test_member_id_normalised():
    assert normalize_member_id("abc 123-x") == "ABC123-X"
    with pytest.raises(ValueError):
        normalize_member_id("bad id!")


def test_create_schema_defaults_and_normalisation():
    patient = PatientCreate(
        first_name="jane", last_name="doe", date_of_birth="03/15/1985", sex="f",
        phone_number="555-123-4567", address_line_1="1 Main St", city="Springfield",
        state="illinois", zip_code="62704", preferred_language="", email="N/A",
    )
    assert patient.preferred_language == "English"
    assert patient.email is None
    assert patient.state == "IL"
    assert patient.sex == "Female"


def test_create_schema_errors_are_per_field():
    with pytest.raises(ValidationError) as exc:
        PatientCreate(first_name="J0hn", last_name="Doe", date_of_birth="01/01/2999", sex="x",
                      phone_number="123", address_line_1="1 Main St", city="X", state="ZZ",
                      zip_code="1", email="not-an-email")
    fields = {d["field"]: d["message"] for d in format_validation_errors(exc.value)}
    assert fields["phone_number"] == "must be a valid 10-digit U.S. phone number"
    assert fields["date_of_birth"] == "cannot be in the future"
    assert "first_name" in fields and "state" in fields and "zip_code" in fields
    assert fields["email"] == "must be a valid email address"


def test_update_schema_rules():
    with pytest.raises(ValidationError):
        PatientUpdate()
    with pytest.raises(ValidationError):
        PatientUpdate(first_name=None)
    update = PatientUpdate(city="Chicago", preferred_language=None, email=None)
    assert update.changes() == {"city": "Chicago", "preferred_language": "English", "email": None}
