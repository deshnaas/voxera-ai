// ============================================================
// Voxera <-> dashboard data layer
// ============================================================
// Everything here reads/writes the SAME Supabase records the Voxera voice
// agent writes. No mock data.

import { supabase } from "@/lib/supabase";
import type { PatientClinicalContext, RecordContext, VoiceSignal } from "@/lib/patientTypes";
export { humanDuration } from "@/lib/format";

// ------------------------------------------------------------
// Types
// ------------------------------------------------------------

export type Patient = {
  id: string;
  patient_id?: string;          // human-facing ID, e.g. VX-000123 (after the patient-intelligence migration)
  full_name: string;
  phone: string | null;
  date_of_birth: string | null;
  gender: string | null;
  preferred_language: string | null;
  village_or_locality: string | null;
  district: string | null;
  created_at?: string;
  updated_at?: string;
  // optional clinical fields — present only after sql/2026_dashboard_v2.sql
  blood_group?: string | null;
  allergies?: string | null;
  chronic_conditions?: string | null;
  emergency_contact_name?: string | null;
  emergency_contact_phone?: string | null;
  clinical_notes?: string | null;
};

export type VoxeraCall = {
  id: string;
  patient_id: string | null;
  facility_id: string | null;
  call_type: string | null;
  status: string | null;
  outcome: string | null;
  language: string | null;
  call_duration_seconds: number | null;
  ai_response_count: number | null;
  interruption_count: number | null;
  emergency_checks_count: number | null;
  call_success: boolean | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
};

export type ConversationTurn = {
  id: string;
  call_id: string;
  speaker: string; // patient | ai | system
  message: string;
  sequence_number: number;
  created_at: string;
};

export type OtcGuidance = {
  type: string;
  deferred: boolean;
  items: string[];
  spoken: string;
  safety: string;
};

export type CareGiven = {
  topic: string | null;
  label: string | null;
  steps: string[];
  escalation: string[];
};

export type CallSummary = {
  chief_concern?: string;
  symptoms?: string[];
  duration?: string | null;
  temperature_f?: string | null;
  medications_mentioned?: string[];
  patient_profile?: {
    full_name?: string;
    age?: number;
    is_child?: boolean;
    pregnant?: boolean;
    allergies?: string[];
    conditions?: string[];
    [k: string]: unknown;
  };
  emergency_status?: {
    detected: boolean;
    category?: string | null;
    severity?: string | null;
    trigger_phrase?: string | null;
    recommended_department?: string | null;
    note?: string;
  };
  care_given?: CareGiven[];
  otc_guidance?: OtcGuidance[];
  referral?: Record<string, unknown> | null;
  follow_up?: Record<string, unknown> | null;
  call_outcome?: string | null;
  generated_at?: string;
  // added by Patient Intelligence (optional)
  voice_signal?: VoiceSignal;
  clinical_context?: PatientClinicalContext;
  record_context?: RecordContext;
};

export type Prescription = {
  id: string;
  patient_id: string;
  facility_id: string | null;
  referral_id: string | null;
  call_id: string | null;
  medication_name: string;
  dosage: string | null;
  route: string | null;
  frequency: string | null;
  duration: string | null;
  instructions: string | null;
  prescribed_by: string | null;
  status: string;
  prescribed_at: string;
};

// ------------------------------------------------------------
// Calls
// ------------------------------------------------------------

export async function getCallsForPatient(patientId: string): Promise<VoxeraCall[]> {
  const { data, error } = await supabase
    .from("calls")
    .select("*")
    .eq("patient_id", patientId)
    .order("created_at", { ascending: false });
  if (error) {
    console.error("getCallsForPatient", error);
    return [];
  }
  return (data ?? []) as unknown as VoxeraCall[];
}

export async function getCall(callId: string): Promise<VoxeraCall | null> {
  const { data, error } = await supabase
    .from("calls").select("*").eq("id", callId).maybeSingle();
  if (error) {
    console.error("getCall", error);
    return null;
  }
  return (data as unknown as VoxeraCall) ?? null;
}

export async function getConversation(callId: string): Promise<ConversationTurn[]> {
  const { data, error } = await supabase
    .from("conversation_turns")
    .select("id, call_id, speaker, message, sequence_number, created_at")
    .eq("call_id", callId)
    .order("sequence_number", { ascending: true });
  if (error) {
    console.error("getConversation", error);
    return [];
  }
  return (data ?? []) as ConversationTurn[];
}

// Summary: prefer the call_summaries table; fall back to a system turn whose
// message starts with "CALL_SUMMARY " and carries the JSON.
export async function getCallSummary(
  callId: string,
  turns?: ConversationTurn[]
): Promise<{ summary: CallSummary | null; text: string | null }> {
  try {
    const { data } = await supabase
      .from("call_summaries")
      .select("summary_json, summary_text")
      .eq("call_id", callId)
      .maybeSingle();
    if (data?.summary_json) {
      return {
        summary: data.summary_json as CallSummary,
        text: (data.summary_text as string) ?? null,
      };
    }
  } catch {
    /* table may not exist yet — use the fallback */
  }

  const list = turns ?? (await getConversation(callId));
  const sys = list
    .filter((t) => t.speaker === "system" && t.message.startsWith("CALL_SUMMARY "))
    .pop();
  if (sys) {
    try {
      return {
        summary: JSON.parse(sys.message.slice("CALL_SUMMARY ".length)) as CallSummary,
        text: null,
      };
    } catch {
      /* ignore */
    }
  }
  return { summary: null, text: null };
}

/**
 * What the patient has told Voxera across ALL their calls
 * (allergies, conditions, pregnancy, age, medications) — unverified,
 * patient-reported. Newest call wins for scalar fields.
 */
export async function getReportedProfile(callIds: string[]): Promise<{
  allergies: string[];
  conditions: string[];
  medications: string[];
  age?: number;
  pregnant?: boolean;
  isChild?: boolean;
  latest: CallSummary | null;
  latestCallId: string | null;
}> {
  const allergies = new Set<string>();
  const conditions = new Set<string>();
  const medications = new Set<string>();
  let age: number | undefined;
  let pregnant: boolean | undefined;
  let isChild: boolean | undefined;
  let latest: CallSummary | null = null;
  let latestCallId: string | null = null;

  for (const id of callIds) {
    const { summary } = await getCallSummary(id);
    if (!summary) continue;
    if (!latest) {
      latest = summary;
      latestCallId = id;
    }
    const p = summary.patient_profile ?? {};
    (p.allergies ?? []).forEach((a) => allergies.add(a));
    (p.conditions ?? []).forEach((c) => conditions.add(c));
    (summary.medications_mentioned ?? []).forEach((m) => medications.add(m));
    if (age === undefined && typeof p.age === "number") age = p.age;
    if (pregnant === undefined && p.pregnant) pregnant = true;
    if (isChild === undefined && p.is_child) isChild = true;
  }

  return {
    allergies: [...allergies],
    conditions: [...conditions],
    medications: [...medications],
    age,
    pregnant,
    isChild,
    latest,
    latestCallId,
  };
}

// ------------------------------------------------------------
// Prescriptions (clinician-only; table created by sql/2026_dashboard_v2.sql)
// ------------------------------------------------------------

export async function getPrescriptions(
  patientId: string
): Promise<{ rows: Prescription[]; tableMissing: boolean }> {
  const { data, error } = await supabase
    .from("prescriptions")
    .select("*")
    .eq("patient_id", patientId)
    .order("prescribed_at", { ascending: false });

  if (error) {
    const msg = `${error.code ?? ""} ${error.message ?? ""}`;
    const missing =
      /PGRST205|42P01|schema cache|does not exist|Could not find the table/i.test(msg);
    if (!missing) console.error("getPrescriptions", error);
    return { rows: [], tableMissing: missing };
  }
  return { rows: (data ?? []) as Prescription[], tableMissing: false };
}

// ------------------------------------------------------------
// Shared helpers
// ------------------------------------------------------------

export type PatientLite = { id: string; full_name: string; phone: string | null };

/** id -> {full_name, phone} for a set of patient ids (chunked). */
export async function getPatientsMap(ids: Array<string | null | undefined>): Promise<Record<string, PatientLite>> {
  const unique = [...new Set(ids.filter(Boolean) as string[])];
  const out: Record<string, PatientLite> = {};
  for (let i = 0; i < unique.length; i += 100) {
    const chunk = unique.slice(i, i + 100);
    const { data } = await supabase.from("patients").select("id, full_name, phone").in("id", chunk);
    (data ?? []).forEach((p) => { out[p.id as string] = p as PatientLite; });
  }
  return out;
}

/** "YYYY-MM-DD" for today in the hospital's timezone. */
export function todayISO(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

/** A call that is still in progress and started recently is "live". */
export function isLiveCall(c: Pick<VoxeraCall, "status" | "created_at">): boolean {
  return c.status === "in_progress" && Date.now() - new Date(c.created_at).getTime() < 20 * 60 * 1000;
}

export function isEmergencyCall(c: Pick<VoxeraCall, "outcome" | "emergency_checks_count">): boolean {
  return (c.outcome ?? "").includes("emergency") || (c.emergency_checks_count ?? 0) > 0;
}
