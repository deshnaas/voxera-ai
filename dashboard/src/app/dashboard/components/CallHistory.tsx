"use client";

import { useEffect, useState } from "react";
import {
  getCallsForPatient,
  getConversation,
  getCallSummary,
  humanDuration,
  type VoxeraCall,
  type ConversationTurn,
  type CallSummary,
} from "@/lib/voxera";

// ============================================================
// Voxera call history for one patient.
// Shows the REAL calls / conversation_turns / call summary that
// Voxera wrote — no fabricated data.
// ============================================================

export default function CallHistory({ patientId }: { patientId: string }) {
  const [calls, setCalls] = useState<VoxeraCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      const rows = await getCallsForPatient(patientId);
      if (!cancelled) {
        setCalls(rows);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  return (
    <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-8">
      <div>
        <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
          Voxera Voice Agent
        </p>
        <h2 className="mt-2 text-2xl font-bold">Calls &amp; Conversations</h2>
        <p className="dashboard-muted mt-2 text-sm">
          Every call this patient had with Voxera, its transcript, and the
          structured call summary.
        </p>
      </div>

      {loading ? (
        <p className="dashboard-muted mt-6 text-sm">Loading calls…</p>
      ) : calls.length === 0 ? (
        <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-6 text-center">
          <p className="font-semibold">No Voxera calls yet</p>
          <p className="dashboard-muted mt-2 text-sm">
            Calls will appear here after the patient speaks with Voxera.
          </p>
        </div>
      ) : (
        <div className="mt-6 space-y-4">
          {calls.map((call) => (
            <CallRow
              key={call.id}
              call={call}
              open={openId === call.id}
              onToggle={() =>
                setOpenId((cur) => (cur === call.id ? null : call.id))
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------

function CallRow({
  call,
  open,
  onToggle,
}: {
  call: VoxeraCall;
  open: boolean;
  onToggle: () => void;
}) {
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

  const emergency =
    (call.outcome ?? "").includes("emergency") ||
    (call.emergency_checks_count ?? 0) > 0;

  return (
    <div className="rounded-xl border border-gray-300 p-5">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full flex-col gap-3 text-left sm:flex-row sm:items-center sm:justify-between"
      >
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-bold">
              {new Date(call.created_at).toLocaleString()}
            </span>
            <span className="rounded-full border border-gray-400 px-2.5 py-0.5 text-xs font-semibold uppercase">
              {call.call_type ?? "call"}
            </span>
            {emergency && (
              <span className="rounded-full border-2 border-red-500 px-2.5 py-0.5 text-xs font-bold uppercase text-red-600">
                Emergency
              </span>
            )}
            <span className="rounded-full border border-[#D4AF37] px-2.5 py-0.5 text-xs font-semibold">
              {call.status ?? "—"}
            </span>
          </div>
          <p className="dashboard-muted mt-2 text-sm">
            {call.outcome ?? "no outcome recorded"} ·{" "}
            {humanDuration(call.call_duration_seconds)} ·{" "}
            {call.ai_response_count ?? 0} AI replies ·{" "}
            {call.interruption_count ?? 0} interruptions
          </p>
        </div>
        <span className="dashboard-muted text-sm font-semibold">
          {open ? "Hide ▲" : "View ▼"}
        </span>
      </button>

      {open && (
        <div className="mt-5 border-t border-gray-300 pt-5">
          {!loaded ? (
            <p className="dashboard-muted text-sm">Loading conversation…</p>
          ) : (
            <>
              {summary && <SummaryCard summary={summary} text={summaryText} />}
              <Transcript turns={turns ?? []} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------

function SummaryCard({
  summary,
  text,
}: {
  summary: CallSummary;
  text: string | null;
}) {
  const es = summary.emergency_status;
  return (
    <div className="mb-5 rounded-xl border-2 border-[#D4AF37] bg-[#faf9f1] p-5 text-black">
      <p className="text-xs font-bold uppercase tracking-widest text-[#9a7b13]">
        Call Summary
      </p>

      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        <Field label="Chief concern" value={summary.chief_concern ?? "—"} />
        <Field
          label="Symptoms"
          value={
            (summary.symptoms ?? []).length
              ? (summary.symptoms ?? []).join(", ")
              : "none explicitly reported"
          }
        />
        {summary.duration && (
          <Field label="Duration" value={summary.duration} />
        )}
        {summary.temperature_f && (
          <Field
            label="Temperature"
            value={`${summary.temperature_f} °F (patient-reported)`}
          />
        )}
      </div>

      <div className="mt-4 rounded-lg border border-gray-300 bg-white p-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">
          Emergency
        </p>
        <p className="mt-1 text-sm">
          {es?.detected
            ? `DETECTED — ${es.category} (${es.severity}). Trigger: “${es.trigger_phrase}”.` +
              (es.recommended_department
                ? ` Dept: ${es.recommended_department}.`
                : "")
            : "No emergency signal detected."}
        </p>
      </div>

      {(summary.care_given ?? []).length > 0 && (
        <div className="mt-3 rounded-lg border border-gray-300 bg-white p-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">
            Care provided
          </p>
          <ul className="mt-1 space-y-2 text-sm">
            {(summary.care_given ?? []).map((c, i) => (
              <li key={i}>
                <span className="font-semibold">✓ {c.label}</span>
                {c.steps?.length > 0 && (
                  <ul className="ml-4 mt-1 list-disc text-xs text-gray-600">
                    {c.steps.slice(0, 3).map((s, j) => (
                      <li key={j}>{s}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 rounded-lg border border-gray-300 bg-white p-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">
          Medication guidance — OTC only, NOT a prescription
        </p>
        {(summary.otc_guidance ?? []).length === 0 ? (
          <p className="mt-1 text-sm">None.</p>
        ) : (
          <ul className="mt-1 space-y-1 text-sm">
            {(summary.otc_guidance ?? []).map((o, i) => (
              <li key={i}>
                {o.deferred ? (
                  <span>
                    Deferred to a pharmacist / clinician (no medicine suggested).
                  </span>
                ) : (
                  <span>
                    <span className="font-semibold">
                      {(o.items ?? []).join(", ") || "supportive care"}
                    </span>{" "}
                    — {o.safety}
                  </span>
                )}
                {o.spoken && (
                  <span className="block text-xs text-gray-500">
                    “{o.spoken}”
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {summary.follow_up && (
        <div className="mt-3 rounded-lg border border-gray-300 bg-white p-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">
            Follow-up
          </p>
          <p className="mt-1 text-sm">
            {String(
              summary.follow_up["note"] ??
                `${summary.follow_up["type"] ?? ""} ${
                  summary.follow_up["date"] ?? ""
                } ${summary.follow_up["status"] ?? ""}`
            ).trim()}
          </p>
        </div>
      )}

      {text && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-semibold text-[#9a7b13]">
            Plain-text summary
          </summary>
          <pre className="mt-2 whitespace-pre-wrap rounded bg-white p-3 text-xs text-gray-700">
            {text}
          </pre>
        </details>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">
        {label}
      </p>
      <p className="mt-1 text-sm font-medium">{value}</p>
    </div>
  );
}

// ------------------------------------------------------------

function Transcript({ turns }: { turns: ConversationTurn[] }) {
  const spoken = turns.filter(
    (t) => t.speaker === "patient" || t.speaker === "ai"
  );
  if (spoken.length === 0) {
    return (
      <p className="dashboard-muted text-sm">No conversation turns recorded.</p>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">
        Conversation
      </p>
      {spoken.map((t) => (
        <div
          key={t.id}
          className={`rounded-lg border p-3 text-sm ${
            t.speaker === "patient"
              ? "border-gray-300 bg-white"
              : "border-[#D4AF37] bg-[#faf9f1]"
          }`}
        >
          <p className="text-xs font-bold uppercase tracking-wider text-gray-500">
            {t.speaker === "patient" ? "Patient" : "Voxera"}
          </p>
          <p className="mt-1">{t.message}</p>
        </div>
      ))}
    </div>
  );
}
