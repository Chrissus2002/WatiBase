from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from phone_identity import PhoneIdentity, build_phone_identity
from supabase_client import SupabaseClient


@dataclass(frozen=True)
class ProviderKeySnapshot:
    id: str | None
    contact_id: str
    provider_contact_id: str | None
    provider_phone: str | None
    is_primary: bool
    first_seen_at: str | None
    last_seen_at: str | None


@dataclass(frozen=True)
class ContactSnapshot:
    contact: Mapping[str, Any]
    provider_keys: tuple[ProviderKeySnapshot, ...]
    message_count: int
    provider_contact_id: str | None
    provider_phone: str | None
    raw_phone_input: str | None
    identity: PhoneIdentity

    @property
    def contact_id(self) -> str:
        return str(self.contact["contact_id"])


@dataclass(frozen=True)
class ContactUpdatePlan:
    contact_id: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class ProviderKeyUpdatePlan:
    existing_id: str | None
    contact_id: str
    provider_contact_id: str
    provider_phone: str | None
    is_primary: bool


@dataclass(frozen=True)
class MergePlan:
    provider_contact_id: str
    survivor_contact_id: str
    loser_contact_ids: tuple[str, ...]


@dataclass(frozen=True)
class RepairPlan:
    contacts_examined: int
    contact_updates: tuple[ContactUpdatePlan, ...]
    provider_key_updates: tuple[ProviderKeyUpdatePlan, ...]
    merges: tuple[MergePlan, ...]
    conflicts: tuple[dict[str, str], ...]


def build_contact_snapshot(
    contact: Mapping[str, Any],
    provider_keys: Iterable[Mapping[str, Any]],
    message_count: int,
    default_region: str | None,
) -> ContactSnapshot:
    provider_key_snapshots = tuple(
        ProviderKeySnapshot(
            id=_string_or_none(provider_key.get("id")),
            contact_id=str(provider_key["contact_id"]),
            provider_contact_id=_string_or_none(provider_key.get("provider_contact_id")),
            provider_phone=_string_or_none(provider_key.get("provider_phone")),
            is_primary=bool(provider_key.get("is_primary", True)),
            first_seen_at=_string_or_none(provider_key.get("first_seen_at")),
            last_seen_at=_string_or_none(provider_key.get("last_seen_at")),
        )
        for provider_key in provider_keys
    )
    primary_key = _select_primary_provider_key(provider_key_snapshots)
    provider_contact_id = (
        primary_key.provider_contact_id
        if primary_key is not None
        else _string_or_none(contact.get("wati_contact_id"))
    )
    provider_phone = (
        primary_key.provider_phone
        if primary_key is not None
        else _string_or_none(contact.get("wati_lookup_phone"))
        or _string_or_none(contact.get("phone_normalized"))
    )
    raw_phone_input = (
        _string_or_none(contact.get("phone_raw_primary"))
        or _string_or_none(contact.get("phone_raw"))
        or provider_phone
    )
    identity = build_phone_identity(
        raw_input=raw_phone_input,
        default_region=default_region,
        wati_contact_id=provider_contact_id,
        explicit_wati_lookup_phone=provider_phone,
    )
    return ContactSnapshot(
        contact=contact,
        provider_keys=provider_key_snapshots,
        message_count=message_count,
        provider_contact_id=provider_contact_id,
        provider_phone=provider_phone,
        raw_phone_input=raw_phone_input,
        identity=identity,
    )


def choose_survivor(snapshots: Iterable[ContactSnapshot]) -> ContactSnapshot:
    snapshots = tuple(snapshots)
    if not snapshots:
        raise ValueError("choose_survivor() requires at least one contact")
    return max(snapshots, key=_survivor_score)


def plan_contact_repairs(
    contacts: Iterable[Mapping[str, Any]],
    provider_keys: Iterable[Mapping[str, Any]],
    message_counts: Mapping[str, int],
    default_region: str | None,
    restrict_provider_contact_ids: set[str] | None = None,
) -> RepairPlan:
    provider_keys_by_contact: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for provider_key in provider_keys:
        provider_keys_by_contact[str(provider_key["contact_id"])].append(provider_key)

    contact_snapshots = [
        build_contact_snapshot(
            contact=contact,
            provider_keys=provider_keys_by_contact.get(str(contact["contact_id"]), []),
            message_count=int(message_counts.get(str(contact["contact_id"]), 0)),
            default_region=default_region,
        )
        for contact in contacts
    ]
    if restrict_provider_contact_ids is not None:
        selected = [
            snapshot
            for snapshot in contact_snapshots
            if snapshot.provider_contact_id in restrict_provider_contact_ids
        ]
    else:
        selected = contact_snapshots

    selected_ids = {snapshot.contact_id for snapshot in selected}
    if not selected:
        return RepairPlan(
            contacts_examined=0,
            contact_updates=(),
            provider_key_updates=(),
            merges=(),
            conflicts=(),
        )

    current_phone_owners = {
        _string_or_none(snapshot.contact.get("phone_normalized")): snapshot.contact_id
        for snapshot in contact_snapshots
        if _string_or_none(snapshot.contact.get("phone_normalized")) is not None
    }

    groups_by_provider_contact_id: dict[str, list[ContactSnapshot]] = defaultdict(list)
    for snapshot in contact_snapshots:
        if snapshot.provider_contact_id:
            groups_by_provider_contact_id[snapshot.provider_contact_id].append(snapshot)

    merges: list[MergePlan] = []
    merged_loser_ids: set[str] = set()
    survivor_ids: set[str] = set()
    for provider_contact_id, snapshots in groups_by_provider_contact_id.items():
        group = [snapshot for snapshot in snapshots if snapshot.contact_id in selected_ids]
        if len(group) <= 1:
            continue
        survivor = choose_survivor(group)
        losers = tuple(snapshot.contact_id for snapshot in group if snapshot.contact_id != survivor.contact_id)
        if not losers:
            continue
        merges.append(
            MergePlan(
                provider_contact_id=provider_contact_id,
                survivor_contact_id=survivor.contact_id,
                loser_contact_ids=losers,
            )
        )
        survivor_ids.add(survivor.contact_id)
        merged_loser_ids.update(losers)

    contact_updates: list[ContactUpdatePlan] = []
    provider_key_updates: list[ProviderKeyUpdatePlan] = []
    conflicts: list[dict[str, str]] = []

    for snapshot in selected:
        if snapshot.contact_id in merged_loser_ids:
            continue

        fields, conflict_reasons = _build_contact_update_fields(
            snapshot=snapshot,
            current_phone_owners=current_phone_owners,
            contacts_being_removed=merged_loser_ids,
        )
        if fields:
            contact_updates.append(ContactUpdatePlan(contact_id=snapshot.contact_id, fields=fields))
        conflicts.extend(conflict_reasons)

        provider_key_plan = _build_provider_key_update(snapshot)
        if provider_key_plan is not None:
            provider_key_updates.append(provider_key_plan)

    return RepairPlan(
        contacts_examined=len(selected),
        contact_updates=tuple(contact_updates),
        provider_key_updates=tuple(provider_key_updates),
        merges=tuple(merges),
        conflicts=tuple(conflicts),
    )


class ContactIdentityRepair:
    def __init__(self, supabase_client: SupabaseClient, db_tenant_id: int):
        self.supabase_client = supabase_client
        self.db_tenant_id = db_tenant_id

    def run(self, batch_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        client = self.supabase_client.get_client()
        default_region = self._load_default_phone_region(client)
        contacts = self._load_contacts(client)
        provider_keys = self._load_provider_keys(client)
        message_counts = self._load_message_counts(client)
        restrict_provider_contact_ids = self._load_batch_provider_contact_ids(client, batch_id) if batch_id else None

        plan = plan_contact_repairs(
            contacts=contacts,
            provider_keys=provider_keys,
            message_counts=message_counts,
            default_region=default_region,
            restrict_provider_contact_ids=restrict_provider_contact_ids,
        )
        summary = {
            "tenant_id": self.db_tenant_id,
            "batch_id": batch_id,
            "dry_run": dry_run,
            "default_region": default_region,
            "contacts_examined": plan.contacts_examined,
            "contact_updates": len(plan.contact_updates),
            "provider_key_updates": len(plan.provider_key_updates),
            "merge_groups": len(plan.merges),
            "merged_contacts": sum(len(merge.loser_contact_ids) for merge in plan.merges),
            "conflicts": len(plan.conflicts),
            "conflict_details": list(plan.conflicts),
        }
        if dry_run:
            return summary

        now = datetime.now(timezone.utc).isoformat()

        for merge in plan.merges:
            for loser_contact_id in merge.loser_contact_ids:
                client.schema("public").rpc(
                    "merge_canonical_contacts",
                    {
                        "p_survivor_contact_id": merge.survivor_contact_id,
                        "p_loser_contact_id": loser_contact_id,
                    },
                ).execute()

        for contact_update in plan.contact_updates:
            update_fields = dict(contact_update.fields)
            update_fields["updated_at"] = now
            client.schema("canonical").table("contacts").update(update_fields).eq(
                "contact_id",
                contact_update.contact_id,
            ).execute()

        for provider_key_update in plan.provider_key_updates:
            existing_id = provider_key_update.existing_id
            if existing_id is None:
                existing_rows = (
                    client.schema("canonical").table("contact_provider_keys")
                    .select("id")
                    .eq("tenant_id", self.db_tenant_id)
                    .eq("provider", "wati")
                    .eq("provider_contact_id", provider_key_update.provider_contact_id)
                    .limit(1)
                    .execute()
                ).data or []
                if existing_rows:
                    existing_id = existing_rows[0]["id"]

            if existing_id is not None:
                client.schema("canonical").table("contact_provider_keys").update(
                    {
                        "contact_id": provider_key_update.contact_id,
                        "provider_phone": provider_key_update.provider_phone,
                        "is_primary": provider_key_update.is_primary,
                        "updated_at": now,
                    }
                ).eq("id", existing_id).execute()
            else:
                client.schema("canonical").table("contact_provider_keys").insert(
                    {
                        "contact_id": provider_key_update.contact_id,
                        "tenant_id": self.db_tenant_id,
                        "provider": "wati",
                        "provider_contact_id": provider_key_update.provider_contact_id,
                        "provider_phone": provider_key_update.provider_phone,
                        "is_primary": provider_key_update.is_primary,
                        "first_seen_at": now,
                        "last_seen_at": now,
                        "updated_at": now,
                    }
                ).execute()

        return summary

    def _load_default_phone_region(self, client) -> str | None:
        result = (
            client.table("tenant_connectors")
            .select("default_phone_region")
            .eq("tenant_id", self.db_tenant_id)
            .eq("provider", "wati")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return _string_or_none(rows[0].get("default_phone_region"))

    def _load_batch_provider_contact_ids(self, client, batch_id: str) -> set[str]:
        rows = self._fetch_paginated(
            lambda: client.schema("staging").table("contacts_raw_data")
            .select("wati_contact_id")
            .eq("tenant_id", self.db_tenant_id)
            .eq("batch_id", batch_id)
            .order("wati_contact_id")
        )
        return {
            provider_contact_id
            for row in rows
            for provider_contact_id in [_string_or_none(row.get("wati_contact_id"))]
            if provider_contact_id is not None
        }

    def _load_contacts(self, client) -> list[dict[str, Any]]:
        return self._fetch_paginated(
            lambda: client.schema("canonical").table("contacts")
            .select(
                "contact_id, tenant_id, batch_id, phone_normalized, phone_raw, phone_raw_primary, "
                "phone_identity_key, phone_e164, normalization_status, normalization_region, "
                "wati_lookup_phone, wati_contact_id, source, created_at, updated_at"
            )
            .eq("tenant_id", self.db_tenant_id)
            .order("created_at")
        )

    def _load_provider_keys(self, client) -> list[dict[str, Any]]:
        return self._fetch_paginated(
            lambda: client.schema("canonical").table("contact_provider_keys")
            .select(
                "id, contact_id, provider_contact_id, provider_phone, is_primary, "
                "first_seen_at, last_seen_at"
            )
            .eq("tenant_id", self.db_tenant_id)
            .eq("provider", "wati")
            .order("provider_contact_id")
        )

    def _load_message_counts(self, client) -> dict[str, int]:
        rows = self._fetch_paginated(
            lambda: client.schema("canonical").table("messages")
            .select("contact_id")
            .eq("tenant_id", self.db_tenant_id)
            .order("contact_id")
        )
        return dict(Counter(str(row["contact_id"]) for row in rows))

    def _fetch_paginated(self, query_factory) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        page_size = 1000
        while True:
            page = query_factory().range(offset, offset + page_size - 1).execute().data or []
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows


def _build_contact_update_fields(
    snapshot: ContactSnapshot,
    current_phone_owners: Mapping[str | None, str],
    contacts_being_removed: set[str],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    fields: dict[str, Any] = {}
    conflicts: list[dict[str, str]] = []
    identity = snapshot.identity
    contact = snapshot.contact

    if identity.raw_input and identity.raw_input != _string_or_none(contact.get("phone_raw_primary")):
        fields["phone_raw_primary"] = identity.raw_input

    for field_name, value in (
        ("phone_identity_key", identity.phone_identity_key),
        ("phone_e164", identity.phone_e164),
        ("normalization_status", identity.normalization_status),
        ("normalization_region", identity.normalization_region),
        ("wati_lookup_phone", identity.wati_lookup_phone),
    ):
        if value != contact.get(field_name):
            fields[field_name] = value

    if snapshot.provider_contact_id and snapshot.provider_contact_id != _string_or_none(contact.get("wati_contact_id")):
        fields["wati_contact_id"] = snapshot.provider_contact_id

    target_phone_normalized = identity.wati_lookup_phone
    current_phone_normalized = _string_or_none(contact.get("phone_normalized"))
    if target_phone_normalized and target_phone_normalized != current_phone_normalized:
        owner_contact_id = current_phone_owners.get(target_phone_normalized)
        if owner_contact_id is None or owner_contact_id == snapshot.contact_id or owner_contact_id in contacts_being_removed:
            fields["phone_normalized"] = target_phone_normalized
        else:
            conflicts.append(
                {
                    "contact_id": snapshot.contact_id,
                    "reason": "phone_normalized_conflict",
                    "target_phone_normalized": target_phone_normalized,
                    "occupied_by_contact_id": owner_contact_id,
                }
            )

    return fields, conflicts


def _build_provider_key_update(snapshot: ContactSnapshot) -> ProviderKeyUpdatePlan | None:
    provider_contact_id = snapshot.provider_contact_id
    if provider_contact_id is None:
        return None

    desired_provider_phone = snapshot.identity.wati_lookup_phone or snapshot.provider_phone
    existing = _select_primary_provider_key(snapshot.provider_keys)
    if existing is None:
        return ProviderKeyUpdatePlan(
            existing_id=None,
            contact_id=snapshot.contact_id,
            provider_contact_id=provider_contact_id,
            provider_phone=desired_provider_phone,
            is_primary=True,
        )

    if (
        existing.contact_id == snapshot.contact_id
        and existing.provider_phone == desired_provider_phone
        and existing.is_primary
    ):
        return None

    return ProviderKeyUpdatePlan(
        existing_id=existing.id,
        contact_id=snapshot.contact_id,
        provider_contact_id=provider_contact_id,
        provider_phone=desired_provider_phone,
        is_primary=True,
    )


def _select_primary_provider_key(provider_keys: Iterable[ProviderKeySnapshot]) -> ProviderKeySnapshot | None:
    ordered = sorted(
        provider_keys,
        key=lambda provider_key: (
            int(provider_key.is_primary),
            _sortable_timestamp(provider_key.last_seen_at),
            _sortable_timestamp(provider_key.first_seen_at),
        ),
        reverse=True,
    )
    return ordered[0] if ordered else None


def _survivor_score(snapshot: ContactSnapshot) -> tuple[int, int, int, float]:
    matches_lookup = int(
        snapshot.identity.wati_lookup_phone is not None
        and _string_or_none(snapshot.contact.get("phone_normalized")) == snapshot.identity.wati_lookup_phone
    )
    identity_richness = sum(
        1
        for field_name in (
            "phone_identity_key",
            "phone_e164",
            "normalization_status",
            "normalization_region",
            "wati_lookup_phone",
            "phone_raw_primary",
        )
        if _string_or_none(snapshot.contact.get(field_name)) is not None
    )
    return (
        matches_lookup,
        identity_richness,
        int(snapshot.message_count),
        -_sortable_timestamp(_string_or_none(snapshot.contact.get("created_at"))),
    )


def _sortable_timestamp(value: str | None) -> float:
    if value is None:
        return 0.0
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
