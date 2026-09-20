"use client";

import { useState } from "react";
import { deleteItem, PatientApiError, verifyItem } from "@/lib/patientApi";
import type { PrescriptionItem, Role } from "@/lib/patientTypes";
import { Badge, Callout } from "../ui";

const FIELDS = ["medicine_name", "strength", "route", "frequency", "duration", "instructions"] as const;
type Field = (typeof FIELDS)[number];
const LABEL: Record<Field, string> = {
  medicine_name: "Medicine", strength: "Strength", route: "Route", frequency: "Frequency",
  duration: "Duration", instructions: "Instructions",
};

const conf = (c: number | null): { text: string; tone: "success" | "warning" | "critical" } =>
  c === null ? { text: "unknown", tone: "warning" }
  : c >= 0.85 ? { text: "High", tone: "success" } : c >= 0.6 ? { text: "Medium", tone: "warning" } : { text: "Low", tone: "critical" };

function Row({ item, role, onDone }: { item: PrescriptionItem; role: Role | null; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<Field, string>>(() => Object.fromEntries(
    FIELDS.map((f) => [f, item[f] ?? ""])) as Record<Field, string>);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const canConfirm = role === "doctor";
  const canReject = role === "doctor" || role === "nurse";
  const c = conf(item.confidence);

  async function remove() {
    if (!window.confirm("Delete this extracted medicine? This can't be undone.")) return;
    setBusy(true); setError("");
    try { await deleteItem(item.id); onDone(); }
    catch (e) { setError(e instanceof PatientApiError ? e.message : "Could not delete."); }
    finally { setBusy(false); }
  }

  async function act(action: "confirm" | "reject") {
    setBusy(true); setError("");
    try {
      const edits = editing ? Object.fromEntries(FIELDS.map((f) => [f, draft[f].trim() || null])) : undefined;
      await verifyItem(item.id, action, edits);
      onDone();
    } catch (e) {
      setError(e instanceof PatientApiError ? e.message : "Could not save.");
    } finally { setBusy(false); }
  }

  return (
    <li className="px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-bold">{item.medicine_name ?? "Unreadable medicine"}</span>
        <Badge tone="warning">{item.verification_status === "needs_review" ? "Needs review" : "Pending verification"}</Badge>
        <Badge tone={c.tone}>Confidence: {c.text}{item.confidence !== null ? ` (${Math.round(item.confidence * 100)}%)` : ""}</Badge>
      </div>
      {editing ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {FIELDS.map((f) => (
            <label key={f} className="block">
              <span className="field-label">{LABEL[f]}</span>
              <input className="input" value={draft[f]} onChange={(e) => setDraft((d) => ({ ...d, [f]: e.target.value }))} />
            </label>
          ))}
        </div>
      ) : (
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
          {FIELDS.filter((f) => f !== "medicine_name").map((f) => (
            <div key={f}><dt className="eyebrow">{LABEL[f]}</dt>
              <dd className={item[f] ? "font-medium" : "text-faint"}>{item[f] ?? "not found in document"}</dd></div>
          ))}
        </dl>
      )}
      {item.warnings && item.warnings.length > 0 && (
        <p className="text-muted mt-2 text-xs">Notes: {item.warnings.map((w) => w.replaceAll("_", " ")).join(" · ")}</p>
      )}
      {item.raw_text && <p className="text-faint mt-1 text-xs">Read as: “{item.raw_text}”</p>}
      {error && <Callout tone="critical" className="mt-3">{error}</Callout>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn btn-success btn-sm" disabled={busy || !canConfirm} onClick={() => void act("confirm")}
                title={canConfirm ? "Confirm as a clinician-verified medication" : "Only a doctor can confirm"}>
          {editing ? "Save & confirm" : "Confirm"}
        </button>
        <button className="btn btn-sm" disabled={busy} onClick={() => setEditing((v) => !v)}>{editing ? "Cancel edit" : "Edit"}</button>
        <button className="btn btn-sm" disabled={busy || !canReject} onClick={() => void act("reject")}>Reject</button>
        <button className="btn btn-danger btn-sm" disabled={busy || !canReject} onClick={() => void remove()}>Delete</button>
        {!canConfirm && <span className="text-muted self-center text-xs">Only a doctor can confirm.</span>}
      </div>
    </li>
  );
}

/** OCR candidates. Nothing here is a prescription until a doctor confirms it. */
export default function PrescriptionReview({ items, role, onChanged }: { items: PrescriptionItem[]; role: Role | null; onChanged: () => void }) {
  const open = items.filter((i) => i.verification_status === "pending" || i.verification_status === "needs_review");
  if (open.length === 0) return null;
  return (
    <div className="card overflow-hidden">
      <div className="border-b px-4 py-3" style={{ borderColor: "var(--dashboard-border)" }}>
        <p className="eyebrow">Review extracted medicines</p>
        <p className="text-muted mt-0.5 text-xs">Extracted from an uploaded document. Not a prescription until a doctor confirms.</p>
      </div>
      <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
        {open.map((i) => <Row key={i.id} item={i} role={role} onDone={onChanged} />)}
      </ul>
    </div>
  );
}
