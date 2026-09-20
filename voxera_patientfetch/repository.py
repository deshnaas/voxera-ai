"""Data-access layer for Patient Intelligence.

* ``SupabaseRepo`` reuses the EXISTING service-role client from
  ``voxera_supabase`` (no second client, no key handling here, key never logged).
* Every read degrades gracefully when a table is missing (migration not run yet):
  it returns [] and records the table in ``self.missing`` so callers can flag
  the context as partial instead of hallucinating.
* ``MemoryRepo`` (memory_repo.py) implements the same interface for tests.

Nothing in this module makes a clinical decision.
"""

from __future__ import annotations

import json
import time
import re
from datetime import datetime, timezone
from typing import Any, Optional

MISSING_RX = re.compile(r"PGRST205|PGRST204|42P01|42703|schema cache|does not exist|Could not find", re.I)
TRANSIENT_RX = re.compile(r"disconnected|connection (reset|aborted|closed|error)|timed? ?out|temporarily|RemoteProtocol|ReadError|ConnectError", re.I)
SUMMARY_PREFIX = "CALL_SUMMARY "


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RepoError(RuntimeError):
    """Real (non-missing-table) database failure. Callers must not invent data."""


class SupabaseRepo:
    def __init__(self, client=None):
        if client is None:
            import voxera_supabase as _db          # existing layer; owns client init
            client = _db.supabase
        self.sb = client
        self.missing: set[str] = set()

    # -- helpers -------------------------------------------------
    def _q(self, table: str, build) -> list:
        """Run a select; missing table/column -> []; other errors -> RepoError."""
        last = ""
        for attempt in range(3):
            try:
                resp = build(self.sb.table(table)).execute()
                return resp.data or []
            except Exception as e:                  # noqa: BLE001
                msg = getattr(e, "message", None) or str(e)
                if MISSING_RX.search(msg):
                    self.missing.add(table)
                    return []
                last = msg
                if TRANSIENT_RX.search(msg) and attempt < 2:
                    time.sleep(0.25 * (attempt + 1))    # idle keep-alive connection dropped: retry on a fresh one
                    continue
                break
        raise RepoError(f"{table}: {last[:160]}") from None

    # ============================================================
    # Identity
    # ============================================================
    def find_patient(self, patient_id: Optional[str] = None, uuid: Optional[str] = None) -> list:
        if uuid:
            return self._q("patients", lambda t: t.select("*").eq("id", uuid).limit(2))
        if patient_id:
            return self._q("patients", lambda t: t.select("*").eq("patient_id", patient_id).limit(2))
        return []

    def get_patient_by_patient_id(self, patient_id: str) -> Optional[dict]:
        rows = self.find_patient(patient_id=patient_id)
        return rows[0] if len(rows) == 1 else None

    def get_patient(self, patient_uuid: str) -> Optional[dict]:
        rows = self.find_patient(uuid=patient_uuid)
        return rows[0] if rows else None

    def search_patients(self, q: str, limit: int = 8) -> list:
        """Indexed search: exact patient_id, phone suffix, name prefix/substring."""
        q = (q or "").strip()
        if len(q) < 2:
            return []
        safe = re.sub(r"[%,()*]", " ", q).strip()
        out: dict[str, dict] = {}
        from .identity import normalize_patient_id, normalize_phone
        pid = normalize_patient_id(q) if re.search(r"(?i)\bvx|^\d{3,}$", q) else None
        if pid:
            for r in self._q("patients", lambda t: t.select("*").eq("patient_id", pid).limit(3)):
                out[r["id"]] = r
        ph = normalize_phone(q) if re.fullmatch(r"[\d\s+\-]{7,}", q) else None
        if ph:
            for r in self._q("patients", lambda t: t.select("*").ilike("phone", f"%{ph}").limit(limit)):
                out[r["id"]] = r
        if not out or re.search(r"[A-Za-z]{2}", safe):
            for r in self._q("patients", lambda t: t.select("*").ilike("full_name", f"%{safe}%").limit(limit)):
                out[r["id"]] = r
        return list(out.values())[:limit]

    # ============================================================
    # Facility relationship (used by the access policy)
    # ============================================================
    def get_hospital_user(self, user_id: str) -> Optional[dict]:
        rows = self._q("hospital_users", lambda t: t.select("user_id,facility_id,role").eq("user_id", user_id).limit(1))
        return rows[0] if rows else None

    def get_patient_facilities(self, patient_uuid: str) -> set:
        """Facilities that already have a care relationship with the patient."""
        fac: set = set()
        for table, col in (("referrals", "receiving_facility_id"), ("appointments", "facility_id"),
                           ("emergency_cases", "facility_id"), ("calls", "facility_id")):
            for r in self._q(table, lambda t, c=col: t.select(c).eq("patient_id", patient_uuid).limit(200)):
                if r.get(col):
                    fac.add(r[col])
        return fac

    def get_facility_names(self, ids) -> dict:
        ids = [i for i in ids if i]
        if not ids:
            return {}
        rows = self._q("facilities", lambda t: t.select("id,name").in_("id", ids))
        return {r["id"]: r["name"] for r in rows}

    # ============================================================
    # Clinical reads (patient = patients.id UUID)
    # ============================================================
    def get_patient_medications(self, patient_uuid: str) -> list:
        return self._q("patient_medications",
                       lambda t: t.select("*").eq("patient_id", patient_uuid).order("created_at", desc=True).limit(100))

    def get_patient_prescriptions(self, patient_uuid: str) -> list:
        """Clinician-authored prescriptions (flat rows: one medicine each)."""
        return self._q("prescriptions",
                       lambda t: t.select("*").eq("patient_id", patient_uuid).order("prescribed_at", desc=True).limit(100))

    def get_prescription_items(self, patient_uuid: str) -> list:
        """Staged/confirmed items extracted from uploaded documents."""
        return self._q("prescription_items",
                       lambda t: t.select("*").eq("patient_id", patient_uuid).order("created_at", desc=True).limit(200))

    def get_patient_documents(self, patient_uuid: str) -> list:
        return self._q("medical_documents",
                       lambda t: t.select("*").eq("patient_id", patient_uuid).order("created_at", desc=True).limit(50))

    def get_document(self, document_id: str) -> Optional[dict]:
        rows = self._q("medical_documents", lambda t: t.select("*").eq("id", document_id).limit(1))
        return rows[0] if rows else None

    def get_patient_calls(self, patient_uuid: str, limit: int = 10) -> list:
        return self._q("calls", lambda t: t.select("*").eq("patient_id", patient_uuid)
                       .order("created_at", desc=True).limit(limit))

    def get_patient_referrals(self, patient_uuid: str, limit: int = 10) -> list:
        return self._q("referrals", lambda t: t.select("*").eq("patient_id", patient_uuid)
                       .order("created_at", desc=True).limit(limit))

    def get_patient_appointments(self, patient_uuid: str, limit: int = 10) -> list:
        return self._q("appointments", lambda t: t.select("*").eq("patient_id", patient_uuid)
                       .order("appointment_date", desc=True).limit(limit))

    def get_patient_emergencies(self, patient_uuid: str, limit: int = 10) -> list:
        return self._q("emergency_cases", lambda t: t.select("*").eq("patient_id", patient_uuid)
                       .order("created_at", desc=True).limit(limit))

    def get_call_summaries(self, call_ids: list) -> dict:
        """call_id -> summary dict. Prefers call_summaries; falls back to the
        ``CALL_SUMMARY {json}`` system turn Voxera writes on older projects."""
        out: dict = {}
        if not call_ids:
            return out
        for r in self._q("call_summaries", lambda t: t.select("call_id,summary_json,summary_text").in_("call_id", call_ids)):
            sj = r.get("summary_json")
            if isinstance(sj, str):
                try:
                    sj = json.loads(sj)
                except ValueError:
                    sj = None
            if sj:
                out[r["call_id"]] = sj
        rest = [c for c in call_ids if c not in out]
        if rest:
            for r in self._q("conversation_turns",
                             lambda t: t.select("call_id,message").in_("call_id", rest)
                             .eq("speaker", "system").like("message", SUMMARY_PREFIX + "%")):
                try:
                    out.setdefault(r["call_id"], json.loads(r["message"][len(SUMMARY_PREFIX):]))
                except ValueError:
                    continue
        return out

    def get_patient_consultations(self, patient_uuid: str, limit: int = 10) -> list:
        """Derived 'consultations': completed Voxera calls with their structured
        summary + assessment + referral, plus appointments. No table needed."""
        calls = self.get_patient_calls(patient_uuid, limit)
        sums = self.get_call_summaries([c["id"] for c in calls])
        assess = self._q("ai_assessments", lambda t: t.select("*").eq("patient_id", patient_uuid)
                         .order("created_at", desc=True).limit(limit))
        by_call = {a.get("call_id"): a for a in assess if a.get("call_id")}
        out = []
        for c in calls:
            s = sums.get(c["id"]) or {}
            a = by_call.get(c["id"]) or {}
            otc = [i for o in (s.get("otc_guidance") or []) for i in (o.get("items") or [])]
            out.append({
                "id": c["id"], "kind": "voxera_call", "date": c.get("created_at"),
                "facility_id": c.get("facility_id"),
                "chief_complaint": s.get("chief_concern"),
                "symptoms": s.get("symptoms") or [],
                "assessment": a.get("ai_summary") or a.get("symptoms_summary"),
                "risk_level": a.get("risk_level"),
                "guidance": [g.get("label") for g in (s.get("care_given") or []) if g.get("label")],
                "otc_guidance": otc,
                "emergency": bool((s.get("emergency_status") or {}).get("detected")) or "emergency" in str(c.get("outcome") or ""),
                "referral": s.get("referral"),
                "follow_up": s.get("follow_up"),
                "outcome": c.get("outcome"),
                "summary": s,
            })
        return out

    # ============================================================
    # Writes (all through the service role; provenance is mandatory)
    # ============================================================
    def _insert(self, table: str, row: dict) -> dict:
        try:
            resp = self.sb.table(table).insert(row).execute()
            return (resp.data or [row])[0]
        except Exception as e:                      # noqa: BLE001
            msg = getattr(e, "message", None) or str(e)
            if MISSING_RX.search(msg):
                self.missing.add(table)
            raise RepoError(f"{table}: {msg[:160]}") from None

    def create_document(self, row: dict) -> dict:
        return self._insert("medical_documents", row)

    def update_document(self, document_id: str, fields: dict) -> None:
        try:
            self.sb.table("medical_documents").update(fields).eq("id", document_id).execute()
        except Exception as e:                      # noqa: BLE001
            raise RepoError(f"medical_documents: {str(e)[:160]}") from None

    def create_medication_candidate(self, item: dict) -> dict:
        """Stage an OCR-extracted medicine. Always pending/needs_review — never active."""
        item = dict(item)
        item["verification_status"] = item.get("verification_status") or "pending"
        if item["verification_status"] not in ("pending", "needs_review"):
            item["verification_status"] = "pending"
        item["source"] = "ocr"
        return self._insert("prescription_items", item)

    def get_prescription_item(self, item_id: str) -> Optional[dict]:
        rows = self._q("prescription_items", lambda t: t.select("*").eq("id", item_id).limit(1))
        return rows[0] if rows else None

    def update_prescription_item(self, item_id: str, fields: dict) -> None:
        try:
            self.sb.table("prescription_items").update(fields).eq("id", item_id).execute()
        except Exception as e:                      # noqa: BLE001
            raise RepoError(f"prescription_items: {str(e)[:160]}") from None

    # -- removal (soft for the medical record, hard for uploaded candidates/documents) --------
    def get_patient_medication(self, med_id: str) -> Optional[dict]:
        rows = self._q("patient_medications", lambda t: t.select("*").eq("id", med_id).limit(1))
        return rows[0] if rows else None

    def get_prescription(self, rx_id: str) -> Optional[dict]:
        rows = self._q("prescriptions", lambda t: t.select("*").eq("id", rx_id).limit(1))
        return rows[0] if rows else None

    def update_patient_medication(self, med_id: str, fields: dict) -> None:
        try:
            self.sb.table("patient_medications").update({**fields, "updated_at": _now()}).eq("id", med_id).execute()
        except Exception as e:                      # noqa: BLE001
            raise RepoError(f"patient_medications: {str(e)[:160]}") from None

    def update_prescription(self, rx_id: str, fields: dict) -> None:
        try:
            self.sb.table("prescriptions").update({**fields, "updated_at": _now()}).eq("id", rx_id).execute()
        except Exception as e:                      # noqa: BLE001
            raise RepoError(f"prescriptions: {str(e)[:160]}") from None

    def get_medications_for_document(self, document_id: str) -> list:
        return self._q("patient_medications", lambda t: t.select("*").eq("source_document_id", document_id))

    def delete_prescription_item(self, item_id: str) -> None:
        try:
            self.sb.table("prescription_items").delete().eq("id", item_id).execute()
        except Exception as e:                      # noqa: BLE001
            raise RepoError(f"prescription_items: {str(e)[:160]}") from None

    def delete_document(self, document_id: str, storage_path: Optional[str] = None) -> None:
        """Deletes the document row (its extracted items cascade) and the stored original file."""
        try:
            self.sb.table("medical_documents").delete().eq("id", document_id).execute()
        except Exception as e:                      # noqa: BLE001
            raise RepoError(f"medical_documents: {str(e)[:160]}") from None
        if storage_path:
            try:
                self.sb.storage.from_("patient-documents").remove([storage_path])
            except Exception:                       # noqa: BLE001
                pass                                # the record row is gone; an orphan file is harmless and private

    def create_patient_medication(self, row: dict) -> dict:
        return self._insert("patient_medications", row)

    def create_record_access_log(self, row: dict) -> dict:
        row = dict(row)
        row.setdefault("accessed_at", _now())
        return self._insert("patient_record_access_logs", row)

    def get_access_logs(self, patient_uuid: str, limit: int = 50) -> list:
        return self._q("patient_record_access_logs",
                       lambda t: t.select("*").eq("patient_id", patient_uuid).order("accessed_at", desc=True).limit(limit))
