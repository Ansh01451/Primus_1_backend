import os
import sys

# Add the Backend directory to sys.path
backend_path = r"c:\Users\AnshKakkar\OneDrive - Meridian Solutions\Desktop\Primus (1)\Primus\Backend"
sys.path.append(backend_path)

import asyncio
from auth.jwt_service import JWTService
from config import settings
from auth.db import collection_map, email_field_map

async def test_auth_me():
    print("Testing auth/me logic...")
    jwt_service = JWTService()
    
    # Simulate a user payload (you might need a real token or mock the verification)
    # Since we want to test the db part, we can just mock the user dict
    user_payload = {
        "sub": "test_id",
        "type": "client",
        "email": "meridian@primuspartners.in" # using email from .env just in case
    }
    
    user_type = user_payload.get("type")
    email = user_payload.get("email")
    
    print(f"User Type: {user_type}")
    print(f"Email: {email}")
    
    try:
        collection = collection_map.get(user_type)
        if not collection:
            print("Error: Collection not found for user type")
            return
            
        print(f"Accessing collection: {collection.name}")
        
        email_field = email_field_map.get(user_type)
        print(f"Email field mapping: {email_field}")
        
        # This is where it likely fails if DB is unreachable or query is wrong
        user_doc = collection.find_one({
            "$or": [
                {email_field: email},
                {"email": email}
            ]
        })
        
        if not user_doc:
            print("User not found in database")
        else:
            print(f"User found: {user_doc.get(email_field) or user_doc.get('email')}")
            
    except Exception as e:
        print(f"CRASH: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_auth_me())
