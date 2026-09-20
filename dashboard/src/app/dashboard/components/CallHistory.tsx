"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getCallsForPatient, getConversation, getCallSummary, humanDuration, isEmergencyCall, isLiveCall,
  type VoxeraCall, type ConversationTurn, type CallSummary,
} from "@/lib/voxera";
import { fmtDateTime } from "@/lib/format";
import { Badge, EmptyState, LoadingRows } from "./ui";
import VoiceSignalBadge from "./patient-intel/VoiceSignalBadge";

// Real calls / conversation_turns / call summaries written by Voxera.

export function CallStatusBadges({ call }: { call: VoxeraCall }) {
  return (
    <>
      {isLiveCall(call) && <span className="badge badge-info"><span className="dot dot-live" /> LIVE</span>}
      {isEmergencyCall(call) && <Badge tone="critical">Emergency</Badge>}
      <Badge>{call.call_type ?? "call"}</Badge>
    </>
  );
}

export default function CallHistory({ patientId }: { patientId: string }) {
  const [calls, setCalls] = useState<VoxeraCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const rows = await getCallsForPatient(patientId);
      if (!cancelled) {
        setCalls(rows);
        setLoading(false);
        if (rows[0]) setOpenId(rows[0].id);
      }
    })();
    return () => { cancelled = true; };
  }, [patientId]);

  if (loading) return <LoadingRows rows={2} />;
  if (calls.length === 0)
    return <EmptyState icon="☎" title="No Voxera calls yet" hint="Every call — routine or emergency — is stored here once the patient speaks with Voxera." />;

  return (
    <div className="space-y-3">
      {calls.map((call) => (
        <CallRow key={call.id} call={call} open={openId === call.id}
          onToggle={() => setOpenId((cur) => (cur === call.id ? null : call.id))} />
      ))}
    </div>
  );
}

function CallRow({ call, open, onToggle }: { call: VoxeraCall; open: boolean; onToggle: () => void }) {
  const [turns, setTurns] = useState<ConversationTurn[] | null>(null);
  const [summary, setSummary] = useState<CallSummary | null>(null);
  const [summaryText, setSummaryText] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!open || loaded) return;
    (async () => {
      const t = await getConversation(call.id);
      setTurns(t);
      const s = await getCallSummary(call.id, t);
      setSummary(s.summary);
      setSummaryText(s.text);
      setLoaded(true);
    })();
  }, [open, loaded, call.id]);

  return (
    <div className="card overflow-hidden">
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="flex w-full flex-col gap-2 p-4 text-left sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-bold">{fmtDateTime(call.created_at)}</span>
            <CallStatusBadges call={call} />
          </div>
          <p className="text-muted mt-1.5 text-sm">
            {call.outcome ?? "no outcome recorded"} · {humanDuration(call.call_duration_seconds)} ·{" "}
            {call.ai_response_count ?? 0} AI replies · {call.interruption_count ?? 0} interruptions
          </p>
        </div>
        <span className="text-muted shrink-0 text-sm font-semibold">{open ? "Hide ▲" : "View ▼"}</span>
      </button>

      {open && (
        <div className="border-t p-4" style={{ borderColor: "var(--dashboard-border)" }}>
          {!loaded ? <LoadingRows rows={2} /> : (
            <div className="space-y-4">
              {summary && <SummaryCard summary={summary} text={summaryText} />}
              <Transcript turns={turns ?? []} />
              <Link href={`/dashboard/calls/${call.id}`} className="btn btn-sm">Open full call page</Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------

function Block({ title, tone, children }: { title: string; tone?: "critical"; children: React.ReactNode }) {
  return (
    <div className={`rounded-lg border p-3 ${tone === "critical" ? "callout-critical" : ""}`}
         style={tone ? undefined : { borderColor: "var(--dashboard-border)", background: "var(--dashboard-surface)" }}>
      <p className="eyebrow" style={tone ? { color: "inherit" } : undefined}>{title}</p>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  );
}

export function SummaryCard({ summary, text }: { summary: CallSummary; text: string | null }) {
  const es = summary.emergency_status;
  return (
    <div className="rounded-xl border p-4" style={{ borderColor: "var(--dashboard-gold-border)", background: "var(--brand-soft)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="eyebrow" style={{ color: "var(--brand-ink)" }}>Call summary</p>
        <VoiceSignalBadge signal={summary.voice_signal} detailed />
      </div>
      {summary.clinical_context?.conclusion && (
        <p className="text-muted mt-2 text-xs">Triage conclusion: {summary.clinical_context.conclusion.replaceAll("_", " ").toLowerCase()}</p>
      )}

      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        <F label="Chief concern" value={summary.chief_concern ?? "—"} />
        <F label="Symptoms" value={(summary.symptoms ?? []).length ? (summary.symptoms ?? []).join(", ") : "none explicitly reported"} />
        {summary.duration && <F label="Duration" value={summary.duration} />}
        {summary.temperature_f && <F label="Temperature" value={`${summary.temperature_f} °F (patient-reported)`} />}
      </div>

      <div className="mt-4 space-y-3">
        <Block title="Emergency" tone={es?.detected ? "critical" : undefined}>
          {es?.detected
            ? <>DETECTED — {es.category} ({es.severity}). Trigger: “{es.trigger_phrase}”.{es.recommended_department ? ` Dept: ${es.recommended_department}.` : ""}</>
            : "No emergency signal detected."}
        </Block>

        {(summary.care_given ?? []).length > 0 && (
          <Block title="Care Voxera provided">
            <ul className="space-y-2">
              {(summary.care_given ?? []).map((c, i) => (
                <li key={i}>
                  <span className="font-semibold">✓ {c.label}</span>
                  {c.steps?.length > 0 && (
                    <ul className="text-muted ml-4 mt-1 list-disc text-xs">
                      {c.steps.slice(0, 3).map((s, j) => <li key={j}>{s}</li>)}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </Block>
        )}

        <Block title="Medication guidance from Voxera — OTC only, NOT a prescription">
          {(summary.otc_guidance ?? []).length === 0 ? "None." : (
            <ul className="space-y-1">
              {(summary.otc_guidance ?? []).map((o, i) => (
                <li key={i}>
                  {o.deferred
                    ? <span>Deferred to a pharmacist / clinician (no medicine suggested).</span>
                    : <span><b>{(o.items ?? []).join(", ") || "supportive care"}</b> — {o.safety}</span>}
                  {o.spoken && <span className="text-muted block text-xs">“{o.spoken}”</span>}
                </li>
              ))}
            </ul>
          )}
        </Block>

        {summary.follow_up && (
          <Block title="Follow-up">
            {String(summary.follow_up["note"] ?? `${summary.follow_up["type"] ?? ""} ${summary.follow_up["date"] ?? ""} ${summary.follow_up["status"] ?? ""}`).trim()}
          </Block>
        )}
      </div>

      {text && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-semibold" style={{ color: "var(--brand-ink)" }}>Plain-text summary</summary>
          <pre className="mt-2 whitespace-pre-wrap rounded p-3 text-xs" style={{ background: "var(--dashboard-surface)" }}>{text}</pre>
        </details>
      )}
    </div>
  );
}

function F({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className="mt-1 text-sm font-medium">{value}</p>
    </div>
  );
}

export function Transcript({ turns }: { turns: ConversationTurn[] }) {
  const spoken = turns.filter((t) => t.speaker === "patient" || t.speaker === "ai");
  if (spoken.length === 0) return <p className="text-muted text-sm">No conversation turns recorded.</p>;
  return (
    <div>
      <p className="eyebrow mb-2">Conversation</p>
      <div className="space-y-2">
        {spoken.map((t) => {
          const patient = t.speaker === "patient";
          return (
            <div key={t.id} className={`flex ${patient ? "justify-start" : "justify-end"}`}>
              <div className="max-w-[85%] rounded-xl border px-3 py-2 text-sm"
                   style={patient
                     ? { borderColor: "var(--dashboard-border)", background: "var(--dashboard-surface)" }
                     : { borderColor: "var(--dashboard-gold-border)", background: "var(--brand-soft)" }}>
                <p className="eyebrow">{patient ? "Patient" : "Voxera"}</p>
                <p className="mt-0.5">{t.message}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
