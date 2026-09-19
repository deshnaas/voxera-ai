"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabase";
import type { Patient } from "@/lib/voxera";
import { Callout, Field, Modal } from "./ui";

const GENDERS = ["", "female", "male", "other"];
const BLOOD = ["", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];

type Reported = { allergies: string[]; conditions: string[] };

export default function EditPatientModal({
  open, onClose, patient, reported, onSaved,
}: {
  open: boolean; onClose: () => void; patient: Patient; reported: Reported; onSaved: () => void;
}) {
  // Clinical columns exist only after sql/2026_dashboard_v2.sql
  const hasClinical = "allergies" in patient;
  const [f, setF] = useState(() => ({
    full_name: patient.full_name ?? "",
    phone: patient.phone ?? "",
    date_of_birth: patient.date_of_birth ?? "",
    gender: patient.gender ?? "",
    preferred_language: patient.preferred_language ?? "",
    village_or_locality: patient.village_or_locality ?? "",
    district: patient.district ?? "",
    blood_group: patient.blood_group ?? "",
    allergies: patient.allergies ?? "",
    chronic_conditions: patient.chronic_conditions ?? "",
    emergency_contact_name: patient.emergency_contact_name ?? "",
    emergency_contact_phone: patient.emergency_contact_phone ?? "",
    clinical_notes: patient.clinical_notes ?? "",
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF((p) => ({ ...p, [k]: e.target.value }));

  async function save() {
    if (!f.full_name.trim()) { setError("Name is required."); return; }
    setSaving(true); setError("");
    const nn = (v: string) => (v.trim() === "" ? null : v.trim());
    const update: Record<string, unknown> = {
      full_name: f.full_name.trim(),
      phone: nn(f.phone),
      date_of_birth: nn(f.date_of_birth),
      gender: nn(f.gender),
      preferred_language: nn(f.preferred_language),
      village_or_locality: nn(f.village_or_locality),
      district: nn(f.district),
    };
    if (hasClinical) {
      Object.assign(update, {
        blood_group: nn(f.blood_group),
        allergies: nn(f.allergies),
        chronic_conditions: nn(f.chronic_conditions),
        emergency_contact_name: nn(f.emergency_contact_name),
        emergency_contact_phone: nn(f.emergency_contact_phone),
        clinical_notes: nn(f.clinical_notes),
      });
    }
    const { data, error: err } = await supabase.from("patients").update(update).eq("id", patient.id).select("id");
    setSaving(false);
    if (err) { setError(err.message); return; }
    if (!data || data.length === 0) {
      setError("Saving was blocked — your account isn't allowed to edit patients yet. Run sql/2026_dashboard_v2.sql once in the Supabase SQL Editor.");
      return;
    }
    onSaved();
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose} title="Edit patient details" width={720}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save changes"}</button>
      </>}>
      <div className="space-y-5">
        {error && <Callout tone="critical">{error}</Callout>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Full name *"><input className="input" value={f.full_name} onChange={set("full_name")} /></Field>
          <Field label="Phone"><input className="input" inputMode="tel" value={f.phone} onChange={set("phone")} /></Field>
          <Field label="Date of birth"><input className="input" type="date" value={f.date_of_birth} onChange={set("date_of_birth")} /></Field>
          <Field label="Gender">
            <select className="select" value={f.gender} onChange={set("gender")}>
              {GENDERS.map((g) => <option key={g} value={g}>{g || "Not specified"}</option>)}
            </select>
          </Field>
          <Field label="Preferred language"><input className="input" value={f.preferred_language} onChange={set("preferred_language")} /></Field>
          <Field label="Locality / village"><input className="input" value={f.village_or_locality} onChange={set("village_or_locality")} /></Field>
          <Field label="District"><input className="input" value={f.district} onChange={set("district")} /></Field>
        </div>

        {hasClinical ? (
          <div className="space-y-4 border-t pt-5" style={{ borderColor: "var(--dashboard-border)" }}>
            <p className="eyebrow">Clinical information (verified by staff)</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Blood group">
                <select className="select" value={f.blood_group} onChange={set("blood_group")}>
                  {BLOOD.map((b) => <option key={b} value={b}>{b || "Unknown"}</option>)}
                </select>
              </Field>
              <span />
              <Field label="Allergies" hint={reported.allergies.length ? `Patient told Voxera: ${reported.allergies.join(", ")}` : undefined}>
                <textarea className="textarea" rows={2} value={f.allergies} onChange={set("allergies")} />
              </Field>
              <Field label="Chronic conditions" hint={reported.conditions.length ? `Patient told Voxera: ${reported.conditions.join(", ")}` : undefined}>
                <textarea className="textarea" rows={2} value={f.chronic_conditions} onChange={set("chronic_conditions")} />
              </Field>
              <Field label="Emergency contact name"><input className="input" value={f.emergency_contact_name} onChange={set("emergency_contact_name")} /></Field>
              <Field label="Emergency contact phone"><input className="input" inputMode="tel" value={f.emergency_contact_phone} onChange={set("emergency_contact_phone")} /></Field>
            </div>
            <Field label="Clinical notes"><textarea className="textarea" rows={3} value={f.clinical_notes} onChange={set("clinical_notes")} /></Field>
          </div>
        ) : (
          <Callout tone="info">
            Allergies, blood group, chronic conditions and emergency contact can be recorded after you run
            <code> sql/2026_dashboard_v2.sql</code> in the Supabase SQL Editor.
          </Callout>
        )}
      </div>
    </Modal>
  );
}
