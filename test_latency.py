import time
import asyncio
import sys
import os

# Set environment file path for pydantic-settings
backend_path = r"c:\Users\AnshKakkar\OneDrive - Meridian Solutions\Desktop\Primus (1)\Primus\Backend"
os.environ["ENV_FILE"] = os.path.join(backend_path, ".env")
sys.path.append(backend_path)

from client.dashboard.services import get_project_dashboard_details

async def measure_latency(project_no: str):
    print(f"Measuring latency for project: {project_no}")
    start_time = time.perf_counter()
    
    try:
        data = await get_project_dashboard_details(project_no)
        end_time = time.perf_counter()
        
        latency = end_time - start_time
        print(f"Latency: {latency:.4f} seconds")
        
        # Simple stats
        if data:
            print(f"Phases fetched: {len(data.get('phases', []))}")
            print(f"Total Amount: {data.get('total_actual_amount')}")
        else:
            print("No data returned")
            
    except Exception as e:
        print(f"Failed to measure latency: {e}")

if __name__ == "__main__":
    # Project ID from the Open Files or common test ID
    test_project_no = "123" 
    asyncio.run(measure_latency(test_project_no))
