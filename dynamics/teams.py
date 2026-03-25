import calendar
from typing import List, Optional
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from azure.identity import ClientSecretCredential
from config import settings
import requests
import pytz
from fastapi import HTTPException

load_dotenv()
TENANT_ID = settings.azure_tenant_id
CLIENT_ID = settings.azure_client_id  
CLIENT_SECRET = settings.azure_client_secret
SCOPES = ["https://graph.microsoft.com/.default"]

# Initialize clients
credential = ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET)

def get_graph_token():
    token = credential.get_token(*SCOPES)
    return token.token

def fetch_user_meetings(user_email: str, scope: str = "week"):
    """
    Fetch Teams online meetings for the current week, month, or past year for a given user.
    """
    now = datetime.now()
    if scope == "day":
        dt_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        dt_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif scope == "week":
        start_of_week = now - timedelta(days=now.weekday())
        end_of_week = start_of_week + timedelta(days=6, hour=23, minute=59, second=59)
        dt_start, dt_end = start_of_week, end_of_week
    elif scope == "month":
        dt_start = now.replace(day=1, hour=0, minute=0, second=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        dt_end = now.replace(day=last_day, hour=23, minute=59, second=59)
    elif scope == "past":
        dt_start = now - timedelta(days=365)
        dt_end = now
    else:
        raise ValueError("Invalid scope. Use 'week', 'month', or 'past'.")

    token = get_graph_token()
    url = (
        f"https://graph.microsoft.com/v1.0/users/{user_email}/calendarView"
        f"?startDateTime={dt_start.isoformat()}Z&endDateTime={dt_end.isoformat()}Z"
    )
    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="India Standard Time"'}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"Graph API error {resp.status_code}: {resp.text}")
    
    events = resp.json().get("value", [])
    meetings = []

    for e in events:
        if not e.get("isOnlineMeeting"):
            continue

        attendees = [
            {
                "email": a["emailAddress"]["address"],
                "name": a["emailAddress"].get("name"),
                "type": a.get("type"),
                "status": a.get("status", {}).get("response")
            }
            for a in e.get("attendees", [])
        ]

        meeting = {
            "id": e["id"],
            "subject": e.get("subject"),
            "start": datetime.fromisoformat(e["start"]["dateTime"]),
            "end": datetime.fromisoformat(e["end"]["dateTime"]),
            "duration_minutes": (
                datetime.fromisoformat(e["end"]["dateTime"]) - datetime.fromisoformat(e["start"]["dateTime"])
            ).total_seconds() / 60,
            "joinUrl": e.get("onlineMeeting", {}).get("joinUrl"),
            "location": e.get("location", {}).get("displayName"),
            "attendees": attendees,
            "categories": e.get("categories", []),
            "body": e.get("body", {}).get("content", "")
        }
        meetings.append(meeting)

    return meetings

def schedule_meeting(
    organizer_email: str,
    client_emails: list[str],
    subject: str,
    description: str,
    start_time: datetime,
    end_time: datetime,
    category: Optional[str] = None,
    agenda: Optional[List[str]] = None
):
    token = get_graph_token()
    url = f"https://graph.microsoft.com/v1.0/users/{organizer_email}/events"

    ist = pytz.timezone("Asia/Kolkata")
    if start_time.tzinfo is None:
        start_time = ist.localize(start_time)
    else:
        start_time = start_time.astimezone(ist)

    if end_time.tzinfo is None:
        end_time = ist.localize(end_time)
    else:
        end_time = end_time.astimezone(ist)

    attendees = [
        {
            "emailAddress": {
                "address": email,
                "name": email.split("@")[0]
            },
            "type": "required"
        }
        for email in client_emails
    ]

    body_content = description or f"Meeting scheduled with {', '.join(client_emails)}"
    if agenda:
        agenda_html = "<ul>" + "".join([f"<li>{item}</li>" for item in agenda]) + "</ul>"
        body_content = f"{body_content}<br><br><b>Agenda:</b><br>{agenda_html}"

    meeting_data = {
        "subject": subject,
        "body": {
            "contentType": "HTML",
            "content": body_content
        },
        "categories": [category] if category else [],
        "start": {
            "dateTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "India Standard Time"
        },
        "end": {
            "dateTime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "India Standard Time"
        },
        "location": {
            "displayName": "Microsoft Teams Meeting"
        },
        "attendees": attendees,
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness"
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=meeting_data)

    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return resp.json()

def cancel_meeting(
    organizer_email: str,
    meeting_id: str,
    cancellation_message: str = "The meeting has been cancelled."
):
    token = get_graph_token()
    url = f"https://graph.microsoft.com/v1.0/users/{organizer_email}/events/{meeting_id}/cancel"

    body = {"comment": cancellation_message}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=body)

    if resp.status_code not in (202, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return {"status": "cancelled", "meetingId": meeting_id}

def update_meeting(
    organizer_email: str,
    meeting_id: str,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    agenda: Optional[List[str]] = None
):
    token = get_graph_token()
    url = f"https://graph.microsoft.com/v1.0/users/{organizer_email}/events/{meeting_id}"

    ist = pytz.timezone("Asia/Kolkata")
    update_data = {}

    if subject:
        update_data["subject"] = subject
    
    if description or agenda:
        body_content = description or ""
        if agenda:
            agenda_html = "<ul>" + "".join([f"<li>{item}</li>" for item in agenda]) + "</ul>"
            body_content = f"{body_content}<br><br><b>Agenda:</b><br>{agenda_html}"
        update_data["body"] = {"contentType": "HTML", "content": body_content}

    if start_time:
        if start_time.tzinfo is None:
            start_time = ist.localize(start_time)
        update_data["start"] = {
            "dateTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "India Standard Time"
        }

    if end_time:
        if end_time.tzinfo is None:
            end_time = ist.localize(end_time)
        update_data["end"] = {
            "dateTime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "India Standard Time"
        }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.patch(url, headers=headers, json=update_data)

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return resp.json()


def get_user_presence(user_id: str):
    """
    Fetch real-time presence for a specific user.
    """
    token = get_graph_token()
    url = f"https://graph.microsoft.com/v1.0/users/{user_id}/presence"
    headers = {"Authorization": f"Bearer {token}"}
    
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return {"availability": "Offline", "activity": "Offline"}
        
    return resp.json()


def get_batch_presence(user_ids: List[str]):
    """
    Fetch presence for multiple users in a single request.
    """
    if not user_ids:
        return []
        
    token = get_graph_token()
    url = "https://graph.microsoft.com/v1.0/communications/getPresencesByUserId"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Graph limit for this endpoint is usually 650 users
    payload = {"ids": user_ids}
    
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        return []
        
    return resp.json().get("value", [])