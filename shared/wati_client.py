import json
import os
import requests
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase_client import SupabaseClient


def parse_wati_time(value) -> datetime | None:
    """Parse a Wati timestamp into an aware UTC datetime.

    Wati mixes epoch seconds, ISO strings with a `Z` suffix, and ISO strings
    with no offset at all. The offset-less ones are UTC, which is also how the
    database promotes them.
    """
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class WatiClient:
    def __init__(self, tenant_id: str, api_url: str, api_token: str):
        if not tenant_id:
            raise ValueError("Tenant ID is required")
        if not api_url or not api_token:
            raise ValueError("Wati API URL and token are required")

        self.tenant_id = tenant_id
        self.api_token = api_token
        self.url = api_url.rstrip("/")
        self.headers = {
            "Authorization": self._authorization_header(api_token),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _authorization_header(api_token: str) -> str:
        token = api_token.strip()
        if token.lower().startswith("bearer "):
            return token
        return f"Bearer {token}"
        
    def __str__(self):
        return f"WatiClient(tenant_id={self.tenant_id}, url={self.url})"

    @classmethod
    def from_supabase(cls, supabase_client, db_tenant_id: int) -> "WatiClient":
        """Create a WatiClient by loading credentials from the tenant_connectors table."""
        result = (
            supabase_client.get_client()
            .table("tenant_connectors")
            .select("tenant_id, wati_api_url, wati_api_token")
            .eq("tenant_id", db_tenant_id)
            .eq("provider", "wati")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise ValueError(f"No active Wati connector found for tenant {db_tenant_id}")
        row = rows[0]
        return cls(
            tenant_id=str(db_tenant_id),
            api_url=row["wati_api_url"],
            api_token=row["wati_api_token"],
        )
    
    def _request_with_retry(self, session, url, max_retries=3, retry_wait=60):
        for attempt in range(max_retries):
            response = session.get(url)
            if response.status_code == 429:
                if attempt < max_retries - 1:
                    print(f"Rate limited (429). Waiting {retry_wait}s before retry ({attempt + 1}/{max_retries})...")
                    time.sleep(retry_wait)
                    continue
                raise Exception(f"Rate limited at url: {url} after {max_retries} retries")
            if response.status_code != 200:
                raise Exception(f"Request failed at url: {url} (status {response.status_code})")
            return response
        raise Exception(f"Request failed at url: {url} after {max_retries} retries")

    def get_contacts(self, max_pages: int = None) -> list:

        all_contacts = []
        pages_fetched = 0
        with requests.Session() as session:
            print(f"Fetching contacts for tenant {self.tenant_id}")
            session.headers.update(self.headers)
            url = self.url + "/api/v1/getContacts?pageSize=200"
            while url is not None:
                if max_pages is not None and pages_fetched >= max_pages:
                    break
                print(url)
                response = self._request_with_retry(session, url)
                data = response.json()
                all_contacts.extend(data.get("contact_list", []))
                pages_fetched += 1
                links = data.get("link") or {}
                url = links.get("nextPage")
                if url is not None:
                    time.sleep(5)

            return all_contacts

    def get_messages(self, phone_number, page_size = 1000) -> list:
        all_messages = {}
        page_number = 1

        with requests.Session() as session:
            session.headers.update(self.headers)

            while True:
                url = self.url + f"/api/v1/getMessages/{phone_number}?pageSize={page_size}&page={page_number}"
                response = self._request_with_retry(session, url)
                data = response.json()
                if phone_number not in all_messages:
                    all_messages[phone_number] = []
                items = (data.get("messages") or {}).get("items") or []
                all_messages[phone_number].extend(items)

                if len(items) < page_size:
                    break
                page_number += 1

            return all_messages

def main():
    load_dotenv()
    supa = SupabaseClient(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    tenant_id = os.getenv("WATI_ACCOUNT_ID")
    wati = WatiClient.from_supabase(supa, tenant_id)
    messages = wati.get_messages('123456789')
    print(json.dumps(messages, indent=4))

if __name__ == "__main__":
    main()
