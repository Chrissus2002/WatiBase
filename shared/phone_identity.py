from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


@dataclass(frozen=True)
class PhoneIdentity:
    raw_input: str | None
    digits_only: str | None
    phone_e164: str | None
    phone_identity_key: str | None
    normalization_status: str
    normalization_region: str | None
    wati_lookup_phone: str | None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def strip_to_digits(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits or None


def normalize_region_code(region: str | None) -> str | None:
    if region is None:
        return None
    normalized = region.strip().upper()
    return normalized or None


def legacy_wati_lookup_phone(value: str | None) -> str | None:
    """
    Compatibility helper that matches the SQL/MX Wati lookup behavior.

    Wati frequently exposes Mexican numbers as either 52XXXXXXXXXX or
    521XXXXXXXXXX. The legacy system inserts the extra "1" after the 52
    country code, and Wati's /getMessages lookup expects that mobile-form
    variant.
    """
    digits = strip_to_digits(value)
    if digits is None:
        return None
    if digits.startswith("52") and not digits.startswith("521"):
        return f"521{digits[2:]}"
    return digits


def identity_from_contact_record(
    contact: Mapping[str, object],
    default_region: str | None = None,
) -> PhoneIdentity:
    raw_phone = (
        contact.get("phone_raw_primary")
        or contact.get("phone_raw")
        or contact.get("wati_lookup_phone")
        or contact.get("phone_normalized")
    )
    return build_phone_identity(
        raw_input=str(raw_phone) if raw_phone is not None else None,
        default_region=default_region,
        wati_contact_id=_string_or_none(contact.get("wati_contact_id")),
        explicit_wati_lookup_phone=_string_or_none(contact.get("wati_lookup_phone")),
    )


def count_distinct_identity_keys(identities: Iterable[PhoneIdentity]) -> int:
    return len(
        {
            identity.phone_identity_key
            for identity in identities
            if identity.phone_identity_key
        }
    )


def build_phone_identity(
    raw_input: str | None,
    default_region: str | None = None,
    wati_contact_id: str | None = None,
    explicit_wati_lookup_phone: str | None = None,
) -> PhoneIdentity:
    normalized_region = normalize_region_code(default_region)
    raw_value = raw_input.strip() if isinstance(raw_input, str) else raw_input
    digits = strip_to_digits(raw_value)

    parse_attempt = _parse_phone_identity(raw_value, digits, normalized_region)
    if parse_attempt is not None:
        e164, status, region = parse_attempt
        return PhoneIdentity(
            raw_input=raw_value,
            digits_only=digits,
            phone_e164=e164,
            phone_identity_key=f"e164:{e164}",
            normalization_status=status,
            normalization_region=region or normalized_region,
            wati_lookup_phone=(
                strip_to_digits(explicit_wati_lookup_phone)
                or _wati_lookup_from_e164(e164)
            ),
        )

    fallback_lookup = strip_to_digits(explicit_wati_lookup_phone) or legacy_wati_lookup_phone(raw_value)

    if wati_contact_id:
        return PhoneIdentity(
            raw_input=raw_value,
            digits_only=digits,
            phone_e164=None,
            phone_identity_key=f"wati:{wati_contact_id}",
            normalization_status="wati_fallback",
            normalization_region=normalized_region,
            wati_lookup_phone=fallback_lookup,
        )

    if digits:
        return PhoneIdentity(
            raw_input=raw_value,
            digits_only=digits,
            phone_e164=None,
            phone_identity_key=f"raw:{digits}",
            normalization_status="raw_fallback",
            normalization_region=normalized_region,
            wati_lookup_phone=fallback_lookup,
        )

    return PhoneIdentity(
        raw_input=raw_value,
        digits_only=None,
        phone_e164=None,
        phone_identity_key=None,
        normalization_status="missing",
        normalization_region=normalized_region,
        wati_lookup_phone=None,
    )


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_phone_identity(
    raw_input: str | None,
    digits: str | None,
    default_region: str | None,
) -> tuple[str, str, str | None] | None:
    for candidate, region, status in _candidate_inputs(raw_input, digits, default_region):
        try:
            number = phonenumbers.parse(candidate, region)
        except NumberParseException:
            continue

        if not phonenumbers.is_possible_number(number):
            continue
        if not phonenumbers.is_valid_number(number):
            continue

        e164 = phonenumbers.format_number(number, PhoneNumberFormat.E164)
        resolved_region = phonenumbers.region_code_for_number(number) or region or default_region
        return e164, status, normalize_region_code(resolved_region)

    return None


def _candidate_inputs(
    raw_input: str | None,
    digits: str | None,
    default_region: str | None,
) -> list[tuple[str, str | None, str]]:
    candidates: list[tuple[str, str | None, str]] = []
    seen: set[tuple[str, str | None, str]] = set()

    def add(candidate: str, region: str | None, status: str) -> None:
        key = (candidate, region, status)
        if candidate and key not in seen:
            seen.add(key)
            candidates.append(key)

    if raw_input:
        raw = raw_input.strip()
        if raw.startswith("+"):
            add(raw, None, "e164")
        elif raw.startswith("00"):
            add(f"+{raw[2:]}", None, "e164")

    if digits:
        if digits.startswith("521") and len(digits) >= 13:
            add(f"+52{digits[3:]}", None, "assumed_region")
        if len(digits) >= 11:
            add(f"+{digits}", None, "e164")
        if default_region:
            add(digits, default_region, "assumed_region")

    return candidates


def _wati_lookup_from_e164(e164: str) -> str:
    digits = strip_to_digits(e164) or ""
    if digits.startswith("52"):
        return legacy_wati_lookup_phone(digits) or digits
    return digits
