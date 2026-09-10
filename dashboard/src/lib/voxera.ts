// ============================================================
// Voxera <-> dashboard read helpers
// ============================================================
// The dashboard reads the SAME Supabase records Voxera writes:
//   calls, conversation_turns, call_summaries (or the system-turn fallback).
// No mock data.

import { supabase } from "@/lib/supabase";

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
  patient_profile?: Record<string, unknown>;
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
};

// ------------------------------------------------------------

export async function getCallsForPatient(
  patientId: string
): Promise<VoxeraCall[]> {
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

export async function getRecentCalls(limit = 25): Promise<VoxeraCall[]> {
  const { data, error } = await supabase
    .from("calls")
    .select(
      "id, patient_id, facility_id, call_type, status, outcome, language, " +
        "call_duration_seconds, ai_response_count, interruption_count, " +
        "emergency_checks_count, call_success, started_at, ended_at, created_at"
    )
    .order("created_at", { ascending: false })
    .limit(limit);

  if (error) {
    console.error("getRecentCalls", error);
    return [];
  }
  return (data ?? []) as unknown as VoxeraCall[];
}

export async function getConversation(
  callId: string
): Promise<ConversationTurn[]> {
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
    // table may not exist yet — use the fallback
  }

  const list = turns ?? (await getConversation(callId));
  const sys = list
    .filter(
      (t) => t.speaker === "system" && t.message.startsWith("CALL_SUMMARY ")
    )
    .pop();

  if (sys) {
    try {
      const json = sys.message.slice("CALL_SUMMARY ".length);
      return { summary: JSON.parse(json) as CallSummary, text: null };
    } catch {
      // ignore
    }
  }
  return { summary: null, text: null };
}

export function humanDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}
