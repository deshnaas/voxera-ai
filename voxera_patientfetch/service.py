"""Patient Intelligence API  (FastAPI).   Run:

    .venv312\\Scripts\\python.exe -m uvicorn voxera_patientfetch.service:app --port 8100

Why a server: OCR is Python and the service-role key must never reach a browser.
The dashboard sends the signed-in staff member's Supabase JWT; this API

    JWT -> Supabase auth -> hospital_users(facility, role) -> access policy
        -> (reason if external) -> audit row -> minimum-necessary sections

A patient ID alone never authorises anything. Every record open, question, upload
and verification is audited; external access is always audited.
"""

from __future__ import annotations

import os
import re
import time
import uuid as _uuid
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import access_policy as policy
from . import prescription_ai
from .identity import normalize_patient_id
from .patient_ai import PatientIntelligenceAI, age_from_dob, split_list
from .record_qa import classify_intent, staff_view
from .repository import TRANSIENT_RX, RepoError, SupabaseRepo

BUCKET = "patient-documents"
MAGIC = {"pdf": (b"%PDF",), "png": (b"\x89PNG",), "jpg": (b"\xff\xd8",), "jpeg": (b"\xff\xd8",)}
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}

app = FastAPI(title="Voxera Patient Intelligence", version="1.0.0", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("VOXERA_API_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()],
    allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

@app.exception_handler(RepoError)
async def _repo_error(_request, _exc):
    """A database hiccup must be a clean 503 (with CORS headers) so the dashboard can say 'try again',
    not a bare 500 that the browser reports as 'service not running'. Details stay in the server log."""
    print(f"[PATIENT_AI] database error: {str(_exc)[:120]}")
    return JSONResponse({"detail": "Records are temporarily unavailable. Please try again."}, status_code=503)


_repo: Optional[SupabaseRepo] = None
_svc: Optional[PatientIntelligenceAI] = None
_token_cache: dict = {}            # jwt -> (expires, user_id)
_audit_seen: dict = {}             # (user, patient, action) -> ts   (dedupe home-facility polling)


def get_svc() -> PatientIntelligenceAI:
    global _repo, _svc
    if _svc is None:
        _repo = SupabaseRepo()
        _svc = PatientIntelligenceAI(_repo, cache_ttl=30.0)
    return _svc


class Staff:
    def __init__(self, user_id: str, facility_id: str, role: str, ip: Optional[str] = None):
        self.user_id, self.facility_id, self.role, self.ip = user_id, facility_id, role, ip


def current_staff(authorization: Optional[str] = Header(None)) -> Staff:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in required.")
    token = authorization[7:].strip()
    svc = get_svc()
    hit = _token_cache.get(token)
    if hit and hit[0] > time.time():
        user_id = hit[1]
    else:
        user_id = None
        for attempt in range(2):
            try:
                user_id = svc.repo.sb.auth.get_user(token).user.id
                break
            except Exception as e:                                  # noqa: BLE001
                if attempt == 0 and TRANSIENT_RX.search(str(e)):
                    time.sleep(0.3)                                 # dropped connection, not a bad token: retry once
                    continue
                raise HTTPException(401, "Session expired. Please sign in again.") from None
        _token_cache[token] = (time.time() + 60, user_id)
        if len(_token_cache) > 500:
            _token_cache.clear()
    try:
        hu = svc.repo.get_hospital_user(user_id)
    except RepoError:
        raise HTTPException(503, "Directory unavailable.") from None
    if not hu:
        raise HTTPException(403, "This account is not linked to a hospital.")
    return Staff(user_id, hu["facility_id"], hu.get("role") or "", None)


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def resolve_patient(svc, pid: str) -> dict:
    """Accept VX-000123 (any spoken/typed form) or the UUID. 404 never says which part failed."""
    try:
        canon = normalize_patient_id(pid)
        rows = svc.repo.find_patient(patient_id=canon) if canon else []
        if not rows:
            try:
                rows = svc.repo.find_patient(uuid=str(_uuid.UUID(pid)))
            except ValueError:
                rows = []
    except RepoError:
        raise HTTPException(503, "Records are temporarily unavailable.") from None
    if len(rows) != 1:
        raise HTTPException(404, "Patient not found.")
    return rows[0]


def authorise(svc, staff: Staff, patient: dict, reason: Optional[str], reason_text: Optional[str], action: str,
              audit: bool = True):
    facilities = svc.repo.get_patient_facilities(patient["id"])
    d = policy.decide(staff.role, staff.facility_id, facilities, reason, reason_text)
    if audit:
        _audit(svc, staff, patient["id"], d, reason, reason_text, action if d.allowed else "denied")
    if not d.allowed:
        detail = {"code": d.deny_reason, "relationship": d.relationship, "requires_reason": d.requires_reason,
                  "reasons": list(policy.REASONS), "label": d.label}
        raise HTTPException(403 if d.deny_reason != "reason_required" else 428, detail)
    return d


def _audit(svc, staff, patient_uuid, decision, reason, reason_text, action, meta=None):
    key = (staff.user_id, patient_uuid, action)
    now = time.time()
    # only routine reads are de-duplicated; uploads, verifications and deletions are ALWAYS logged
    if (action == "open_record" and decision and decision.relationship == "home" and decision.allowed
            and now - _audit_seen.get(key, 0) < 300):
        return                                                     # home-facility polling: one row / 5 min
    _audit_seen[key] = now
    try:
        svc.audit_access(patient_uuid=patient_uuid, user_id=staff.user_id, facility_id=staff.facility_id,
                         role=staff.role, reason=reason, reason_text=reason_text, source="dashboard",
                         decision=decision, action=action, meta=meta)
    except RepoError:
        if decision and decision.relationship == "external":
            raise HTTPException(503, "Access could not be audited, so it was not granted.") from None


def _identity(patient: dict, relationship: str) -> dict:
    p = dict(patient)
    p["age"] = age_from_dob(patient.get("date_of_birth"))
    return policy.redact_identity(p, relationship)


def _strip_consult(c: dict, external: bool) -> dict:
    out = {k: c.get(k) for k in ("id", "kind", "date", "facility_id", "chief_complaint", "symptoms", "assessment",
                                 "risk_level", "guidance", "otc_guidance", "emergency", "outcome", "follow_up")}
    s = c.get("summary") or {}
    if not external:
        out["voice_signal"] = s.get("voice_signal")
        out["clinical_context"] = s.get("clinical_context")
        out["record_context"] = s.get("record_context")
    return out


# ------------------------------------------------------------------
# endpoints
# ------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "service": "voxera-patient-intelligence"}


@app.get("/api/patients/search")
def search(q: str = Query(..., min_length=2, max_length=64), staff: Staff = Depends(current_staff)):
    svc = get_svc()
    try:
        hits = svc.repo.search_patients(q, limit=8)
    except RepoError:
        raise HTTPException(503, "Search is temporarily unavailable.") from None
    role = policy.normalise_role(staff.role)
    results = []
    for p in hits:
        facilities = svc.repo.get_patient_facilities(p["id"])
        d = policy.decide(staff.role, staff.facility_id, facilities, reason="follow_up")   # relationship only
        row = dict(p, age=age_from_dob(p.get("date_of_birth")))
        summary = None
        if d.relationship == "home" and role and policy.MEDICATIONS in policy.HOME_POLICY.get(role, set()):
            try:
                summary = svc.get_patient_summary(p["id"])
            except RepoError:
                summary = None
        proj = policy.search_projection(row, staff.role, summary)
        proj["relationship"] = d.relationship
        proj["access"] = "direct" if d.relationship == "home" else "reason_required"
        if d.relationship == "external":
            proj["facility"] = None                                 # facility names are not disclosed in search
            _audit(svc, staff, p["id"], d, None, None, "search", {"q_len": len(q)})
        results.append(proj)
    return {"results": results, "role": role}


class OpenBody(BaseModel):
    reason: Optional[str] = None
    reason_text: Optional[str] = None


@app.post("/api/patients/{pid}/record")
def open_record(pid: str, body: OpenBody, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, body.reason, body.reason_text, "open_record")
    sec = d.sections
    rec: dict = {"relationship": d.relationship, "label": d.label, "sections": sorted(sec),
                 "role": policy.normalise_role(staff.role), "patient": _identity(patient, d.relationship)}
    ext = d.relationship == "external"
    try:
        ctx = svc.build_clinical_context(patient["id"])
        if policy.ALLERGIES in sec:
            rec["allergies"] = {"allergies": ctx.allergies, "blood_group": patient.get("blood_group")}
        if policy.MEDICATIONS in sec:
            rec["medications"] = svc.get_medications(patient["id"])
        if policy.PRESCRIPTIONS in sec:
            rec["prescriptions"] = svc.get_prescriptions(patient["id"])
            if not ext:      # unverified OCR candidates are reviewed only at the patient's own facility
                rec["prescription_items"] = [i for i in svc.repo.get_prescription_items(patient["id"])
                                             if i.get("verification_status") in ("pending", "needs_review")]
        if policy.CONSULTATIONS in sec:
            rec["consultations"] = [_strip_consult(c, ext) for c in ctx.previous_consultations]
            rec["conditions"] = ctx.conditions
        if policy.REFERRALS in sec:
            rec["referrals"] = ctx.recent_referrals
        if policy.APPOINTMENTS in sec:
            rec["appointments"] = ctx.appointments
        if policy.DOCUMENTS in sec:
            rec["documents"] = svc.repo.get_patient_documents(patient["id"])
        if policy.AUDIT in sec:
            logs = svc.repo.get_access_logs(patient["id"], 50)
            names = svc.repo.get_facility_names({l.get("facility_id") for l in logs})
            rec["audit"] = [dict(l, facility_name=names.get(l.get("facility_id"))) for l in logs]
        rec["partial"] = ctx.partial
    except RepoError:
        raise HTTPException(503, "Records are temporarily unavailable.") from None
    return rec


@app.get("/api/patients/{pid}/medications")
def medications(pid: str, reason: Optional[str] = None, reason_text: Optional[str] = None, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, reason, reason_text, "open_record")
    if policy.MEDICATIONS not in d.sections:
        raise HTTPException(403, "Your role cannot view medications.")
    return {"relationship": d.relationship, "medications": svc.get_medications(patient["id"])}


@app.get("/api/patients/{pid}/prescriptions")
def prescriptions(pid: str, reason: Optional[str] = None, reason_text: Optional[str] = None, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, reason, reason_text, "open_record")
    if policy.PRESCRIPTIONS not in d.sections:
        raise HTTPException(403, "Your role cannot view prescriptions.")
    return {"relationship": d.relationship, "prescriptions": svc.get_prescriptions(patient["id"])}


@app.get("/api/patients/{pid}/consultations")
def consultations(pid: str, reason: Optional[str] = None, reason_text: Optional[str] = None, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, reason, reason_text, "open_record")
    if policy.CONSULTATIONS not in d.sections:
        raise HTTPException(403, "Your role cannot view consultations.")
    ext = d.relationship == "external"
    return {"relationship": d.relationship,
            "consultations": [_strip_consult(c, ext) for c in svc.get_previous_consultations(patient["id"])]}


class QuestionBody(BaseModel):
    question: str
    reason: Optional[str] = None
    reason_text: Optional[str] = None


@app.post("/api/patients/{pid}/record-question")
def record_question(pid: str, body: QuestionBody, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    if not body.question.strip() or len(body.question) > 400:
        raise HTTPException(422, "Ask a short question about the record.")
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, body.reason, body.reason_text, "ask_question")
    needed = {policy.MEDICATIONS, policy.CONSULTATIONS}
    if not (needed & d.sections):
        raise HTTPException(403, "Your role cannot query clinical records.")
    use_llm = os.getenv("VOXERA_RECORD_LLM", "0") == "1"
    ans = svc.answer_record_question(patient["id"], body.question, use_llm=use_llm,
                                     llm=_ollama if use_llm else None)
    data = ans.to_dict()
    data["answer"] = staff_view(data["answer"])
    # never reveal sources the caller's policy does not grant
    granted_types = {"medication": policy.MEDICATIONS, "consultation": policy.CONSULTATIONS, "referral": policy.REFERRALS,
                     "appointment": policy.APPOINTMENTS, "patient": policy.IDENTITY, "facility": policy.IDENTITY}
    if any(granted_types.get(s.get("type"), policy.IDENTITY) not in d.sections for s in data["sources"]):
        data.update(answer="That information is not available to your role at this facility.", sources=[], found=False)
    data["intent"] = classify_intent(body.question)["intent"]
    data["relationship"] = d.relationship
    data["ai_generated"] = True                                    # the UI must not present this as clinician-authored
    return data


@app.post("/api/patients/{pid}/prescriptions/upload")
async def upload_prescription(pid: str, file: UploadFile = File(...), staff: Staff = Depends(current_staff)):
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, None, None, "upload")
    if d.relationship != "home" or policy.normalise_role(staff.role) not in ("doctor", "nurse"):
        raise HTTPException(403, "Only a doctor or nurse of the patient's facility can upload prescriptions.")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "upload")[:80]
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    if ext not in MAGIC:
        raise HTTPException(415, "Upload a PDF, JPG or PNG file.")
    data = await file.read(prescription_ai.MAX_BYTES + 1)
    if len(data) > prescription_ai.MAX_BYTES:
        raise HTTPException(413, "File is too large (max 12 MB).")
    if not any(data.startswith(m) for m in MAGIC[ext]):
        raise HTTPException(415, "That file does not look like a valid " + ext.upper() + ".")

    storage_path = None
    warning = None
    try:
        storage_path = f"{patient['id']}/{_uuid.uuid4()}.{ext}"
        svc.repo.sb.storage.from_(BUCKET).upload(storage_path, data, {"content-type": MIME[ext]})
    except Exception:                                               # noqa: BLE001
        storage_path, warning = None, "The original file could not be stored (storage bucket missing?)."
    res = prescription_ai.process_document(svc, patient["id"], data, name, uploaded_by=staff.user_id,
                                           facility_id=staff.facility_id, storage_path=storage_path)
    if warning:
        res["warning"] = warning
    return res


class VerifyBody(BaseModel):
    action: str                       # confirm | reject
    edits: Optional[dict] = None


@app.post("/api/prescription-items/{item_id}/verify")
def verify_item(item_id: str, body: VerifyBody, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    item = svc.repo.get_prescription_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found.")
    patient = svc.repo.get_patient(item["patient_id"])
    d = authorise(svc, staff, patient, None, None, "verify")
    if d.relationship != "home":
        raise HTTPException(403, "Only the patient's own facility can verify medications.")
    try:
        if body.action == "confirm":
            med = svc.confirm_medication(item_id, user_id=staff.user_id, role=staff.role,
                                         facility_id=staff.facility_id, edits=body.edits)
            return {"ok": True, "medication": med}
        if body.action == "reject":
            svc.reject_medication(item_id, user_id=staff.user_id, role=staff.role)
            return {"ok": True}
    except PermissionError:
        raise HTTPException(403, "Only a doctor can confirm a medication.") from None
    except (LookupError, ValueError) as e:
        raise HTTPException(409, str(e)) from None
    raise HTTPException(422, "action must be confirm or reject")


@app.get("/api/patients/{pid}/documents/{doc_id}")
def document(pid: str, doc_id: str, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, None, None, "open_record")
    if policy.DOCUMENTS not in d.sections:
        raise HTTPException(403, "Your role cannot view documents.")
    doc = svc.repo.get_document(doc_id)
    if not doc or doc["patient_id"] != patient["id"]:
        raise HTTPException(404, "Document not found.")
    url = None
    if doc.get("storage_path"):
        try:
            r = svc.repo.sb.storage.from_(BUCKET).create_signed_url(doc["storage_path"], 300)
            url = r.get("signedURL") or r.get("signedUrl")
        except Exception:                                           # noqa: BLE001
            url = None
    items = [i for i in svc.repo.get_prescription_items(patient["id"]) if i.get("document_id") == doc_id]
    return {"document": {k: doc.get(k) for k in ("id", "filename", "document_type", "extracted_text", "ocr_metadata",
                                                 "ocr_status", "created_at", "uploaded_by", "facility_id")},
            "url": url, "items": items}


@app.get("/api/patients/{pid}/audit")
def audit_log(pid: str, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, None, None, "open_record")
    if policy.AUDIT not in d.sections:
        raise HTTPException(403, "Your role cannot view the access audit.")
    logs = svc.repo.get_access_logs(patient["id"], 100)
    names = svc.repo.get_facility_names({l.get("facility_id") for l in logs})
    return {"audit": [dict(l, facility_name=names.get(l.get("facility_id"))) for l in logs]}


def _home_only(d):
    if d.relationship != "home":
        raise HTTPException(403, "Only the patient's own facility can change their record.")


class VoidBody(BaseModel):
    origin: str                       # patient_medications | prescriptions
    reason: Optional[str] = None


@app.post("/api/patients/{pid}/medications/{med_id}/remove")
def remove_medication(pid: str, med_id: str, body: VoidBody, staff: Staff = Depends(current_staff)):
    """Remove a medication that was confirmed / prescribed by mistake (kept for audit, hidden everywhere)."""
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, None, None, "delete")
    _home_only(d)
    try:
        out = svc.void_medication(origin=body.origin, med_id=med_id, patient_uuid=patient["id"], role=staff.role)
    except PermissionError:
        raise HTTPException(403, "Only a doctor can remove a medication.") from None
    except LookupError:
        raise HTTPException(404, "Medication not found.") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True, **out}


@app.delete("/api/prescription-items/{item_id}")
def delete_item(item_id: str, staff: Staff = Depends(current_staff)):
    svc = get_svc()
    item = svc.repo.get_prescription_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found.")
    patient = svc.repo.get_patient(item["patient_id"])
    d = authorise(svc, staff, patient, None, None, "delete")
    _home_only(d)
    try:
        svc.delete_item(item_id, role=staff.role)
    except PermissionError:
        raise HTTPException(403, "You can't delete this.") from None
    except (LookupError, ValueError) as e:
        raise HTTPException(409, str(e)) from None
    return {"ok": True}


@app.delete("/api/patients/{pid}/documents/{doc_id}")
def delete_document(pid: str, doc_id: str, staff: Staff = Depends(current_staff)):
    """Delete an uploaded prescription: the file, its extracted candidates and (doctor only) any medicines
    confirmed from it are removed from the active record."""
    svc = get_svc()
    patient = resolve_patient(svc, pid)
    d = authorise(svc, staff, patient, None, None, "delete")
    _home_only(d)
    try:
        out = svc.delete_document(doc_id, patient_uuid=patient["id"], role=staff.role)
    except PermissionError as e:
        raise HTTPException(403, str(e) or "You can't delete this.") from None
    except LookupError:
        raise HTTPException(404, "Document not found.") from None
    return {"ok": True, **out}


class LogBody(BaseModel):
    patient_id: str
    reason: Optional[str] = None
    reason_text: Optional[str] = None
    action: str = "open_record"


@app.post("/api/patient-access-log")
def access_log(body: LogBody, staff: Staff = Depends(current_staff)):
    """Explicit log entry (e.g. the UI recording the reason a clinician gave). Decision is recomputed server-side."""
    svc = get_svc()
    patient = resolve_patient(svc, body.patient_id)
    authorise(svc, staff, patient, body.reason, body.reason_text, body.action if body.action in
              ("open_record", "ask_question", "verify") else "open_record")
    return {"ok": True}


# ------------------------------------------------------------------
# optional local LLM (Ollama) — reword only; validated in record_qa
# ------------------------------------------------------------------

def _ollama(system: str, user: str) -> str:
    import requests
    r = requests.post(os.getenv("VOXERA_OLLAMA_URL", "http://localhost:11434/api/chat"), timeout=20, json={
        "model": os.getenv("VOXERA_RECORD_MODEL", "voxera-patientfetch"), "stream": False, "think": False,
        "options": {"temperature": 0.1, "num_predict": 90},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return r.json().get("message", {}).get("content", "")
