// Client for the Voxera Patient Intelligence API. The browser only ever holds the staff member's own
// Supabase session token; the service-role key stays on the server.

import { supabase } from "@/lib/supabase";
import type {
  AccessReason, DocumentView, PatientAccessLog, PatientRecord, RecordAnswer, SearchResult, UploadResult,
} from "./patientTypes";

export const PATIENT_API_URL = (process.env.NEXT_PUBLIC_VOXERA_API_URL ?? "http://localhost:8100").replace(/\/$/, "");

export type ReasonRequired = {
  code: string;
  relationship: string;
  requires_reason: boolean;
  reasons: AccessReason[];
  label: string;
};

export class PatientApiError extends Error {
  status: number;
  reasonRequired: ReasonRequired | null;
  offline: boolean;
  constructor(status: number, message: string, reasonRequired: ReasonRequired | null = null, offline = false) {
    super(message);
    this.status = status;
    this.reasonRequired = reasonRequired;
    this.offline = offline;
  }
}

async function token(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  const t = data.session?.access_token;
  if (!t) throw new PatientApiError(401, "Please sign in again.");
  return t;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = await token();
  let res: Response;
  try {
    res = await fetch(`${PATIENT_API_URL}${path}`, {
      ...init,
      headers: { ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        Authorization: `Bearer ${auth}`, ...(init.headers ?? {}) },
    });
  } catch {
    throw new PatientApiError(0, "The patient-records service is not running.", null, true);
  }
  if (res.ok) return (await res.json()) as T;
  let detail: unknown = null;
  try { detail = (await res.json() as { detail?: unknown }).detail; } catch { /* not json */ }
  if (typeof detail === "object" && detail !== null && "requires_reason" in detail) {
    throw new PatientApiError(res.status, "A reason is required to open this record.", detail as ReasonRequired);
  }
  throw new PatientApiError(res.status, typeof detail === "string" ? detail : `Request failed (${res.status}).`);
}

export function searchPatients(q: string): Promise<{ results: SearchResult[]; role: string | null }> {
  return call(`/api/patients/search?q=${encodeURIComponent(q)}`);
}

export function openRecord(patientRef: string, reason?: AccessReason, reasonText?: string): Promise<PatientRecord> {
  return call(`/api/patients/${encodeURIComponent(patientRef)}/record`, {
    method: "POST", body: JSON.stringify({ reason: reason ?? null, reason_text: reasonText ?? null }),
  });
}

export function askRecord(patientRef: string, question: string, reason?: AccessReason, reasonText?: string): Promise<RecordAnswer> {
  return call(`/api/patients/${encodeURIComponent(patientRef)}/record-question`, {
    method: "POST", body: JSON.stringify({ question, reason: reason ?? null, reason_text: reasonText ?? null }),
  });
}

export function uploadPrescription(patientRef: string, file: File): Promise<UploadResult> {
  const fd = new FormData();
  fd.append("file", file);
  return call(`/api/patients/${encodeURIComponent(patientRef)}/prescriptions/upload`, { method: "POST", body: fd });
}

export function verifyItem(
  itemId: string, action: "confirm" | "reject", edits?: Record<string, string | null>,
): Promise<{ ok: boolean }> {
  return call(`/api/prescription-items/${encodeURIComponent(itemId)}/verify`, {
    method: "POST", body: JSON.stringify({ action, edits: edits ?? null }),
  });
}

export function getDocument(patientRef: string, docId: string): Promise<DocumentView> {
  return call(`/api/patients/${encodeURIComponent(patientRef)}/documents/${encodeURIComponent(docId)}`);
}

export function getAudit(patientRef: string): Promise<{ audit: PatientAccessLog[] }> {
  return call(`/api/patients/${encodeURIComponent(patientRef)}/audit`);
}

export function deleteDocument(patientRef: string, docId: string): Promise<{ ok: boolean; medications_removed: number }> {
  return call(`/api/patients/${encodeURIComponent(patientRef)}/documents/${encodeURIComponent(docId)}`, { method: "DELETE" });
}

export function deleteItem(itemId: string): Promise<{ ok: boolean }> {
  return call(`/api/prescription-items/${encodeURIComponent(itemId)}`, { method: "DELETE" });
}

/** Remove a medication/prescription entered or confirmed by mistake (kept for audit, hidden everywhere). */
export function removeMedication(
  patientRef: string, medId: string, origin: "patient_medications" | "prescriptions",
): Promise<{ ok: boolean }> {
  return call(`/api/patients/${encodeURIComponent(patientRef)}/medications/${encodeURIComponent(medId)}/remove`, {
    method: "POST", body: JSON.stringify({ origin }),
  });
}
