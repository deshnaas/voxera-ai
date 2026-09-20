"""PatientIntelligenceAI — retrieval + provenance + context, NOT a doctor.

Deterministic by design: lookup, medication provenance, date sorting, access
control and audit never touch an LLM. The optional LLM only rewords answers
that were already retrieved (see record_qa.py).

Logging never contains names, prescription text or secrets — only ids/counts.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Optional

from . import access_policy as policy
from .models import (CLINICIAN_AUTHORED, PatientContext, Source, Verification,
                     is_clinician_authored)
from .repository import RepoError

log = logging.getLogger("voxera.patient_ai")


def _plog(msg: str) -> None:
    print(f"[PATIENT_AI] {msg}")


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def age_from_dob(dob: Optional[str], today: Optional[date] = None) -> Optional[int]:
    if not dob:
        return None
    try:
        d = datetime.fromisoformat(str(dob)[:10]).date()
    except ValueError:
        return None
    t = today or date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def split_list(text) -> list:
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        return [str(x).strip() for x in text if str(x).strip()]
    return [p.strip() for p in re.split(r"[,;\n]", str(text)) if p.strip()]


REMOVED = "entered_in_error"


def _iso(v) -> str:
    return str(v) if v else ""


# ------------------------------------------------------------------
# Unified medication view (this is where provenance is enforced)
# ------------------------------------------------------------------

def normalize_medications(meds: list, prescriptions: list, items: list) -> list:
    """Merge the three storage shapes into one list with an explicit source and
    a display status. Rules:
      * only clinician-authored sources can be ``is_clinician_confirmed``
      * OCR items that are pending/needs_review stay 'pending verification'
      * confirmed OCR items appear once, via patient_medications (source_item_id)
      * a medicine is ``is_current`` only if its status explicitly says 'active'
        AND it is clinician-authored
    """
    out: list = []
    confirmed_item_ids = {m.get("source_item_id") for m in meds if m.get("source_item_id")}
    # Rows removed as "entered in error" stay in the database for audit but never appear in the record or answers.
    gone = lambda r: str(r.get("status") or "").lower() == REMOVED       # noqa: E731
    meds = [m for m in meds if not gone(m)]
    prescriptions = [p for p in prescriptions if not gone(p)]
    items = [i for i in items if i.get("verification_status") != "confirmed"]   # a confirmed item lives on as its medication

    for m in meds:
        src = m.get("source") or Source.IMPORTED.value
        status = (m.get("status") or "active").lower()
        out.append({
            "id": m.get("id"), "origin": "patient_medications",
            "medicine_name": m.get("medicine_name") or m.get("medication_name"),
            "generic_name": m.get("generic_name"), "strength": m.get("strength") or m.get("dosage"),
            "form": m.get("form"), "route": m.get("route"), "frequency": m.get("frequency"),
            "duration": m.get("duration"), "instructions": m.get("instructions"),
            "source": src, "status": status,
            "verified_by": m.get("verified_by"), "verified_at": m.get("verified_at"),
            "date": _iso(m.get("created_at")), "document_id": m.get("source_document_id"),
        })

    for p in prescriptions:
        # v2 flat clinician prescription: one row per medicine
        out.append({
            "id": p.get("id"), "origin": "prescriptions",
            "medicine_name": p.get("medication_name") or p.get("medicine_name"),
            "generic_name": p.get("generic_name"), "strength": p.get("dosage") or p.get("strength"),
            "form": p.get("form"), "route": p.get("route"), "frequency": p.get("frequency"),
            "duration": p.get("duration"), "instructions": p.get("instructions"),
            "source": Source.CLINICIAN.value, "status": (p.get("status") or "active").lower(),
            "verified_by": p.get("prescribed_by"), "verified_at": p.get("prescribed_at"),
            "date": _iso(p.get("prescribed_at") or p.get("created_at")), "document_id": None,
        })

    for it in items:
        if it.get("id") in confirmed_item_ids:
            continue                                   # represented by its patient_medications row
        vs = (it.get("verification_status") or "pending").lower()
        out.append({
            "id": it.get("id"), "origin": "prescription_items",
            "medicine_name": it.get("medicine_name"), "generic_name": it.get("generic_name"),
            "strength": it.get("strength"), "form": it.get("form"), "route": it.get("route"),
            "frequency": it.get("frequency"), "duration": it.get("duration"),
            "instructions": it.get("instructions"),
            "source": Source.OCR.value, "status": vs,     # pending | needs_review | rejected | confirmed
            "confidence": it.get("confidence"),
            "verified_by": it.get("verified_by"), "verified_at": it.get("verified_at"),
            "date": _iso(it.get("created_at")), "document_id": it.get("document_id"),
        })

    for r in out:
        clin = is_clinician_authored(r["source"])
        r["is_clinician_confirmed"] = clin and r["status"] in ("active", "stopped", "completed")
        r["is_current"] = clin and r["status"] == "active"
        r["provenance_label"] = provenance_label(r)
    out.sort(key=lambda r: r.get("date") or "", reverse=True)
    return out


def provenance_label(m: dict) -> str:
    src, st = m.get("source"), m.get("status")
    if src == Source.OCR.value:
        if st == "rejected":
            return "Extracted from prescription — rejected by clinician"
        return "Extracted from prescription — pending verification"
    if src == Source.OCR_VERIFIED.value:
        return "Extracted from prescription — verified by clinician"
    if src == Source.CLINICIAN.value:
        return "Prescribed by clinician"
    if src == Source.PATIENT_REPORTED.value:
        return "Reported by patient — not verified"
    return "Imported record"


# ------------------------------------------------------------------
# Service
# ------------------------------------------------------------------

class PatientIntelligenceAI:
    def __init__(self, repo, cache_ttl: float = 600.0, workers: int = 6):
        self.repo = repo
        self.cache_ttl = cache_ttl
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vpai")

    # -- identity ---------------------------------------------------
    def get_patient(self, patient_id: Optional[str] = None, uuid: Optional[str] = None) -> Optional[dict]:
        rows = self.repo.find_patient(patient_id=patient_id, uuid=uuid)
        return rows[0] if len(rows) == 1 else None

    # -- reads ------------------------------------------------------
    def get_medications(self, patient_uuid: str) -> list:
        return normalize_medications(
            self.repo.get_patient_medications(patient_uuid),
            self.repo.get_patient_prescriptions(patient_uuid),
            self.repo.get_prescription_items(patient_uuid))

    def get_prescriptions(self, patient_uuid: str) -> list:
        return [p for p in self.repo.get_patient_prescriptions(patient_uuid)
                if str(p.get("status") or "").lower() != REMOVED]

    def get_previous_consultations(self, patient_uuid: str, limit: int = 10) -> list:
        return self.repo.get_patient_consultations(patient_uuid, limit)

    def get_previous_calls(self, patient_uuid: str, limit: int = 10) -> list:
        return self.repo.get_patient_calls(patient_uuid, limit)

    def get_patient_summary(self, patient_uuid: str) -> dict:
        ctx = self.build_clinical_context(patient_uuid)
        cons = ctx.previous_consultations
        return {
            "last_consultation": (cons[0].get("date") if cons else None),
            "active_medications": len(ctx.active_medications),
            "unverified_medications": len(ctx.unverified_medications),
            "allergies": len(ctx.allergies),
            "referrals": len(ctx.recent_referrals),
            "partial": ctx.partial,
        }

    def search_records(self, patient_uuid: str, query: str, limit: int = 8) -> list:
        """Deterministic keyword search across the patient's structured records."""
        terms = [t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower())]
        if not terms:
            return []
        hits = []
        for m in self.get_medications(patient_uuid):
            blob = " ".join(str(m.get(k) or "") for k in
                            ("medicine_name", "generic_name", "route", "instructions", "form")).lower()
            score = sum(t in blob for t in terms)
            if score:
                hits.append((score, {"type": "medication", "id": m["id"], "date": m["date"], "record": m}))
        for c in self.get_previous_consultations(patient_uuid):
            blob = " ".join([str(c.get("chief_complaint") or ""), " ".join(c.get("symptoms") or []),
                             str(c.get("assessment") or ""), " ".join(c.get("guidance") or [])]).lower()
            score = sum(t in blob for t in terms)
            if score:
                hits.append((score, {"type": "consultation", "id": c["id"], "date": c["date"], "record": c}))
        hits.sort(key=lambda h: (h[0], h[1].get("date") or ""), reverse=True)
        _plog(f"record query terms={len(terms)} hits={len(hits)}")
        return [h[1] for h in hits[:limit]]

    def answer_record_question(self, patient_uuid: str, question: str, use_llm: bool = False, llm=None):
        from .record_qa import answer_patient_history_question
        return answer_patient_history_question(self, patient_uuid, question, use_llm=use_llm, llm=llm)

    # -- context ----------------------------------------------------
    def build_clinical_context(self, patient_uuid: str, force: bool = False) -> PatientContext:
        with self._lock:
            hit = self._cache.get(patient_uuid)
            if hit and not force and (time.time() - hit[0]) < self.cache_ttl:
                return hit[1]

        patient = self.repo.get_patient(patient_uuid)          # RepoError propagates: no invented record
        if not patient:
            raise RepoError("patient not found")

        partial = False

        def safe(fn, *a):
            nonlocal partial
            try:
                return fn(*a)
            except RepoError:
                partial = True
                return []

        f = {
            "meds": self._pool.submit(safe, self.repo.get_patient_medications, patient_uuid),
            "rx": self._pool.submit(safe, self.repo.get_patient_prescriptions, patient_uuid),
            "items": self._pool.submit(safe, self.repo.get_prescription_items, patient_uuid),
            "cons": self._pool.submit(safe, self.repo.get_patient_consultations, patient_uuid, 5),
            "calls": self._pool.submit(safe, self.repo.get_patient_calls, patient_uuid, 5),
            "refs": self._pool.submit(safe, self.repo.get_patient_referrals, patient_uuid, 5),
            "appt": self._pool.submit(safe, self.repo.get_patient_appointments, patient_uuid, 5),
            "emg": self._pool.submit(safe, self.repo.get_patient_emergencies, patient_uuid, 5),
            "docs": self._pool.submit(safe, self.repo.get_patient_documents, patient_uuid),
        }
        r = {k: v.result() for k, v in f.items()}

        meds = normalize_medications(r["meds"], r["rx"], r["items"])
        active = [m for m in meds if m["is_current"]]
        unverified = [m for m in meds if not m["is_clinician_confirmed"] and m["status"] not in ("rejected",)
                      and (m["source"] in (Source.OCR.value, Source.PATIENT_REPORTED.value)
                           and m["status"] in ("pending", "needs_review", "active"))]

        # allergies / conditions: verified record + anything the patient reported on calls
        allergies = split_list(patient.get("allergies"))
        conditions = split_list(patient.get("chronic_conditions"))
        for c in r["cons"]:
            prof = (c.get("summary") or {}).get("patient_profile") or {}
            for a in prof.get("allergies") or []:
                if a and a.lower() not in {x.lower() for x in allergies}:
                    allergies.append(a)
            for cd in prof.get("conditions") or []:
                if cd and cd.lower() not in {x.lower() for x in conditions}:
                    conditions.append(cd)

        ctx = PatientContext(
            patient={
                "patient_id": patient.get("patient_id"),
                "first_name": (patient.get("full_name") or "").split(" ")[0] or None,
                "age": age_from_dob(patient.get("date_of_birth")),
                "sex": patient.get("gender"),
            },
            allergies=allergies, conditions=conditions,
            active_medications=active, unverified_medications=unverified,
            previous_prescriptions=[m for m in meds if m["origin"] == "prescriptions"][:5],
            previous_consultations=r["cons"], previous_calls=r["calls"],
            recent_referrals=r["refs"], recent_emergencies=r["emg"], appointments=r["appt"],
            relevant_documents=[{"id": d["id"], "type": d.get("document_type"), "date": d.get("created_at")}
                                for d in r["docs"][:5]],
            loaded_at=time.time(), partial=partial or bool(getattr(self.repo, "missing", set())),
        )
        with self._lock:
            self._cache[patient_uuid] = (time.time(), ctx)
        _plog(f"context loaded meds={len(active)}+{len(unverified)}unv "
              f"cons={len(ctx.previous_consultations)} partial={ctx.partial}")
        return ctx

    def invalidate(self, patient_uuid: str) -> None:
        with self._lock:
            self._cache.pop(patient_uuid, None)

    # -- medication staging / confirmation ------------------------------
    def stage_medication(self, patient_uuid: str, item: dict, document_id: Optional[str] = None) -> dict:
        """Persist an OCR candidate. Always pending / needs_review. Never active."""
        row = dict(item)
        row["patient_id"] = patient_uuid
        if document_id:
            row["document_id"] = document_id
        staged = self.repo.create_medication_candidate(row)
        self.invalidate(patient_uuid)
        print(f"[PRESCRIPTION_AI] verification {staged.get('verification_status')}")
        return staged

    def confirm_medication(self, item_id: str, *, user_id: str, role: str, facility_id: str,
                           edits: Optional[dict] = None) -> dict:
        """Clinician confirmation: OCR candidate -> patient_medications(source=ocr_verified).

        Only a doctor may confirm. Edits are applied to the item (and the
        original OCR text is preserved in the document)."""
        if policy.normalise_role(role) != "doctor":
            raise PermissionError("only a doctor can confirm a medication")
        item = self.repo.get_prescription_item(item_id)
        if not item:
            raise LookupError("item not found")
        if (item.get("verification_status") or "") in ("confirmed", "rejected"):
            raise ValueError(f"item already {item.get('verification_status')}")

        allowed = ("medicine_name", "generic_name", "strength", "form", "route",
                   "frequency", "duration", "instructions")
        merged = {k: item.get(k) for k in allowed}
        if edits:
            merged.update({k: v for k, v in edits.items() if k in allowed})
        if not (merged.get("medicine_name") or "").strip():
            raise ValueError("medicine name is required")

        now = datetime.now(timezone.utc).isoformat()
        self.repo.update_prescription_item(item_id, {**merged, "verification_status": "confirmed",
                                                     "verified_by": user_id, "verified_at": now})
        med = self.repo.create_patient_medication({
            "patient_id": item["patient_id"], **merged,
            "source": Source.OCR_VERIFIED.value, "status": "active",
            "source_document_id": item.get("document_id"), "source_item_id": item_id,
            "verified_by": user_id, "verified_at": now, "facility_id": facility_id,
        })
        self.invalidate(item["patient_id"])
        print("[PRESCRIPTION_AI] medication confirmed by clinician")
        return med

    def reject_medication(self, item_id: str, *, user_id: str, role: str) -> None:
        if policy.normalise_role(role) not in ("doctor", "nurse"):
            raise PermissionError("not permitted to review medications")
        item = self.repo.get_prescription_item(item_id)
        if not item:
            raise LookupError("item not found")
        self.repo.update_prescription_item(item_id, {
            "verification_status": "rejected", "verified_by": user_id,
            "verified_at": datetime.now(timezone.utc).isoformat()})
        self.invalidate(item["patient_id"])

    # -- removal -----------------------------------------------------------------
    # Wrongly entered records are marked "entered_in_error" (kept for audit, hidden everywhere else);
    # OCR candidates and uploaded documents are deleted outright. Every removal is audited by the caller.
    def void_medication(self, *, origin: str, med_id: str, patient_uuid: str, role: str) -> dict:
        """Remove a medication/prescription that was entered or confirmed by mistake. Doctors only."""
        if policy.normalise_role(role) != "doctor":
            raise PermissionError("only a doctor can remove a medication")
        if origin == "patient_medications":
            row = self.repo.get_patient_medication(med_id)
            update = self.repo.update_patient_medication
        elif origin == "prescriptions":
            row = self.repo.get_prescription(med_id)
            update = self.repo.update_prescription
        else:
            raise ValueError("unknown medication origin")
        if not row or row.get("patient_id") != patient_uuid:
            raise LookupError("medication not found")
        update(med_id, {"status": REMOVED})
        self.invalidate(patient_uuid)
        print("[PATIENT_AI] medication removed (entered in error)")
        return {"id": med_id, "status": REMOVED}

    def delete_item(self, item_id: str, *, role: str) -> str:
        """Delete an unconfirmed OCR candidate. A confirmed one must be removed as a medication instead."""
        if policy.normalise_role(role) not in ("doctor", "nurse"):
            raise PermissionError("not permitted")
        item = self.repo.get_prescription_item(item_id)
        if not item:
            raise LookupError("item not found")
        if item.get("verification_status") == "confirmed":
            raise ValueError("this medicine was confirmed; remove it from the medication list instead")
        self.repo.delete_prescription_item(item_id)
        self.invalidate(item["patient_id"])
        return item["patient_id"]

    def delete_document(self, doc_id: str, *, patient_uuid: str, role: str) -> dict:
        """Delete an uploaded prescription (row, extracted candidates, stored file).
        Medicines a doctor already confirmed from it are marked entered-in-error; a nurse may only delete
        documents with nothing confirmed."""
        r = policy.normalise_role(role)
        if r not in ("doctor", "nurse"):
            raise PermissionError("not permitted")
        doc = self.repo.get_document(doc_id)
        if not doc or doc.get("patient_id") != patient_uuid:
            raise LookupError("document not found")
        live = [m for m in self.repo.get_medications_for_document(doc_id)
                if str(m.get("status") or "").lower() != REMOVED]
        if live and r != "doctor":
            raise PermissionError("a doctor must delete a prescription that has confirmed medicines")
        for m in live:
            self.repo.update_patient_medication(m["id"], {"status": REMOVED})
        self.repo.delete_document(doc_id, doc.get("storage_path"))
        self.invalidate(patient_uuid)
        print("[PATIENT_AI] prescription document deleted")
        return {"deleted": doc_id, "medications_removed": len(live)}

    # -- patient-reported (from a call) ----------------------------------
    def record_patient_reported(self, patient_uuid: str, medicine_name: str, call_id: Optional[str] = None) -> dict:
        row = self.repo.create_patient_medication({
            "patient_id": patient_uuid, "medicine_name": medicine_name.strip(),
            "source": Source.PATIENT_REPORTED.value, "status": "active"})
        self.invalidate(patient_uuid)
        return row

    # -- audit -----------------------------------------------------
    def audit_access(self, *, patient_uuid: str, user_id: Optional[str], facility_id: Optional[str],
                     role: Optional[str], reason: Optional[str], reason_text: Optional[str] = None,
                     source: str = "dashboard", decision=None, action: str = "open_record",
                     call_id: Optional[str] = None, meta: Optional[dict] = None) -> Optional[dict]:
        row = policy.build_audit_row(
            patient_uuid=patient_uuid, user_id=user_id, facility_id=facility_id, role=role,
            reason=reason, reason_text=reason_text, source=source, decision=decision,
            action=action, call_id=call_id, meta=meta)
        try:
            return self.repo.create_record_access_log(row)
        except RepoError:
            # An access we cannot audit must not silently succeed for external records.
            raise
