"""In-memory repository with the same interface as SupabaseRepo.

Used by the unit tests (and handy for offline demos). Behaviour mirrors the real
layer: prescription_items are staged as pending; confirmation is a separate,
explicit write that produces a patient_medications row.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Optional

from .repository import RepoError


def _now():
    return datetime.now(timezone.utc).isoformat()


class MemoryRepo:
    def __init__(self):
        self.patients: list = []
        self.medications: list = []
        self.prescriptions: list = []
        self.items: list = []
        self.documents: list = []
        self.calls: list = []
        self.referrals: list = []
        self.appointments: list = []
        self.emergencies: list = []
        self.summaries: dict = {}
        self.assessments: list = []
        self.hospital_users: list = []
        self.facilities: dict = {}
        self.access_logs: list = []
        self.missing: set = set()
        self.fail_reads = False          # simulate Supabase outage
        self._n = itertools.count(1)
        self.reads = 0                   # counts reads (cache tests)

    def _id(self):
        return f"id-{next(self._n)}"

    def _check(self):
        self.reads += 1
        if self.fail_reads:
            raise RepoError("simulated outage")

    # identity
    def find_patient(self, patient_id=None, uuid=None):
        self._check()
        if uuid:
            return [p for p in self.patients if p["id"] == uuid]
        if patient_id:
            return [p for p in self.patients if p.get("patient_id") == patient_id]
        return []

    def get_patient_by_patient_id(self, patient_id):
        rows = self.find_patient(patient_id=patient_id)
        return rows[0] if len(rows) == 1 else None

    def get_patient(self, patient_uuid):
        rows = self.find_patient(uuid=patient_uuid)
        return rows[0] if rows else None

    def search_patients(self, q, limit=8):
        self._check()
        from .identity import normalize_patient_id, normalize_phone
        q = (q or "").strip()
        if len(q) < 2:
            return []
        out = {}
        pid = normalize_patient_id(q)
        ph = normalize_phone(q) if q.replace(" ", "").replace("+", "").replace("-", "").isdigit() else None
        for p in self.patients:
            if pid and p.get("patient_id") == pid:
                out[p["id"]] = p
            if ph and (normalize_phone(p.get("phone")) or "").endswith(ph[-7:]):
                out[p["id"]] = p
            if q.lower() in (p.get("full_name") or "").lower():
                out[p["id"]] = p
        return list(out.values())[:limit]

    def get_hospital_user(self, user_id):
        self._check()
        return next((h for h in self.hospital_users if h["user_id"] == user_id), None)

    def get_patient_facilities(self, patient_uuid):
        self._check()
        fac = set()
        for r in self.referrals:
            if r["patient_id"] == patient_uuid and r.get("receiving_facility_id"):
                fac.add(r["receiving_facility_id"])
        for coll in (self.appointments, self.emergencies, self.calls):
            for r in coll:
                if r["patient_id"] == patient_uuid and r.get("facility_id"):
                    fac.add(r["facility_id"])
        return fac

    def get_facility_names(self, ids):
        return {i: self.facilities.get(i, i) for i in ids if i}

    # clinical reads
    def _for(self, coll, patient_uuid, limit=None):
        self._check()
        rows = [r for r in coll if r.get("patient_id") == patient_uuid]
        rows.sort(key=lambda r: r.get("created_at") or r.get("prescribed_at") or "", reverse=True)
        return rows[:limit] if limit else rows

    def get_patient_medications(self, p):
        return self._for(self.medications, p)

    def get_patient_prescriptions(self, p):
        return self._for(self.prescriptions, p)

    def get_prescription_items(self, p):
        return self._for(self.items, p)

    def get_patient_documents(self, p):
        return self._for(self.documents, p)

    def get_document(self, document_id):
        return next((d for d in self.documents if d["id"] == document_id), None)

    def get_patient_calls(self, p, limit=10):
        return self._for(self.calls, p, limit)

    def get_patient_referrals(self, p, limit=10):
        return self._for(self.referrals, p, limit)

    def get_patient_appointments(self, p, limit=10):
        return self._for(self.appointments, p, limit)

    def get_patient_emergencies(self, p, limit=10):
        return self._for(self.emergencies, p, limit)

    def get_call_summaries(self, call_ids):
        self._check()
        return {c: self.summaries[c] for c in call_ids if c in self.summaries}

    def get_patient_consultations(self, patient_uuid, limit=10):
        calls = self.get_patient_calls(patient_uuid, limit)
        out = []
        for c in calls:
            s = self.summaries.get(c["id"]) or {}
            a = next((x for x in self.assessments if x.get("call_id") == c["id"]), {})
            out.append({
                "id": c["id"], "kind": "voxera_call", "date": c.get("created_at"),
                "facility_id": c.get("facility_id"),
                "chief_complaint": s.get("chief_concern"),
                "symptoms": s.get("symptoms") or [],
                "assessment": a.get("ai_summary"),
                "risk_level": a.get("risk_level"),
                "guidance": [g.get("label") for g in (s.get("care_given") or []) if g.get("label")],
                "otc_guidance": [i for o in (s.get("otc_guidance") or []) for i in (o.get("items") or [])],
                "emergency": bool((s.get("emergency_status") or {}).get("detected")),
                "referral": s.get("referral"), "follow_up": s.get("follow_up"),
                "outcome": c.get("outcome"), "summary": s,
            })
        return out

    # writes
    def create_document(self, row):
        row = dict(row)
        row.setdefault("id", self._id())
        row.setdefault("created_at", _now())
        self.documents.append(row)
        return row

    def update_document(self, document_id, fields):
        d = self.get_document(document_id)
        if d:
            d.update(fields)

    def create_medication_candidate(self, item):
        item = dict(item)
        item.setdefault("id", self._id())
        item.setdefault("created_at", _now())
        vs = item.get("verification_status") or "pending"
        item["verification_status"] = vs if vs in ("pending", "needs_review") else "pending"
        item["source"] = "ocr"
        self.items.append(item)
        return item

    def get_prescription_item(self, item_id):
        return next((i for i in self.items if i["id"] == item_id), None)

    def update_prescription_item(self, item_id, fields):
        i = self.get_prescription_item(item_id)
        if i:
            i.update(fields)

    def get_patient_medication(self, med_id):
        return next((m for m in self.medications if m["id"] == med_id), None)

    def get_prescription(self, rx_id):
        return next((r for r in self.prescriptions if r["id"] == rx_id), None)

    def update_patient_medication(self, med_id, fields):
        m = self.get_patient_medication(med_id)
        if m:
            m.update(fields)

    def update_prescription(self, rx_id, fields):
        r = self.get_prescription(rx_id)
        if r:
            r.update(fields)

    def get_medications_for_document(self, document_id):
        return [m for m in self.medications if m.get("source_document_id") == document_id]

    def delete_prescription_item(self, item_id):
        self.items = [i for i in self.items if i["id"] != item_id]

    def delete_document(self, document_id, storage_path=None):
        self.documents = [d for d in self.documents if d["id"] != document_id]
        self.items = [i for i in self.items if i.get("document_id") != document_id]            # cascade
        for m in self.medications:
            if m.get("source_document_id") == document_id:
                m["source_document_id"] = None                                                  # on delete set null

    def create_patient_medication(self, row):
        row = dict(row)
        row.setdefault("id", self._id())
        row.setdefault("created_at", _now())
        self.medications.append(row)
        return row

    def create_record_access_log(self, row):
        row = dict(row)
        row.setdefault("id", self._id())
        row.setdefault("accessed_at", _now())
        self.access_logs.append(row)
        return row

    def get_access_logs(self, patient_uuid, limit=50):
        self._check()
        return [r for r in self.access_logs if r["patient_id"] == patient_uuid][-limit:][::-1]
