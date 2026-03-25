# projects/services.py
import asyncio
import logging
from typing import List, Dict, Any, Optional
import mimetypes
from fastapi import HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from config import settings
from .db import registered_clients_col
from .models import ProjectSummary, ProjectDetailsOut, DashboardOverview
from .enums import TaskPriority
from datetime import datetime
from dynamics.services import get_access_token, fetch_dynamics, get_onedrive_access_token, fetch_onedrive_file_content_by_name

logger = logging.getLogger("projects.service")
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


async def get_projects(token: Optional[str] = None, filter_expr: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch all projects (optionally filtered) from Business Central projectApiPage.

    Args:
      token: optional pre-acquired Bearer token (if None, function obtains one).
      filter_expr: optional OData $filter expression (e.g. "status eq 'Open'").

    Returns:
      List of project dicts returned by Dynamics (each item is raw JSON 'value' entry).
    """
    if token is None:
        token = await get_access_token()

    results: List[Dict[str, Any]] = []
    next_url: Optional[str] = None

    try:
        while True:
            # fetch first page or nextLink
            if next_url:
                data = await fetch_dynamics(next_url, token)  # pass full URL
            else:
                data = await fetch_dynamics("projectApiPage", token, filter_expr)

            # add current page results
            if isinstance(data, dict) and "value" in data:
                results.extend(data["value"])
            elif isinstance(data, list):
                results.extend(data)

            # check pagination
            next_url = data.get("@odata.nextLink") if isinstance(data, dict) else None
            if not next_url:
                break

        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to fetch projects from Dynamics: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch projects from Dynamics"
        )


async def get_project_by_no(project_no: str, token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch a single project by its 'no' field (project id).

    Args:
      project_no: the Business Central project id (e.g. 'PR00110' or 'PP-01').
      token: optional Bearer token; if omitted the function will request one.

    Returns:
      The first matching project dict, or None if not found.
    """
    if token is None:
        token = await get_access_token()

    # OData filter expression - ensure project_no is quoted
    filter_expr = f"no eq '{project_no}'"

    try:
        items = await fetch_dynamics("projectApiPage", token, filter_expr)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching project %s from Dynamics: %s", project_no, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch project from Dynamics")

    if not items:
        return None

    # return first match
    return items[0]



async def fetch_client_projects_by_email(client_email: str, include_first_details: bool = True) -> Dict[str, Any]:
    """
    Given a client email:
      1. Find the registered client in Mongo.
      2. Fetch projects from Dynamics by client_id (billToCustomerNo).
      3. Optionally fetch full details for the first project to save a frontend round-trip.
      4. Return summary counts and project list.
    """
    # 1) find registered client
    reg_doc = await registered_clients_col.find_one({"client_email": client_email})
    if not reg_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        
    client_id = reg_doc.get("client_id")
    client_name = reg_doc.get("client_name")
    if not client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registered client has no client_id")

    # 2) fetch projects
    filter_expr = f"billToCustomerNo eq '{client_id}'"
    try:
        projects: List[Dict[str, Any]] = await get_projects(token=None, filter_expr=filter_expr)
    except Exception as e:
        logger.exception("Error fetching projects for client %s: %s", client_id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch projects from Dynamics")

    # 3) compute counts and key-value list
    totalOverallProjectValue = 0.0
    kv_list = []
    ongoing = 0
    completed = 0

    for p in projects:
        pid = p.get("no") or ""
        name = p.get("description") or ""
        status_val = p.get("status") or ""
        val = float(p.get("overallProjectValue") or 0.0)
        
        if status_val.strip().lower() == "open": ongoing += 1
        elif status_val.strip().lower() == "completed": completed += 1

        kv_list.append({
            "project_id": str(pid),
            "project_name": str(name),
            "sector": str(p.get("sector") or ""),
            "clientType": str(p.get("clientType") or ""),
            "status": str(status_val)
        })
        totalOverallProjectValue += val

    result = {
        "client_id": client_id,
        "client_name": client_name,
        "total_projects": len(projects),
        "ongoing_projects": ongoing,
        "completed_projects": completed,
        "totalOverallProjectValue": totalOverallProjectValue,
        "projects": kv_list,
        "initial_project_details": None
    }

    # 4) Fetch details for the first project if requested
    if include_first_details and kv_list:
        first_project_id = kv_list[0]["project_id"]
        try:
            # We already have a token if we fetched projects, but get_project_dashboard_details will handle it
            result["initial_project_details"] = await get_project_dashboard_details(first_project_id)
        except Exception as e:
            logger.warning(f"Failed to pre-fetch details for first project {first_project_id}: {e}")

    return result


def _parse_date(d: Optional[str]):
    if not d:
        return None
    try:
        return datetime.fromisoformat(d).date()
    except Exception:
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            return None


async def get_project_dashboard_details(project_no: str, token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch full project details for dashboard, including members & phases.
    Optimized with parallel fetches and batched financial lookups.
    """
    if token is None:
        token = await get_access_token()

    # 1. Parallelize core fetches: Project info, Team members, and Phases
    project_filter = f"no eq '{project_no}'"
    members_filter = f"projectNo eq '{project_no}'"
    phases_filter = f"jobNo eq '{project_no}' and jobTaskType eq 'Posting'"

    try:
        logger.info(f"Parallel fetching core data for project {project_no}...")
        results = await asyncio.gather(
            fetch_dynamics("projectApiPage", token, project_filter),
            fetch_dynamics("projectBidTeamMemberApiPage", token, members_filter),
            fetch_dynamics("projectTaskApiPage", token, phases_filter),
            return_exceptions=True
        )
        
        # Handle potential errors in parallel tasks
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Parallel fetch {idx} failed: {res}")
                if idx == 0: # Project info is critical
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch project info")
        
        project_items, member_items, phase_items = results
        if isinstance(project_items, Exception): raise project_items
        if isinstance(member_items, Exception): member_items = []
        if isinstance(phase_items, Exception): phase_items = []

    except Exception as e:
        logger.exception("Error in parallel fetch for project %s: %s", project_no, e)
        if isinstance(e, HTTPException): raise
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch project data")

    if not project_items:
        return None

    project = project_items[0]
    project_data = {
        "projectNo": project.get("no"),
        "description": project.get("description"),
        "startingDate": project.get("startingDate"),
        "status": project.get("status"),
        "sector": project.get("sector"),
        "clientType": project.get("clientType"),
        "projectManagerPrimus": project.get("projectManagerPrimus"),
        "overallProjectValue": project.get("overallProjectValue", 0.0),
        "members": [{"memberID": m.get("memberID"), "memberName": m.get("memberName")} for m in member_items]
    }

    # 2. Optimized Financial Processing: Batch fetch ledger entries and invoices
    # We fetch ALL ledger entries for this project in one go
    ledger_items = []
    invoices_map = {} # doc_no -> invoice_header_dict
    
    try:
        ledger_filter = f"jobNo eq '{project_no}'"
        ledger_items = await fetch_dynamics("jobLedgerEntryPageApi", token, ledger_filter)
        
        # Extract unique document numbers across all ledger entries for batch invoice fetch
        doc_numbers = {li.get("documentNo") for li in ledger_items if li.get("documentNo")}
        
        if doc_numbers:
            # Batch invoice fetching: OData filter for multiple IDs
            # Note: We batch these to avoid extremely long URL strings (limit ~2000 chars)
            doc_list = list(doc_numbers)
            batch_size = 40 
            for i in range(0, len(doc_list), batch_size):
                batch = doc_list[i : i + batch_size]
                inv_filter = " or ".join([f"no eq '{d}'" for d in batch])
                batch_inv_items = await fetch_dynamics("salesInvoiceHeaderPageApi", token, inv_filter)
                for inv in batch_inv_items:
                    invoices_map[inv.get("no")] = inv

    except Exception as e:
        logger.exception("Error in batched financial lookups for project %s: %s", project_no, e)
        # Continue with best-effort (zeroed) financial data

    # 3. Process Phases in-memory
    today = datetime.now().date()
    phases = []
    completed_count = 0
    total_actual_amount = 0.0
    total_remaining_amount = 0.0
    total_completed_amount = 0.0

    # Group ledger entries by jobTaskNo for efficient lookup
    ledger_by_task = {}
    for li in ledger_items:
        t_no = li.get("jobTaskNo")
        if t_no not in ledger_by_task:
            ledger_by_task[t_no] = []
        ledger_by_task[t_no].append(li)

    for p in phase_items:
        start_raw = p.get("startDate")
        end_raw = p.get("endDate")
        task_no = p.get("jobTaskNo")
        actual_amt = float(p.get("actualBillingAmount") or 0.0)
        
        start_dt = _parse_date(start_raw)
        end_dt = _parse_date(end_raw)

        # Status logic
        status = "pending"
        if start_dt and end_dt:
            if end_dt < today: status = "completed"
            elif start_dt <= today <= end_dt: status = "ongoing"
        elif end_dt and end_dt < today:
            status = "completed"

        if status == "completed":
            completed_count += 1

        # Financial logic using batched data
        phase_remaining = 0.0
        if task_no and task_no in ledger_by_task:
            task_ledgers = ledger_by_task[task_no]
            task_docs = {li.get("documentNo") for li in task_ledgers if li.get("documentNo")}
            for d_no in task_docs:
                inv = invoices_map.get(d_no)
                if inv:
                    phase_remaining += float(inv.get("remainingAmount") or 0.0)

        phase_completed_amount = max(0.0, actual_amt - phase_remaining)
        
        total_actual_amount += actual_amt
        total_remaining_amount += phase_remaining
        total_completed_amount += phase_completed_amount

        phases.append({
            "phaseName": p.get("description"),
            "startDate": start_raw,
            "endDate": end_raw,
            "status": status,
            "actualBillingAmount": actual_amt,
            "remainingAmount": phase_remaining,
            "completedAmount": phase_completed_amount
        })

    # 4. Final Aggregations
    total_phases = len(phases)
    progress_percent = round((completed_count / total_phases) * 100, 2) if total_phases > 0 else 0.0
    
    denom = total_actual_amount if total_actual_amount > 0 else (project_data.get("overallProjectValue") or 0.0)
    payment_completed_percent = 0.0
    payment_pending_percent = 0.0
    if denom > 0:
        payment_completed_percent = max(0.0, min(100.0, round((total_completed_amount / denom) * 100, 2)))
        payment_pending_percent = max(0.0, min(100.0, round((total_remaining_amount / denom) * 100, 2)))

    project_data.update({
        "phases": phases,
        "progress_percent": progress_percent,
        "total_actual_amount": total_actual_amount,
        "total_remaining_amount": total_remaining_amount,
        "payment_completed_percent": payment_completed_percent,
        "payment_pending_percent": payment_pending_percent
    })

    return project_data


class TeamMemberOut(BaseModel):
    member_id: Optional[str] = None           # memberID from projectBidTeamMemberApiPage
    member_name: Optional[str] = None         # memberName from projectBidTeamMemberApiPage
    user_id: Optional[str] = None             # userID from userSetupPageApi (if different)
    resource: Optional[str] = None            # resource code (e.g., "LINA")
    name: Optional[str] = None                # resource.name (Lina Townsend)
    type: Optional[str] = None                # resource.type (Person/Other)
    address: Optional[str] = None             # resource.address
    job_title: Optional[str] = None           # resource.jobTitle
    post_code: Optional[str] = None           # resource.postCode
    position: Optional[str] = None            # resource.position (e.g., "Delivery MD")
    error: Optional[str] = None               # populated if we failed to fetch details for this member


async def fetch_project_team_members(project_no: str, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Given a project_no:
      1) fetch members from projectBidTeamMemberApiPage (memberID, memberName)
      2) for each memberID fetch userSetupPageApi to obtain 'resource'
      3) for each resource code fetch resourcePageApi to get resource details
    Returns a list of dicts matching TeamMemberOut fields.
    """
    # 1) ensure token
    if not token:
        token = await get_access_token()

    # 2) get project bid team members
    try:
        members_filter = f"projectNo eq '{project_no}'"
        member_items = await fetch_dynamics("projectBidTeamMemberApiPage", token, members_filter)
    except Exception as e:
        logger.exception("Error fetching members for project %s: %s", project_no, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch project members")

    # member_items expected to be a list of dicts with at least memberID and memberName
    results: List[Dict[str, Any]] = []

    # helper per-member coroutine
    async def _resolve_member(m: Dict[str, Any]) -> Dict[str, Any]:
        member_id = m.get("memberID") or m.get("memberId") or m.get("MemberID")
        member_name = m.get("memberName") or m.get("member_name") or m.get("MemberName")

        out = {
            "member_id": member_id,
            "member_name": member_name,
            "user_id": None,
            "resource": None,
            "name": None,
            "type": None,
            "address": None,
            "job_title": None,
            "post_code": None,
            "position": None,
            "error": None
        }

        if not member_id:
            out["error"] = "no memberID in project team member entry"
            return out

        # 3) fetch userSetupPageApi by userID == member_id
        try:
            user_filter = f"userID eq '{member_id}'"
            user_items = await fetch_dynamics("userSetupPageApi", token, user_filter)
            # fetch_dynamics may return [] or a list; take first if present
            user = user_items[0] if user_items else None
            if not user:
                out["error"] = f"userSetup not found for userID={member_id}"
                return out

            out["user_id"] = user.get("userID")
            resource_code = user.get("resource")
            out["resource"] = resource_code

            if not resource_code:
                out["error"] = f"resource missing in userSetup for userID={member_id}"
                return out

        except Exception as e:
            logger.exception("Error fetching userSetup for member %s: %s", member_id, e)
            out["error"] = f"failed to fetch userSetup: {e}"
            return out

        # 4) fetch resource page for resource code
        try:
            resource_filter = f"no eq '{resource_code}'"
            resource_items = await fetch_dynamics("resourcePageApi", token, resource_filter)
            resource = resource_items[0] if resource_items else None
            if not resource:
                out["error"] = f"resource record not found for resource={resource_code}"
                return out
            
            street = (resource.get("address") or "").strip()
            city = (resource.get("city") or "").strip()
            if street and city:
                combined_address = f"{street}, {city}"
            elif street:
                combined_address = street
            elif city:
                combined_address = city
            else:
                combined_address = None

            # map/normalize resource fields into output shape
            out.update({
                "name": resource.get("name"),
                "type": resource.get("type"),
                "address": combined_address,
                "job_title": resource.get("jobTitle") or resource.get("job_title"),
                "post_code": resource.get("postCode") or resource.get("post_code"),
                "position": resource.get("position"),
            })
            return out
        except Exception as e:
            logger.exception("Error fetching resource %s for member %s: %s", resource_code, member_id, e)
            out["error"] = f"failed to fetch resource: {e}"
            return out

    # schedule all member resolution coros concurrently (bounded concurrency if needed)
    tasks = [asyncio.create_task(_resolve_member(m)) for m in member_items]
    if tasks:
        resolved = await asyncio.gather(*tasks, return_exceptions=False)
        results.extend(resolved)

    return results


async def get_document_attachments_for_project(project_no: str, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return documentAttachmentApiPage entries for a given project_no.
    Each returned dict will include a computed `file_name` per formula:
      FileName := DocAttach."File Name" + '_' + Format(DocAttach.ID) + '_' + DocAttach."No." + '.' + DocAttach."File Extension";
    """
    if token is None:
        token = await get_access_token()

    filter_expr = f"no eq '{project_no}'"
    try:
        items = await fetch_dynamics("documentAttachmentApiPage", token, filter_expr)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to fetch document attachments for project %s: %s", project_no, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch document attachments")

    results: List[Dict[str, Any]] = []
    for row in items:
        file_name_raw = row.get("fileName") or ""
        file_id = row.get("id")
        project_no = row.get("no") or ""
        file_ext = row.get("fileExtension") or ""

        # ensure id exists
        if file_id is None:
            # skip or include with error marker
            row_copy = dict(row)
            row_copy["file_name"] = None
            row_copy["error"] = "missing id"
            results.append(row_copy)
            continue

        # build file name: <FileName>_<ID>_<No>.<FileExtension>
        # make sure we don't include extra dots if extension missing
        filename_base = f"{file_name_raw}_{file_id}_{project_no}"
        if file_ext:
            constructed = f"{filename_base}.{file_ext}"
        else:
            constructed = filename_base

        row_copy = dict(row)
        row_copy["file_name"] = constructed
        results.append(row_copy)

    return results


async def get_attachment_and_stream(file_name: str) -> StreamingResponse:
    """
    High-level helper: find the attachment row by id for project_no,
    build file name, fetch bytes from OneDrive and return a StreamingResponse.
    """
    print("Requested file name:", file_name)
    # 1) find the attachment row
    one_drive_user = settings.onedrive_user_email
    
    # 2) fetch oneDrive file content
    graph_token = await get_onedrive_access_token()
    print("Obtained Graph token")
    resp = await fetch_onedrive_file_content_by_name(one_drive_user, file_name, graph_token=graph_token)

    # 3) create streaming response using resp.aiter_bytes()
    # determine content type from Graph response or guess by extension
    content_type = resp.headers.get("content-type") or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    # streaming iterator
    async def iter_bytes():
        async for chunk in resp.aiter_bytes():
            yield chunk

    # set filename for download
    download_name = file_name

    return StreamingResponse(iter_bytes(), media_type=content_type, headers={
        "Content-Disposition": f'attachment; filename="{download_name}"'
    })


