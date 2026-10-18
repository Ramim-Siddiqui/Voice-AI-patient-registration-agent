"""Tool (function-calling) definitions exposed to the voice agent.

The same definitions are used by

* ``scripts/vapi_setup.py`` - to provision the assistant on Vapi, and
* ``app.voice.handlers`` - to know which argument names belong to a patient.

Keep the descriptions short and concrete: they are what the LLM reads when it
decides how to fill the arguments.
"""

from __future__ import annotations

from typing import Any

# JSON-schema properties for the patient demographic fields.  Descriptions tell
# the model the exact canonical format so the backend rarely has to reject.
PATIENT_PROPERTIES: dict[str, dict[str, Any]] = {
    "first_name": {"type": "string", "description": "Legal first name, spelled as confirmed with the caller."},
    "last_name": {"type": "string", "description": "Legal last name, spelled as confirmed with the caller."},
    "date_of_birth": {"type": "string", "description": "Date of birth in MM/DD/YYYY, e.g. 03/15/1985."},
    "sex": {
        "type": "string",
        "enum": ["Male", "Female", "Other", "Decline to Answer"],
        "description": "Sex as stated by the caller.",
    },
    "phone_number": {"type": "string", "description": "10-digit U.S. phone number, digits only, e.g. 5551234567."},
    "email": {"type": "string", "description": "Email address, only if the caller chose to give one."},
    "address_line_1": {"type": "string", "description": "Street address, e.g. 123 Maple Street."},
    "address_line_2": {"type": "string", "description": "Apartment, suite or unit, e.g. Apt 4B. Leave out entirely if there is none."},
    "city": {"type": "string", "description": "City, e.g. Springfield."},
    "state": {"type": "string", "description": "Two-letter U.S. state abbreviation, e.g. IL."},
    "zip_code": {"type": "string", "description": "5-digit ZIP code or ZIP+4, e.g. 62704 or 62704-1234."},
    "insurance_provider": {"type": "string", "description": "Insurance company name, e.g. Blue Cross Blue Shield."},
    "insurance_member_id": {"type": "string", "description": "Insurance member/subscriber ID, letters and digits."},
    "preferred_language": {"type": "string", "description": "Preferred language, e.g. English or Spanish. Defaults to English."},
    "emergency_contact_name": {"type": "string", "description": "Emergency contact's full name."},
    "emergency_contact_phone": {"type": "string", "description": "Emergency contact's 10-digit U.S. phone number, digits only."},
}

REQUIRED_PATIENT_FIELDS: list[str] = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
]

PATIENT_FIELD_NAMES: list[str] = list(PATIENT_PROPERTIES)

TOOL_LOOKUP = "lookup_patient"
TOOL_REGISTER = "register_patient"
TOOL_UPDATE = "update_patient"
TOOL_SLOTS = "get_appointment_slots"
TOOL_BOOK = "book_appointment"

# Given to the MODEL (role=system) when Vapi could not reach the backend at all
# (timeout, non-2xx, DNS failure).  With role=system the model composes the
# spoken reply itself, so the prompt's honest-failure and retry rules apply
# instead of a canned sentence that would end the turn.
_REQUEST_FAILED_HINT = (
    "The records system did not respond or returned an error, so nothing was saved or looked up. "
    "Tell the caller honestly that you could not reach it right now and ask whether they would like "
    "you to try once more. If it fails again, apologise and suggest calling back later. Never say "
    "anything was saved."
)


def _function_tool(
    *,
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    server_url: str,
    secret: str | None,
    timeout_seconds: int,
    request_start: str | None = None,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"type": "request-failed", "role": "system", "content": _REQUEST_FAILED_HINT},
        {
            # Spoken by Vapi if the backend is slow (e.g. a serverless cold start).
            "type": "request-response-delayed",
            "content": "Just a moment, this is taking a little longer than usual.",
            "timingMilliseconds": 5000,
        },
    ]
    if request_start:
        messages.insert(0, {"type": "request-start", "content": request_start})
    server: dict[str, Any] = {"url": server_url, "timeoutSeconds": timeout_seconds}
    if secret:
        # Sent by Vapi on every webhook; the backend compares it in constant time.
        server["headers"] = {"x-vapi-secret": secret}
    return {
        "type": "function",
        "async": False,
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
        "server": server,
        "messages": messages,
    }


def build_tools(server_url: str, secret: str | None = None, *, timeout_seconds: int = 20) -> list[dict[str, Any]]:
    """Vapi tool list for the assistant (custom function tools + built-in endCall)."""
    common = dict(server_url=server_url, secret=secret, timeout_seconds=timeout_seconds)

    lookup = _function_tool(
        name=TOOL_LOOKUP,
        description=(
            "Check whether a patient record already exists for a phone number. Call it as soon "
            "as you have the caller's phone number. Call it WITHOUT phone_number to use the "
            "caller ID of the current call (the result tells you which number it used). It only "
            "returns the name on the record, never the stored details."
        ),
        properties={
            "phone_number": {
                "type": "string",
                "description": "10-digit U.S. phone number, digits only. Omit to use the caller ID.",
            }
        },
        required=[],
        **common,
    )

    register = _function_tool(
        name=TOOL_REGISTER,
        description=(
            "Create the new patient record. Call it ONLY after reading every collected detail "
            "back to the caller and getting an explicit yes. Pass canonical values (date "
            "MM/DD/YYYY, phone as 10 digits, state as 2 letters). Omit optional fields the "
            "caller did not provide."
        ),
        properties={
            **PATIENT_PROPERTIES,
            "allow_duplicate_phone": {
                "type": "boolean",
                "description": (
                    "Set true only when a record with this phone number already exists and the "
                    "caller confirmed they are a different person who wants a separate record."
                ),
            },
        },
        required=REQUIRED_PATIENT_FIELDS,
        request_start="Perfect, let me save that for you.",
        **common,
    )

    update = _function_tool(
        name=TOOL_UPDATE,
        description=(
            "Update an existing patient record (returning caller). Requires the patient_id from "
            "lookup_patient and the date of birth the caller stated (verified against the record "
            "for privacy). Pass ONLY the fields that changed, after reading the changes back and "
            "getting a yes."
        ),
        properties={
            "patient_id": {"type": "string", "description": "patient_id returned by lookup_patient."},
            "verified_date_of_birth": {
                "type": "string",
                "description": "Date of birth the caller just stated, MM/DD/YYYY. Must match the record.",
            },
            **PATIENT_PROPERTIES,
        },
        required=["patient_id", "verified_date_of_birth"],
        request_start="Got it, updating your record now.",
        **common,
    )

    slots = _function_tool(
        name=TOOL_SLOTS,
        description=(
            "List the next available first-appointment slots. Call it only if the caller "
            "wants to schedule an appointment after a successful registration."
        ),
        properties={},
        required=[],
        **common,
    )

    book = _function_tool(
        name=TOOL_BOOK,
        description="Book one of the slots returned by get_appointment_slots for the registered patient.",
        properties={
            "patient_id": {"type": "string", "description": "patient_id of the registered patient."},
            "slot_id": {"type": "string", "description": "slot_id of the chosen slot."},
            "reason": {"type": "string", "description": "Optional short reason for the visit."},
        },
        required=["patient_id", "slot_id"],
        request_start="Booking that for you now.",
        **common,
    )

    # Built-in Vapi tool that lets the model hang up after the goodbye.
    end_call = {"type": "endCall"}

    return [lookup, register, update, slots, book, end_call]
