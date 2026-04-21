import os
import requests
from dotenv import load_dotenv
import time
import json

load_dotenv()

wati_api_key = os.getenv("WATI_API_TOKEN")
wati_base_url = os.getenv("WATI_ENDPOINT")

class WatiClient:
    def __init__(self, tenant_id: str = None):
        self.api_key = wati_api_key
        self.base_url = wati_base_url
        self.tenant_id = tenant_id

        if tenant_id == None:
            raise ValueError("Tenant ID is required")

        self.url = self.base_url + self.tenant_id
        self.headers = {
            "Authorization": f"{self.api_key}",
            "Content-Type": "application/json"
        }
        
    def __str__(self):
        return f"WatiClient(tenant_id={self.tenant_id}, url={self.url})"
    
    def get_contacts(self) -> list:

        all_contacts = []
        with requests.Session() as session:
            print(f"Fetching contacts for tenant {self.tenant_id}")
            session.headers.update(self.headers)
            url = self.url + "/api/v1/getContacts?pageSize=200"
            while url is not None:
                print(url)
                response = session.get(url)
                if response.status_code != 200:
                    raise Exception(f"Failed to resquest contacts at url: {url}")
                data = response.json()
                all_contacts.extend(data.get("contact_list", []))
                url = (data.get("link")).get("nextPage")
                if url is not None:
                    time.sleep(5)

            return all_contacts

    def get_messages(self, phone_number, page_size = 1000) -> list:
        all_messages = {}
        page_number = 1

        with requests.Session() as session:
            print(f"Fetching messages for tenant {self.tenant_id} where the phone number is {phone_number}")
            session.headers.update(self.headers)

            while True:
                url = self.url + f"/api/v1/getMessages/{phone_number}?pageSize={page_size}&page={page_number}"
                response = session.get(url)
                if response.status_code != 200:
                    raise Exception(f"Failed to request messages at url: {url}")
                data = response.json()
                if phone_number not in all_messages:
                    all_messages[phone_number] = []
                all_messages[phone_number] = data.get("messages")

                if len(data.get("messages", [])) < page_size:
                    break
                page_number += 1
            
            return all_messages

def main():
    wati = WatiClient(os.getenv("WATI_ACCOUNT_ID"))
    #contacts = wati.get_contacts()
    messages = wati.get_messages('123456789')
    print(json.dumps(messages, indent=4))

if __name__ == "__main__":
    main()