import json
import sys
from collections import defaultdict
from pathlib import Path

# Allow importing shared modules without installing them
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))

from phone_identity import (
    build_phone_identity,
    count_distinct_identity_keys,
    identity_from_contact_record,
    legacy_wati_lookup_phone,
)


def load_cases():
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "phone_identity_cases.json"
    return json.loads(fixture_path.read_text())


def test_fixture_cases_cover_expected_identity_outputs():
    for case in load_cases():
        identity = build_phone_identity(
            raw_input=case["raw_input"],
            default_region=case.get("default_region"),
            wati_contact_id=case.get("wati_contact_id"),
        )

        expected = case["expected"]
        assert identity.phone_e164 == expected["phone_e164"], case["name"]
        assert identity.phone_identity_key == expected["phone_identity_key"], case["name"]
        assert identity.normalization_status == expected["normalization_status"], case["name"]
        assert identity.normalization_region == expected["normalization_region"], case["name"]
        assert identity.wati_lookup_phone == expected["wati_lookup_phone"], case["name"]


def test_equivalent_fixture_groups_share_a_single_identity_key():
    groups: dict[str, set[str]] = defaultdict(set)

    for case in load_cases():
        if "equivalent_group" not in case:
            continue
        identity = build_phone_identity(
            raw_input=case["raw_input"],
            default_region=case.get("default_region"),
            wati_contact_id=case.get("wati_contact_id"),
        )
        groups[case["equivalent_group"]].add(identity.phone_identity_key)

    assert groups["mx_monterrey"] == {"e164:+528123456789"}
    assert groups["us_sf"] == {"e164:+14155550100"}
    assert groups["uk_london"] == {"e164:+442079460056"}


def test_ambiguous_provider_fallbacks_do_not_collapse():
    ambiguous = [
        case for case in load_cases()
        if case["name"].startswith("ambiguous_local_with_provider")
    ]

    keys = {
        build_phone_identity(
            raw_input=case["raw_input"],
            default_region=case.get("default_region"),
            wati_contact_id=case.get("wati_contact_id"),
        ).phone_identity_key
        for case in ambiguous
    }

    assert len(keys) == 2
    assert keys == {"wati:ambiguous-a", "wati:ambiguous-b"}


def test_identity_from_contact_record_prefers_shadow_then_legacy_fields():
    contact = {
        "phone_raw_primary": None,
        "phone_raw": "+1 (415) 555-0100",
        "wati_lookup_phone": None,
        "phone_normalized": "14155550100",
        "wati_contact_id": "us-1",
    }

    identity = identity_from_contact_record(contact, default_region="US")

    assert identity.phone_identity_key == "e164:+14155550100"
    assert identity.wati_lookup_phone == "14155550100"


def test_count_distinct_identity_keys_counts_only_present_keys():
    identities = [
        build_phone_identity("5218123456789", default_region="MX", wati_contact_id="mx-1"),
        build_phone_identity("+52 81 2345 6789", wati_contact_id="mx-2"),
        build_phone_identity(None, default_region="MX", wati_contact_id=None),
    ]

    assert count_distinct_identity_keys(identities) == 1


def test_legacy_wati_lookup_phone_preserves_mx_mobile_lookup_semantics():
    assert legacy_wati_lookup_phone("+52 81 2345 6789") == "5218123456789"
    assert legacy_wati_lookup_phone("5218123456789") == "5218123456789"
    assert legacy_wati_lookup_phone("+1 (415) 555-0100") == "14155550100"
