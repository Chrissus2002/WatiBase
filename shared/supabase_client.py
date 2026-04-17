import supabase
import os

from dotenv import load_dotenv

load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_TOKEN")

class SupabaseClient:
    def __init__(self, supabase_url: str, supabase_key: str):
        self.client = supabase.create_client(supabase_url, supabase_key)
    
    def get_client(self):
        return self.client

    def test_connection(self):
        try:
            print(self.client.table('tenants').select('*').limit(1).execute())
            return True
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False
    
    def get_tenant_info(self, tenant_id: str = None):
        try:
            client = self.client
            if tenant_id == None:
                result = client.table("tenants").select("*").execute()
            else:
                result = client.table("tenants").select("*").eq("id", tenant_id).execute()
            return result
        except Exception as e:
            print(f"Failed to get tenant info: {e}")
            return False
    

def main():
    supabase = SupabaseClient(supabase_url, supabase_key)
    print(supabase.test_connection())
    print(supabase.get_tenant_info())

if __name__ == "__main__":
    main()