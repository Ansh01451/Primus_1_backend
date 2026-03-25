import asyncio
import sys
import os
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

os.environ.setdefault("SUPPORT_URL", "http://dummy")
os.environ.setdefault("MAIL_SERVICE", "dummy")
os.environ.setdefault("MAIL_CNN_STRING", "dummy")

from dynamics.services import get_access_token
from client.dashboard.services import get_project_dashboard_details
import json

async def test_dashboard_function():
    p_no = "PR-000007"
    print(f"Testing get_project_dashboard_details for {p_no}...")
    try:
        token = await get_access_token()
        result = await get_project_dashboard_details(p_no, token)
        
        if result:
            print(f"FULL RESULT: {json.dumps(result, indent=2)}")
            phases = result.get('phases', [])
            print(f"SUCCESS! Returned {len(phases)} phases.", flush=True)
            print(f"Progress Percent: {result.get('progress_percent')}%", flush=True)
            if phases:
                for idx, p in enumerate(phases[:3]):
                    print(f"  Phase {idx}: {p.get('phaseName')} (Status: {p.get('status')})", flush=True)
        else:
            print("Returned None!")
            
    except Exception as e:
        print("EXCEPTION CAUGHT:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_dashboard_function())
