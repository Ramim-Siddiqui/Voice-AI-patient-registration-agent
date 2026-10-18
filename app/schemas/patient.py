"""Pydantic schemas and normalisation rules for the patient demographic data model.

These schemas are the *single source of truth* for validation.  They are used by

* the REST API (request bodies and query parameters), and
* the voice agent's tool handlers (so the agent can never bypass validation).

Normalisation is deliberately forgiving on *input* - a voice agent and a human
both say "California", "+1 (555) 123-4567" or "March 15th, 1985" - and strict on
*output*: state ``CA``, phone ``5551234567``, date ``1985-03-15``.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    # Federal district and territories (valid USPS abbreviations)
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
_STATE_BY_NAME: dict[str, str] = {name.casefold(): code for code, name in US_STATES.items()}
_STATE_BY_NAME["washington dc"] = "DC"
_STATE_BY_NAME["washington d.c."] = "DC"
_STATE_BY_NAME["virgin islands"] = "VI"

SEX_VALUES: tuple[str, ...] = ("Male", "Female", "Other", "Decline to Answer")
_SEX_SYNONYMS: dict[str, str] = {
    "m": "Male", "male": "Male", "man": "Male", "boy": "Male",
    "f": "Female", "female": "Female", "woman": "Female", "girl": "Female",
    "o": "Other", "other": "Other", "non binary": "Other", "nonbinary": "Other",
    "non-binary": "Other", "intersex": "Other", "x": "Other",
    "decline": "Decline to Answer", "declined": "Decline to Answer",
    "decline to answer": "Decline to Answer", "declines to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
    "rather not say": "Decline to Answer", "no answer": "Decline to Answer",
    "not specified": "Decline to Answer", "unspecified": "Decline to Answer",
}

# Values a voice agent might send for "nothing" on an optional field.
_EMPTY_TOKENS = {
    "", "n/a", "na", "none", "null", "nil", "no", "not provided", "not available",
    "not applicable", "skip", "skipped", "declined", "-", "unknown",
}

_NAME_EXTRA_CHARS = set("'’- ")

# ---------------------------------------------------------------------------
# Normalisers (plain functions so they can be reused for query params too)
# ---------------------------------------------------------------------------


# Spoken ways of saying "there isn't one" that a voice agent may pass through verbatim.
_EMPTY_PATTERNS = (
    re.compile(r"^no\s+(apartment|apt|unit|suite|second line|address line 2|email|e-?mail|insurance|insurer|member id|emergency contact|contact|middle name)(\s+(number|address|provider|id|name))?$"),
    re.compile(r"^(i\s+)?(don'?t|do not|doesn'?t|does not)\s+have\s+(one|any|it|an?\s+[\w\s]+)$"),
    re.compile(r"^(none needed|not needed|no thanks|no thank you|nothing|nope|nah)$"),
)


def blank_to_none(value: Any) -> Any:
    """Map blanks / "n/a" / "no apartment" style tokens to ``None`` (for optional fields)."""
    if value is None:
        return None
    if isinstance(value, str):
        key = re.sub(r"\s+", " ", value.strip().casefold()).strip(" .!")
        if key in _EMPTY_TOKENS or any(p.match(key) for p in _EMPTY_PATTERNS):
            return None
    return value


def normalize_name(value: Any, *, max_len: int = 50) -> str:
    """Letters, hyphens, apostrophes (and spaces for multi-part names)."""
    if value is None:
        raise ValueError("is required")
    text = re.sub(r"\s+", " ", str(value)).strip().replace("’", "'")
    if not text:
        raise ValueError("is required")
    if len(text) > max_len:
        raise ValueError(f"must be at most {max_len} characters")
    if not text[0].isalpha():
        raise ValueError("must start with a letter")
    for ch in text:
        if not (ch.isalpha() or ch in _NAME_EXTRA_CHARS):
            raise ValueError("may only contain letters, hyphens and apostrophes")
    # "john" / "JOHN" -> "John" (mixed case such as "McDonald" is left untouched)
    if text.islower() or text.isupper():
        text = text.title()
    return text


def normalize_us_phone(value: Any) -> str:
    """Return exactly 10 digits for a U.S. phone number given in any common format."""
    if value is None:
        raise ValueError("is required")
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("must be a valid 10-digit U.S. phone number")
    return digits


_DOB_FORMATS = (
    "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d", "%m.%d.%Y",
    "%B %d, %Y", "%B %d %Y", "%d %B %Y", "%b %d, %Y", "%b %d %Y",
)


def parse_date_of_birth(value: Any) -> date:
    """Accept MM/DD/YYYY (canonical), ISO dates and spoken forms; reject impossible dates."""
    if value is None:
        raise ValueError("is required")
    if isinstance(value, datetime):
        parsed: date | None = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = re.sub(r"\s+", " ", str(value)).strip()
        text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)  # "March 15th, 1985"
        parsed = None
        for fmt in _DOB_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError("must be a valid date in MM/DD/YYYY format")
    today = datetime.now(timezone.utc).date()
    if parsed > today:
        raise ValueError("cannot be in the future")
    if parsed.year < 1900:
        raise ValueError("must be on or after 01/01/1900")
    return parsed


def normalize_sex(value: Any) -> str:
    if value is None:
        raise ValueError("is required")
    key = re.sub(r"[\s_]+", " ", str(value).strip().casefold())
    if key in _SEX_SYNONYMS:
        return _SEX_SYNONYMS[key]
    raise ValueError("must be one of: Male, Female, Other, Decline to Answer")


def normalize_state(value: Any) -> str:
    if value is None:
        raise ValueError("is required")
    text = re.sub(r"\s+", " ", str(value)).strip().strip(".")
    if not text:
        raise ValueError("is required")
    code = text.upper()
    if len(code) == 2 and code in US_STATES:
        return code
    name = text.casefold()
    if name in _STATE_BY_NAME:
        return _STATE_BY_NAME[name]
    raise ValueError("must be a valid 2-letter U.S. state abbreviation (e.g. CA)")


def normalize_zip(value: Any) -> str:
    if value is None:
        raise ValueError("is required")
    text = str(value).strip().replace(" ", "")
    if re.fullmatch(r"\d{5}", text):
        return text
    match = re.fullmatch(r"(\d{5})-?(\d{4})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    raise ValueError("must be a 5-digit ZIP code or ZIP+4 (e.g. 12345 or 12345-6789)")


def normalize_free_text(value: Any, *, max_len: int) -> str:
    if value is None:
        raise ValueError("is required")
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        raise ValueError("is required")
    if len(text) > max_len:
        raise ValueError(f"must be at most {max_len} characters")
    return text


def normalize_member_id(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value)).upper()
    if not re.fullmatch(r"[A-Z0-9\-]{1,50}", text):
        raise ValueError("must be alphanumeric (letters, digits, hyphens), up to 50 characters")
    return text


def normalize_language(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return "English"
    if len(text) > 50 or not all(ch.isalpha() or ch in " -" for ch in text):
        raise ValueError("must be a language name (letters only), up to 50 characters")
    return text.title() if text.islower() or text.isupper() else text


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_OPTIONAL_FIELDS = frozenset({
    "email", "address_line_2", "insurance_provider", "insurance_member_id",
    "emergency_contact_name", "emergency_contact_phone", "preferred_language",
})


class _PatientValidators(BaseModel):
    """Field normalisers shared by the create and update schemas."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Optional fields: blanks / "n/a" / "no apartment" become None before any
    # per-field validator runs (a model-level validator guarantees the order).
    @model_validator(mode="before")
    @classmethod
    def _blank_optionals(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: (blank_to_none(v) if k in _OPTIONAL_FIELDS else v) for k, v in data.items()}
        return data

    @field_validator("first_name", "last_name", mode="before", check_fields=False)
    @classmethod
    def _names(cls, value: Any) -> Any:
        return normalize_name(value, max_len=50) if value is not None else None

    @field_validator("date_of_birth", mode="before", check_fields=False)
    @classmethod
    def _dob(cls, value: Any) -> Any:
        return parse_date_of_birth(value) if value is not None else None

    @field_validator("sex", mode="before", check_fields=False)
    @classmethod
    def _sex(cls, value: Any) -> Any:
        return normalize_sex(value) if value is not None else None

    @field_validator("phone_number", "emergency_contact_phone", mode="before", check_fields=False)
    @classmethod
    def _phones(cls, value: Any) -> Any:
        return normalize_us_phone(value) if value is not None else None

    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _email(cls, value: Any) -> Any:
        return str(value).strip().lower() if value is not None else None

    @field_validator("address_line_1", mode="before", check_fields=False)
    @classmethod
    def _addr1(cls, value: Any) -> Any:
        return normalize_free_text(value, max_len=200) if value is not None else None

    @field_validator("address_line_2", mode="before", check_fields=False)
    @classmethod
    def _addr2(cls, value: Any) -> Any:
        return normalize_free_text(value, max_len=200) if value is not None else None

    @field_validator("city", mode="before", check_fields=False)
    @classmethod
    def _city(cls, value: Any) -> Any:
        return normalize_free_text(value, max_len=100) if value is not None else None

    @field_validator("state", mode="before", check_fields=False)
    @classmethod
    def _state(cls, value: Any) -> Any:
        return normalize_state(value) if value is not None else None

    @field_validator("zip_code", mode="before", check_fields=False)
    @classmethod
    def _zip(cls, value: Any) -> Any:
        return normalize_zip(value) if value is not None else None

    @field_validator("insurance_provider", mode="before", check_fields=False)
    @classmethod
    def _insurer(cls, value: Any) -> Any:
        return normalize_free_text(value, max_len=100) if value is not None else None

    @field_validator("insurance_member_id", mode="before", check_fields=False)
    @classmethod
    def _member_id(cls, value: Any) -> Any:
        return normalize_member_id(value) if value is not None else None

    @field_validator("preferred_language", mode="before", check_fields=False)
    @classmethod
    def _language(cls, value: Any) -> Any:
        return normalize_language(value) if value is not None else None

    @field_validator("emergency_contact_name", mode="before", check_fields=False)
    @classmethod
    def _ec_name(cls, value: Any) -> Any:
        return normalize_name(value, max_len=100) if value is not None else None


class PatientCreate(_PatientValidators):
    """Body of ``POST /patients`` (and the voice agent's ``register_patient`` tool)."""

    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: EmailStr | None = None
    address_line_1: str
    address_line_2: str | None = None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = "English"  # null / blank -> "English"
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    @field_validator("preferred_language", mode="after")
    @classmethod
    def _default_language(cls, value: str | None) -> str:
        return value or "English"


class PatientUpdate(_PatientValidators):
    """Body of ``PUT /patients/{id}``: partial update, every field optional.

    Sending ``null`` for an optional field clears it; ``preferred_language: null``
    resets it to "English".
    """

    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    phone_number: str | None = None
    email: EmailStr | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    _REQUIRED = ("first_name", "last_name", "date_of_birth", "sex", "phone_number",
                 "address_line_1", "city", "state", "zip_code")

    @model_validator(mode="after")
    def _check(self) -> PatientUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for name in self._REQUIRED:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} is required and cannot be cleared")
        if "preferred_language" in self.model_fields_set and self.preferred_language is None:
            self.preferred_language = "English"
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent."""
        return self.model_dump(exclude_unset=True)


class PatientOut(BaseModel):
    """Canonical representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @field_serializer("created_at", "updated_at", "deleted_at")
    def _utc_iso(self, value: datetime | None) -> str | None:
        return to_utc_iso(value)


def to_utc_iso(value: datetime | None) -> str | None:
    """Serialise timestamps as UTC ISO-8601 with a trailing ``Z``.

    SQLite returns naive datetimes; we always store UTC so naive == UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Error formatting (shared by the API and the voice tools)
# ---------------------------------------------------------------------------


def format_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    """Turn a pydantic error into ``[{"field": ..., "message": ...}, ...]``."""
    details: list[dict[str, str]] = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p != "body"]
        field = ".".join(loc) or "body"
        message = err.get("msg", "is invalid")
        err_type = err.get("type", "")
        if message.startswith("Value error, "):
            message = message[len("Value error, "):]
        if err_type == "missing":
            message = "is required"
        elif err_type == "extra_forbidden":
            message = "is not a recognised field"
        elif err_type in ("string_type", "date_type") and err.get("input") is None:
            message = "is required"
        elif err_type.startswith("value_error") and "email" in message.lower():
            message = "must be a valid email address"
        details.append({"field": field, "message": message})
    return details
