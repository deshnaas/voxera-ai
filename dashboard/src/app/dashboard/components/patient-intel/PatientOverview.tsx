"use client";

import { useState } from "react";
import type { PatientRecord } from "@/lib/patientTypes";
import { Callout, InfoItem, SectionCard } from "../ui";

export function PatientIdChip({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button type="button" className="btn btn-sm font-mono" title="Copy patient ID"
            onClick={() => { void navigator.clipboard?.writeText(id); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
      {id} {copied ? "✓ copied" : ""}
    </button>
  );
}

/** Identity + allergies, limited to whatever the server granted this user. */
export default function PatientOverview({ record }: { record: PatientRecord }) {
  const p = record.patient;
  const external = record.relationship === "external";
  return (
    <SectionCard title={external ? "External patient record" : "Patient details"}>
      {external && (
        <Callout tone="warning" className="mb-4">{record.label}. Essential information only. This access is recorded.</Callout>
      )}
      {record.allergies && record.allergies.allergies.length > 0 && (
        <Callout tone="warning" className="mb-4"><b>Allergies:</b> {record.allergies.allergies.join(", ")}</Callout>
      )}
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        <InfoItem label="Patient ID" value={<PatientIdChip id={p.patient_id} />} />
        <InfoItem label="Name" value={p.full_name} />
        <InfoItem label="Age" value={p.age ?? "—"} />
        <InfoItem label="Gender" value={p.gender ?? "—"} />
        {!external && <InfoItem label="Phone" value={p.phone ?? "—"} />}
        {record.allergies?.blood_group !== undefined && <InfoItem label="Blood group" value={record.allergies.blood_group ?? "—"} />}
        {record.conditions && record.conditions.length > 0 && <InfoItem label="Conditions" value={record.conditions.join(", ")} />}
      </div>
    </SectionCard>
  );
}
