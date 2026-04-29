import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))

from contact_identity_repair import (
    build_contact_snapshot,
    choose_survivor,
    plan_contact_repairs,
)


def _contact(
    contact_id: str,
    *,
    phone_normalized: str,
    phone_raw: str,
    wati_contact_id: str | None,
    created_at: str = "2026-04-24T00:00:00+00:00",
    phone_raw_primary: str | None = None,
    phone_identity_key: str | None = None,
    phone_e164: str | None = None,
    normalization_status: str | None = None,
    normalization_region: str | None = None,
    wati_lookup_phone: str | None = None,
) -> dict:
    return {
        "contact_id": contact_id,
        "phone_normalized": phone_normalized,
        "phone_raw": phone_raw,
        "phone_raw_primary": phone_raw_primary,
        "phone_identity_key": phone_identity_key,
        "phone_e164": phone_e164,
        "normalization_status": normalization_status,
        "normalization_region": normalization_region,
        "wati_lookup_phone": wati_lookup_phone,
        "wati_contact_id": wati_contact_id,
        "created_at": created_at,
    }


def _provider_key(
    provider_contact_id: str,
    contact_id: str,
    *,
    provider_phone: str | None = None,
    key_id: str = "pk-1",
    is_primary: bool = True,
) -> dict:
    return {
        "id": key_id,
        "contact_id": contact_id,
        "provider_contact_id": provider_contact_id,
        "provider_phone": provider_phone,
        "is_primary": is_primary,
        "first_seen_at": "2026-04-24T00:00:00+00:00",
        "last_seen_at": "2026-04-24T00:00:00+00:00",
    }


def test_plan_backfills_legacy_identity_fields_and_lookup_phone():
    contacts = [
        _contact(
            "c-1",
            phone_normalized="525539923400",
            phone_raw="525539923400",
            wati_contact_id="wati-1",
        )
    ]
    provider_keys = [_provider_key("wati-1", "c-1", provider_phone="5215539923400")]

    plan = plan_contact_repairs(contacts, provider_keys, {}, default_region="MX")

    assert plan.contacts_examined == 1
    assert len(plan.contact_updates) == 1
    update = plan.contact_updates[0]
    assert update.contact_id == "c-1"
    assert update.fields["phone_identity_key"] == "e164:+525539923400"
    assert update.fields["phone_e164"] == "+525539923400"
    assert update.fields["wati_lookup_phone"] == "5215539923400"
    assert update.fields["phone_normalized"] == "5215539923400"
    assert len(plan.provider_key_updates) == 0
    assert len(plan.merges) == 0
    assert len(plan.conflicts) == 0


def test_choose_survivor_prefers_row_that_already_matches_lookup_phone():
    old_snapshot = build_contact_snapshot(
        _contact(
            "old",
            phone_normalized="525539923400",
            phone_raw="525539923400",
            wati_contact_id="wati-1",
            created_at="2026-04-24T00:00:00+00:00",
        ),
        [_provider_key("wati-1", "old", provider_phone="5215539923400")],
        message_count=0,
        default_region="MX",
    )
    new_snapshot = build_contact_snapshot(
        _contact(
            "new",
            phone_normalized="5215539923400",
            phone_raw="525539923400",
            wati_contact_id="wati-1",
            created_at="2026-04-25T00:00:00+00:00",
            phone_identity_key="e164:+525539923400",
            phone_e164="+525539923400",
            normalization_status="e164",
            normalization_region="MX",
            wati_lookup_phone="5215539923400",
        ),
        [],
        message_count=0,
        default_region="MX",
    )

    survivor = choose_survivor([old_snapshot, new_snapshot])

    assert survivor.contact_id == "new"


def test_plan_repairs_same_provider_duplicate_group_to_single_survivor():
    contacts = [
        _contact(
            "old",
            phone_normalized="525539923400",
            phone_raw="525539923400",
            wati_contact_id="wati-1",
            created_at="2026-04-24T00:00:00+00:00",
        ),
        _contact(
            "new",
            phone_normalized="5215539923400",
            phone_raw="525539923400",
            wati_contact_id="wati-1",
            created_at="2026-04-25T00:00:00+00:00",
            phone_identity_key="e164:+525539923400",
            phone_e164="+525539923400",
            normalization_status="e164",
            normalization_region="MX",
            wati_lookup_phone="5215539923400",
        ),
    ]
    provider_keys = [_provider_key("wati-1", "old", provider_phone="5215539923400")]
    message_counts = {"old": 0, "new": 3}

    plan = plan_contact_repairs(contacts, provider_keys, message_counts, default_region="MX")

    assert len(plan.merges) == 1
    merge = plan.merges[0]
    assert merge.provider_contact_id == "wati-1"
    assert merge.survivor_contact_id == "new"
    assert merge.loser_contact_ids == ("old",)
    assert len(plan.provider_key_updates) == 1
    assert plan.provider_key_updates[0].contact_id == "new"
    assert plan.provider_key_updates[0].provider_contact_id == "wati-1"


def test_plan_is_idempotent_for_already_repaired_contact():
    contacts = [
        _contact(
            "ready",
            phone_normalized="5215539923400",
            phone_raw="525539923400",
            phone_raw_primary="525539923400",
            wati_contact_id="wati-1",
            phone_identity_key="e164:+525539923400",
            phone_e164="+525539923400",
            normalization_status="e164",
            normalization_region="MX",
            wati_lookup_phone="5215539923400",
        )
    ]
    provider_keys = [_provider_key("wati-1", "ready", provider_phone="5215539923400")]

    plan = plan_contact_repairs(contacts, provider_keys, {"ready": 2}, default_region="MX")

    assert len(plan.contact_updates) == 0
    assert len(plan.provider_key_updates) == 0
    assert len(plan.merges) == 0
    assert len(plan.conflicts) == 0


def test_plan_skips_phone_normalized_collision_outside_safe_duplicate_class():
    contacts = [
        _contact(
            "legacy",
            phone_normalized="525539923400",
            phone_raw="525539923400",
            wati_contact_id="wati-1",
        ),
        _contact(
            "other",
            phone_normalized="5215539923400",
            phone_raw="5215539923400",
            wati_contact_id="wati-2",
            phone_identity_key="e164:+525539923400",
            phone_e164="+525539923400",
            normalization_status="assumed_region",
            normalization_region="MX",
            wati_lookup_phone="5215539923400",
        ),
    ]
    provider_keys = [_provider_key("wati-1", "legacy", provider_phone="5215539923400")]

    plan = plan_contact_repairs(contacts, provider_keys, {}, default_region="MX")

    assert len(plan.merges) == 0
    assert len(plan.conflicts) == 1
    assert plan.conflicts[0]["reason"] == "phone_normalized_conflict"
    legacy_update = next(update for update in plan.contact_updates if update.contact_id == "legacy")
    assert "phone_normalized" not in legacy_update.fields
    assert legacy_update.fields["wati_lookup_phone"] == "5215539923400"
