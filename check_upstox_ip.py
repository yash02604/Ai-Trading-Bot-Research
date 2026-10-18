import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("UPSTOX_ACCESS_TOKEN")

if not token:
    print("UPSTOX_ACCESS_TOKEN is missing")
    raise SystemExit(1)

response = requests.get(
    "https://api.upstox.com/v2/user/ip",
    headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    },
    timeout=10,
)

print("Status:", response.status_code)
print("Response:", response.text)