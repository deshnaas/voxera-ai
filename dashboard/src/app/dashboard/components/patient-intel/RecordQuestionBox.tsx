"use client";

import { useState } from "react";
import { askRecord, PatientApiError } from "@/lib/patientApi";
import type { AccessReason, RecordAnswer } from "@/lib/patientTypes";
import { fmtDate } from "@/lib/format";
import { Badge, Callout, SectionCard } from "../ui";

const EXAMPLES = [
  "What medicines are currently in the record?",
  "What was prescribed during the last consultation?",
  "What medicine is used for nebulization?",
  "Was the patient previously referred?",
];

const SRC_LABEL: Record<string, string> = {
  medication: "Medication record", consultation: "Consultation summary", referral: "Referral",
  appointment: "Appointment", patient: "Patient record", facility: "Facility record",
};

/** Answers are retrieved from records and labelled as AI-generated — never as clinician-authored. */
export default function RecordQuestionBox({ patientRef, reason, reasonText }: { patientRef: string; reason?: AccessReason; reasonText?: string }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [ans, setAns] = useState<RecordAnswer | null>(null);
  const [error, setError] = useState("");

  async function ask(text: string) {
    if (!text.trim()) return;
    setBusy(true); setError(""); setAns(null);
    try {
      setAns(await askRecord(patientRef, text.trim(), reason, reasonText));
    } catch (e) {
      setError(e instanceof PatientApiError ? (e.offline ? "The patient-records service is not running." : e.message) : "Could not answer.");
    } finally { setBusy(false); }
  }

  return (
    <SectionCard title="Ask about this patient's records" subtitle="Answers come only from what is stored in the record.">
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); void ask(q); }}>
        <input className="input" value={q} maxLength={300} onChange={(e) => setQ(e.target.value)}
               placeholder="e.g. What was prescribed during the last consultation?" aria-label="Question about the record" />
        <button className="btn btn-primary" disabled={busy || !q.trim()}>{busy ? "Searching…" : "Ask"}</button>
      </form>
      <div className="mt-2 flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button key={ex} type="button" className="btn btn-sm" style={{ borderRadius: 999 }} onClick={() => { setQ(ex); void ask(ex); }}>{ex}</button>
        ))}
      </div>
      {error && <Callout tone="critical" className="mt-4">{error}</Callout>}
      {ans && (
        <div className="mt-4 rounded-xl border p-4" style={{ borderColor: "var(--dashboard-border)", background: "var(--dashboard-surface-muted)" }}>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge tone="info">AI-generated from records</Badge>
            {ans.conflict && <Badge tone="warning">Conflicting records</Badge>}
            {!ans.found && <Badge>Not found</Badge>}
          </div>
          <p className="text-sm leading-6">{ans.answer || "That doesn't look like a question about the record."}</p>
          {ans.sources.length > 0 && (
            <div className="mt-3">
              <p className="eyebrow">Source</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {ans.sources.map((s, i) => (
                  <li key={`${s.id}-${i}`} className="text-muted">
                    {SRC_LABEL[s.type] ?? s.type}{s.date ? ` — ${fmtDate(s.date)}` : ""}{s.source ? ` · ${s.source.replace("_", " ")}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-faint mt-3 text-xs">This answer is a retrieval from the record, not a clinician’s note or advice.</p>
        </div>
      )}
    </SectionCard>
  );
}
