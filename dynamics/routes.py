from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel
from .teams import fetch_user_meetings, schedule_meeting, cancel_meeting, update_meeting
from auth.middleware import get_current_user, require_roles
from auth.roles import Role

router = APIRouter(
    prefix="/dynamics", 
    tags=["Dynamics"],
    dependencies=[Depends(require_roles(Role.CLIENT, Role.ADMIN, Role.VENDOR, Role.ALUMNI, Role.ADVISOR))])

class MeetingListRequest(BaseModel):
    user_email: str
    scope: str = "week"  # can be 'week', 'month', or 'past'

class ScheduleMeetingRequest(BaseModel):
    organizer_email: str
    client_emails: List[str]
    subject: str
    description: str
    start_time: datetime
    end_time: datetime
    category: Optional[str] = None
    agenda: Optional[List[str]] = None

class UpdateMeetingRequest(BaseModel):
    organizer_email: str
    meeting_id: str
    subject: Optional[str] = None
    description: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    agenda: Optional[List[str]] = None

class CancelMeetingRequest(BaseModel):
    organizer_email: str
    meeting_id: str
    message: str = "The meeting has been cancelled."

@router.post("/meetings")
def list_and_save_weekly_meetings(payload: MeetingListRequest, user: dict = Depends(get_current_user)):
    """
    API route to return user's Teams meetings for this week, month, or past.
    """
    email = (user.get("email") or "").lower()
    req_email = (payload.user_email or "").lower()
    
    if email != req_email and "admin" not in user.get("roles", []):
         raise HTTPException(
            status_code=403,
            detail=f"User not Authorised to access meetings for {req_email}"
        )

    try:
        meetings = fetch_user_meetings(user_email=payload.user_email, scope=payload.scope)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
    return {
        "email": payload.user_email,
        "scope": payload.scope,
        "count": len(meetings),
        "meetings": meetings,
    }

@router.post("/schedule-meeting")
def schedule_user_meeting(payload: ScheduleMeetingRequest, user: dict = Depends(get_current_user)):
    """
    API route to schedule a Teams meeting.
    """
    email = (user.get("email") or "").lower()
    req_email = (payload.organizer_email or "").lower()
    
    if email != req_email and "admin" not in user.get("roles", []):
         raise HTTPException(
            status_code=403,
            detail=f"User not Authorised to schedule meetings for {req_email}"
        )

    try:
        meeting = schedule_meeting(
            payload.organizer_email,
            payload.client_emails,
            payload.subject,
            payload.description,
            payload.start_time,
            payload.end_time,
            payload.category,
            payload.agenda
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
  
    return {
        "organizer": payload.organizer_email,
        "client": payload.client_emails,
        "subject": payload.subject,
        "start": payload.start_time,
        "end": payload.end_time,
        "joinUrl": meeting.get("onlineMeeting", {}).get("joinUrl"),
        "meetingId": meeting.get("id"),
    }

@router.patch("/reschedule-meeting")
def reschedule_user_meeting(payload: UpdateMeetingRequest, user: dict = Depends(get_current_user)):
    """
    API route to reschedule (update) an existing Teams meeting.
    """
    email = (user.get("email") or "").lower()
    req_email = (payload.organizer_email or "").lower()
    
    if email != req_email and "admin" not in user.get("roles", []):
         raise HTTPException(
            status_code=403,
            detail=f"User not Authorised to update meetings for {req_email}"
        )

    try:
        updated_meeting = update_meeting(
            payload.organizer_email,
            payload.meeting_id,
            payload.subject,
            payload.description,
            payload.start_time,
            payload.end_time,
            payload.agenda
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
  
    return updated_meeting

@router.post("/cancel-meeting")
def cancel_user_meeting(payload: CancelMeetingRequest, user: dict = Depends(get_current_user)):
    """
    API route to cancel a scheduled Teams meeting.
    """
    email = (user.get("email") or "").lower()
    req_email = (payload.organizer_email or "").lower()
    
    if email != req_email and "admin" not in user.get("roles", []):
         raise HTTPException(
            status_code=403,
            detail=f"User not Authorised to cancel meetings for {req_email}"
        )

    try:
        result = cancel_meeting(payload.organizer_email, payload.meeting_id, payload.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
  
    return result
