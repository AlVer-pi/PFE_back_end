import os
from dotenv import load_dotenv
from supabase import create_client, Client


# 1. Load the variables from .env into the OS environment
load_dotenv() 

# 2. Grab them using os.getenv
url: str | None = os.getenv("SUPABASE_URL")
key: str | None = os.getenv("SUPABASE_KEY")

# 3. Validation: Stop the app if keys are missing
if not url or not key:
    raise ValueError("Supabase credentials not found in .env file!")

# 4. Create the client instance
supabase: Client = create_client(url, key)
