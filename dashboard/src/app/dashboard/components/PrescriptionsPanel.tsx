"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { useStaff } from "@/lib/staff";
import type { Prescription } from "@/lib/voxera";
import { fmtDateTime } from "@/lib/format";
import { Badge, Callout, EmptyState, Field, Modal } from "./ui";
import Icon from "./Icon";

const ROUTES = ["Oral", "Topical", "Inhaled", "Injection (IM)", "Injection (IV)", "Sublingual", "Eye/Ear drops", "Other"];

type Props = {
  patientId: string;
  rows: Prescription[];
  tableMissing: boolean;
  onChanged: () => void;
  /** Allergies / conditions shown before prescribing (clinician-verified + patient-reported). */
  allergies: string[];
  conditions: string[];
  pregnant?: boolean;
  referralId?: string | null;
};

const blank = { medication_name: "", dosage: "", route: "Oral", frequency: "", duration: "", instructions: "" };

export default function PrescriptionsPanel({ patientId, rows, tableMissing, onChanged, allergies, conditions, pregnant, referralId }: Props) {
  const { user, facilityId } = useStaff();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ ...blank });
  const [prescriber, setPrescriber] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [ack, setAck] = useState(false);

  const hasWarnings = allergies.length > 0 || conditions.length > 0 || !!pregnant;
  const set = (k: keyof typeof blank) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  function openForm() {
    setForm({ ...blank });
    setPrescriber(user?.email ?? "");
    setError("");
    setAck(false);
    setOpen(true);
  }

  async function save() {
    if (!form.medication_name.trim()) { setError("Medication name is required."); return; }
    if (!form.dosage.trim() || !form.frequency.trim()) { setError("Enter the dose and how often it is taken."); return; }
    if (hasWarnings && !ack) { setError("Please confirm you have checked the allergy / condition warnings above."); return; }
    setSaving(true); setError("");
    const { error: err } = await supabase.from("prescriptions").insert({
      patient_id: patientId,
      facility_id: facilityId,
      referral_id: referralId ?? null,
      medication_name: form.medication_name.trim(),
      dosage: form.dosage.trim(),
      route: form.route || null,
      frequency: form.frequency.trim(),
      duration: form.duration.trim() || null,
      instructions: form.instructions.trim() || null,
      prescribed_by: prescriber.trim() || user?.email || null,
      prescribed_by_user: user?.id ?? null,
      source: "clinician",
      status: "active",
    });
    setSaving(false);
    if (err) {
      setError(
        /row-level security|permission|42501/i.test(err.message)
          ? "Your account isn't allowed to add prescriptions yet. Run sql/2026_dashboard_v2.sql once in the Supabase SQL Editor."
          : err.message
      );
      return;
    }
    setOpen(false);
    onChanged();
  }

  async function setStatus(p: Prescription, status: "active" | "stopped" | "completed") {
    const { error: err } = await supabase.from("prescriptions").update({ status }).eq("id", p.id);
    if (err) alert("Could not update: " + err.message);
    else onChanged();
  }

  const active = rows.filter((r) => r.status === "active");
  const past = rows.filter((r) => r.status !== "active");

  return (
    <div className="space-y-4">
      <Callout tone="info">
        <b>Clinician prescriptions</b> are entered by hospital staff only. Voxera never prescribes and gives no doses — its
        over-the-counter guidance appears separately under Calls, and patient-reported medicines under Overview.
      </Callout>

      {tableMissing ? (
        <Callout tone="warning">
          Prescriptions aren’t set up in the database yet. Run <code>sql/2026_dashboard_v2.sql</code> once in the Supabase
          SQL Editor, then refresh this page.
        </Callout>
      ) : (
        <div className="flex items-center justify-between gap-3">
          <p className="text-muted text-sm">{active.length} active · {past.length} past</p>
          <button className="btn btn-primary" onClick={openForm}><Icon name="plus" size={16} /> Add prescription</button>
        </div>
      )}

      {!tableMissing && rows.length === 0 && (
        <div className="card"><EmptyState icon="℞" title="No prescriptions yet" hint="Prescriptions you add here are saved to this patient’s record." /></div>
      )}

      {active.length > 0 && <RxList title="Active" rows={active} onStatus={setStatus} />}
      {past.length > 0 && <RxList title="Past" rows={past} onStatus={setStatus} />}

      <Modal open={open} onClose={() => setOpen(false)} title="Add prescription"
        footer={<>
          <button className="btn" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save prescription"}</button>
        </>}>
        <div className="space-y-4">
          {hasWarnings && (
            <Callout tone="warning">
              <p className="font-bold">Check before prescribing</p>
              <ul className="mt-1 list-disc pl-5 text-sm">
                {allergies.length > 0 && <li>Allergies: {allergies.join(", ")}</li>}
                {conditions.length > 0 && <li>Conditions: {conditions.join(", ")}</li>}
                {pregnant && <li>Patient reported being pregnant</li>}
              </ul>
              <label className="mt-2 flex items-center gap-2 text-sm font-semibold">
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have checked these
              </label>
            </Callout>
          )}
          {error && <Callout tone="critical">{error}</Callout>}
          <Field label="Medication *"><input className="input" autoFocus value={form.medication_name} onChange={set("medication_name")} placeholder="e.g. Amoxicillin" /></Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Dose *"><input className="input" value={form.dosage} onChange={set("dosage")} placeholder="e.g. 500 mg" /></Field>
            <Field label="Route">
              <select className="select" value={form.route} onChange={set("route")}>{ROUTES.map((r) => <option key={r}>{r}</option>)}</select>
            </Field>
            <Field label="Frequency *"><input className="input" value={form.frequency} onChange={set("frequency")} placeholder="e.g. Twice daily" /></Field>
            <Field label="Duration"><input className="input" value={form.duration} onChange={set("duration")} placeholder="e.g. 5 days" /></Field>
          </div>
          <Field label="Instructions"><textarea className="textarea" rows={3} value={form.instructions} onChange={set("instructions")} placeholder="e.g. Take after food" /></Field>
          <Field label="Prescribed by"><input className="input" value={prescriber} onChange={(e) => setPrescriber(e.target.value)} /></Field>
        </div>
      </Modal>
    </div>
  );
}

function RxList({ title, rows, onStatus }: { title: string; rows: Prescription[]; onStatus: (p: Prescription, s: "active" | "stopped" | "completed") => void }) {
  return (
    <div className="card overflow-hidden">
      <div className="border-b px-4 py-3" style={{ borderColor: "var(--dashboard-border)" }}><p className="eyebrow">{title}</p></div>
      <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
        {rows.map((p) => (
          <li key={p.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-bold">{p.medication_name}</span>
                <Badge tone={p.status === "active" ? "success" : undefined}>{p.status}</Badge>
              </div>
              <p className="mt-0.5 text-sm">
                {[p.dosage, p.route, p.frequency, p.duration && `for ${p.duration}`].filter(Boolean).join(" · ")}
              </p>
              {p.instructions && <p className="text-muted mt-0.5 text-sm">{p.instructions}</p>}
              <p className="text-faint mt-1 text-xs">{fmtDateTime(p.prescribed_at)}{p.prescribed_by ? ` · ${p.prescribed_by}` : ""}</p>
            </div>
            <div className="flex shrink-0 gap-2">
              {p.status === "active" ? (
                <>
                  <button className="btn btn-sm" onClick={() => onStatus(p, "completed")}>Completed</button>
                  <button className="btn btn-sm" onClick={() => onStatus(p, "stopped")}>Stop</button>
                </>
              ) : (
                <button className="btn btn-sm" onClick={() => onStatus(p, "active")}>Reactivate</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
