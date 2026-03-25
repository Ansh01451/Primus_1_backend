import sys
import os
import json
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

os.environ.setdefault("SUPPORT_URL", "www.onmeridian.com")
os.environ.setdefault("MAIL_SERVICE", "dummy")
os.environ.setdefault("MAIL_CNN_STRING", "dummy")

from fastapi.testclient import TestClient
from app import app
from auth.jwt_service import JWTService

def run_test():
    client = TestClient(app)
    jwt_service = JWTService()
    
    # Generate a dummy token with CLIENT role
    token = jwt_service.create_access_token(
        subject="ansh.kakkar@onmeridian.com",
        roles=["client", "CLIENT", "Client"], # add variations just in case
        user_type="CLIENT",
        email="ansh.kakkar@onmeridian.com"
    )
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    print("Sending GET request to /client/PR-000007/dashboard...")
    response = client.get("/client/PR-000007/dashboard", headers=headers)
    
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        phases = data.get("phases", [])
        print(f"Number of phases returned by API: {len(phases)}")
        print(f"Progress format: {data.get('progress_percent')}")
        if not phases:
            print("WARNING: API returned 0 phases, but backend function returned 11 locally!")
            print(f"Full response: {json.dumps(data, indent=2)}")
    else:
        print(f"Error Response: {response.text}")

if __name__ == "__main__":
    run_test()
