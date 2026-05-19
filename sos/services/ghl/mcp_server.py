"""
GHL MCP Server - SOS Standard

GoHighLevel integration following SOS software patterns:
- FastAPI endpoints
- 16D-aware contact/learner management
- Course enrollment via opportunities
- Witness-verified communications

Connects to existing GHL app-level auth.
"""

import os
import json
import logging
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sos.kernel import Config
from sos.kernel.physics import CoherencePhysics
from sos.observability.logging import get_logger

log = get_logger("ghl_mcp")

app = FastAPI(title="SOS GHL MCP Server", version="1.0.0")

# GHL API Configuration
GHL_API_BASE = "https://services.leadconnectorhq.com"

def get_ghl_headers() -> Dict[str, str]:
    """Get GHL API headers from environment."""
    api_key = os.getenv("GHL_ACCESS_TOKEN") or os.getenv("GHL_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GHL_ACCESS_TOKEN not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Version": "2021-07-28"
    }

def get_location_id() -> str:
    """Get GHL location ID from environment."""
    location_id = os.getenv("GHL_LOCATION_ID")
    if not location_id:
        raise HTTPException(status_code=500, detail="GHL_LOCATION_ID not configured")
    return location_id

# ============================================
# Pydantic Models (SOS Standard)
# ============================================

class UV16D(BaseModel):
    """16D Universal Vector for a learner/rider."""
    # Inner Octave
    p: float = Field(0.5, ge=0, le=1, description="Phase/Identity")
    e: float = Field(0.5, ge=0, le=1, description="Existence/Worlds")
    mu: float = Field(0.5, ge=0, le=1, description="Cognition/Masks")
    v: float = Field(0.5, ge=0, le=1, description="Energy/Vitality")
    n: float = Field(0.5, ge=0, le=1, description="Narrative/Story")
    delta: float = Field(0.5, ge=0, le=1, description="Trajectory/Motion")
    r: float = Field(0.5, ge=0, le=1, description="Relationality/Bonds")
    phi: float = Field(0.5, ge=0, le=1, description="Field Awareness")
    # Derived
    coherence: float = Field(0.5, ge=0, le=1, description="Overall Coherence")

class CreateLearnerRequest(BaseModel):
    """Create a new learner (GHL Contact + 16D profile)."""
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    telegram: Optional[str] = None
    uv: Optional[UV16D] = None
    tags: List[str] = Field(default_factory=lambda: ["learner", "sos"])
    source: str = "SOS Platform"

class EnrollCourseRequest(BaseModel):
    """Enroll learner in a course (GHL Opportunity)."""
    contact_id: str
    course_id: str
    course_name: str
    pipeline_id: Optional[str] = None
    stage_id: Optional[str] = None
    monetary_value: float = 0.0

class SendMessageRequest(BaseModel):
    """Send message to learner (SMS/Email with witness tracking)."""
    contact_id: str
    message_type: str = Field("SMS", pattern="^(SMS|Email)$")
    body: str
    subject: Optional[str] = None  # For email
    track_witness: bool = True  # Track response latency for coherence

class WitnessEventRequest(BaseModel):
    """Record a witness event (learner verification)."""
    contact_id: str
    event_type: str  # lesson_complete, quiz_pass, course_finish
    latency_ms: float  # Response time in milliseconds
    metadata: Dict[str, Any] = Field(default_factory=dict)

# ============================================
# Endpoints
# ============================================

@app.get("/health")
def health():
    """Health check."""
    return {
        "status": "ok",
        "service": "ghl_mcp",
        "standard": "sos",
        "location_id": os.getenv("GHL_LOCATION_ID", "not_set")
    }

@app.post("/learners/create")
async def create_learner(req: CreateLearnerRequest):
    """
    Create a new learner in GHL with 16D profile.
    Stores UV in custom fields.
    """
    headers = get_ghl_headers()
    location_id = get_location_id()

    # Build custom fields for 16D storage
    custom_fields = []
    if req.uv:
        custom_fields.append({"key": "uv_16d", "value": req.uv.model_dump_json()})
        custom_fields.append({"key": "coherence", "value": str(req.uv.coherence)})

    if req.telegram:
        custom_fields.append({"key": "telegram", "value": req.telegram})

    payload = {
        "locationId": location_id,
        "email": req.email,
        "firstName": req.first_name,
        "lastName": req.last_name,
        "phone": req.phone,
        "tags": req.tags,
        "source": req.source,
        "customFields": custom_fields
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{GHL_API_BASE}/contacts/",
                headers=headers,
                json=payload
            )
            resp.raise_for_status()
            data = resp.json()

            log.info(f"Created learner: {req.email} (ID: {data.get('contact', {}).get('id')})")

            return {
                "success": True,
                "contact_id": data.get("contact", {}).get("id"),
                "uv_stored": req.uv is not None,
                "result": data
            }
        except httpx.HTTPStatusError as e:
            log.error(f"GHL API error: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

@app.post("/learners/enroll")
async def enroll_course(req: EnrollCourseRequest):
    """
    Enroll learner in a course by creating a GHL Opportunity.
    """
    headers = get_ghl_headers()
    location_id = get_location_id()

    payload = {
        "locationId": location_id,
        "contactId": req.contact_id,
        "name": req.course_name,
        "pipelineId": req.pipeline_id or os.getenv("GHL_COURSES_PIPELINE_ID"),
        "pipelineStageId": req.stage_id or os.getenv("GHL_COURSES_STAGE_ID"),
        "monetaryValue": req.monetary_value,
        "status": "open",
        "source": "SOS Course Enrollment"
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{GHL_API_BASE}/opportunities/",
                headers=headers,
                json=payload
            )
            resp.raise_for_status()
            data = resp.json()

            log.info(f"Enrolled contact {req.contact_id} in course: {req.course_name}")

            return {
                "success": True,
                "opportunity_id": data.get("opportunity", {}).get("id"),
                "course": req.course_name,
                "result": data
            }
        except httpx.HTTPStatusError as e:
            log.error(f"GHL enrollment error: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

@app.post("/messages/send")
async def send_message(req: SendMessageRequest):
    """
    Send SMS or Email to learner with optional witness tracking.
    """
    headers = get_ghl_headers()
    location_id = get_location_id()

    payload = {
        "type": req.message_type,
        "locationId": location_id,
        "contactId": req.contact_id,
        "message": req.body
    }

    if req.message_type == "Email" and req.subject:
        payload["subject"] = req.subject

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{GHL_API_BASE}/conversations/messages",
                headers=headers,
                json=payload
            )
            resp.raise_for_status()
            data = resp.json()

            message_id = data.get("messageId") or data.get("id")

            log.info(f"Sent {req.message_type} to {req.contact_id}: {message_id}")

            result = {
                "success": True,
                "message_id": message_id,
                "type": req.message_type,
                "result": data
            }

            # Track witness event if enabled
            if req.track_witness:
                result["witness_tracking"] = {
                    "enabled": True,
                    "sent_at": datetime.now().isoformat(),
                    "awaiting_response": True
                }

            return result

        except httpx.HTTPStatusError as e:
            log.error(f"GHL message error: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

@app.post("/witness/record")
async def record_witness_event(req: WitnessEventRequest):
    """
    Record a witness event and calculate coherence using Physics of Will (RC-7).
    Updates learner's 16D profile with new coherence score.
    """
    physics = CoherencePhysics()

    # Calculate Will magnitude from latency
    omega = physics.calculate_will_magnitude(req.latency_ms)

    # Compute full collapse energy
    result = physics.compute_collapse_energy(
        vote=1,  # Positive witness (completed lesson/quiz)
        latency_ms=req.latency_ms,
        agent_coherence=0.8  # System coherence
    )

    log.info(f"Witness event for {req.contact_id}: Ω={omega:.4f}, ΔC={result['delta_c']:.6f}")

    # Update contact's coherence in GHL (via custom field)
    headers = get_ghl_headers()

    async with httpx.AsyncClient() as client:
        try:
            # Get current contact to retrieve existing UV
            get_resp = await client.get(
                f"{GHL_API_BASE}/contacts/{req.contact_id}",
                headers=headers
            )

            if get_resp.status_code == 200:
                contact = get_resp.json().get("contact", {})

                # Update coherence score
                custom_fields = [
                    {"key": "coherence", "value": str(round(omega, 4))},
                    {"key": "last_witness", "value": datetime.now().isoformat()},
                    {"key": "witness_type", "value": req.event_type}
                ]

                await client.put(
                    f"{GHL_API_BASE}/contacts/{req.contact_id}",
                    headers=headers,
                    json={"customFields": custom_fields}
                )

        except Exception as e:
            log.warning(f"Failed to update contact coherence: {e}")

    return {
        "success": True,
        "contact_id": req.contact_id,
        "event_type": req.event_type,
        "physics": {
            "omega": omega,
            "delta_c": result["delta_c"],
            "latency_ms": req.latency_ms,
            "interpretation": "fast" if omega > 0.8 else "moderate" if omega > 0.5 else "hesitant"
        },
        "mind_earned": round(omega * 10, 2)  # Convert to $MIND tokens
    }

@app.get("/learners/{contact_id}/uv")
async def get_learner_uv(contact_id: str):
    """
    Get learner's 16D Universal Vector profile.
    """
    headers = get_ghl_headers()

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{GHL_API_BASE}/contacts/{contact_id}",
                headers=headers
            )
            resp.raise_for_status()
            contact = resp.json().get("contact", {})

            # Extract UV from custom fields
            custom_fields = contact.get("customFields", [])
            uv_field = next((f for f in custom_fields if f.get("key") == "uv_16d"), None)
            coherence_field = next((f for f in custom_fields if f.get("key") == "coherence"), None)

            if uv_field and uv_field.get("value"):
                try:
                    uv = json.loads(uv_field["value"])
                except:
                    uv = None
            else:
                uv = None

            return {
                "contact_id": contact_id,
                "name": f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip(),
                "email": contact.get("email"),
                "uv": uv,
                "coherence": float(coherence_field["value"]) if coherence_field else None,
                "has_16d_profile": uv is not None
            }

        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

@app.get("/courses/pipeline")
async def get_courses_pipeline():
    """
    Get available courses from GHL pipeline.
    """
    headers = get_ghl_headers()
    location_id = get_location_id()
    pipeline_id = os.getenv("GHL_COURSES_PIPELINE_ID")

    if not pipeline_id:
        return {"pipelines": [], "note": "GHL_COURSES_PIPELINE_ID not configured"}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{GHL_API_BASE}/opportunities/pipelines",
                headers=headers,
                params={"locationId": location_id}
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("GHL_MCP_PORT", "8850"))
    uvicorn.run(app, host="0.0.0.0", port=port)
