"use client";

import { useState } from "react";
import { removeMedication } from "@/lib/patientApi";
import type { PatientMedication, Role } from "@/lib/patientTypes";
import { fmtDate } from "@/lib/format";
import { Badge, EmptyState } from "../ui";
import ConfirmRemove from "./ConfirmRemove";

function tone(m: PatientMedication): "success" | "warning" | "neutral" | "critical" {
  if (m.status === "rejected") return "critical";
  if (m.source === "clinician" || m.source === "ocr_verified") return "success";
  if (m.source === "ocr") return "warning";
  return "neutral";
}

function line(m: PatientMedication): string {
  return [m.strength, m.route, m.frequency, m.duration && `for ${m.duration}`].filter(Boolean).join(" · ");
}

type Ctl = { canRemove: boolean; onRemove: (m: PatientMedication) => void };

function Row({ m, ctl }: { m: PatientMedication; ctl: Ctl }) {
  const removable = ctl.canRemove && (m.origin === "patient_medications" || m.origin === "prescriptions");
  return (
    <li className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <p className="font-bold">{m.medicine_name ?? "Unnamed medicine"}
          {m.generic_name ? <span className="text-muted font-normal"> ({m.generic_name})</span> : null}
        </p>
        {line(m) && <p className="text-sm">{line(m)}</p>}
        {m.instructions && <p className="text-muted text-sm">{m.instructions}</p>}
        <p className="text-faint mt-1 text-xs">{m.provenance_label} · {fmtDate(m.date)}</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Badge tone={tone(m)}>{m.source === "ocr" ? "Pending verification" : m.source === "patient_reported" ? "Patient reported" : m.source === "ocr_verified" ? "Verified from document" : m.source === "clinician" ? "Clinician" : "Imported"}</Badge>
        <Badge>{m.status.replace("_", " ")}</Badge>
        {removable && <button className="btn btn-danger btn-sm" onClick={() => ctl.onRemove(m)}>Delete</button>}
      </div>
    </li>
  );
}

function Group({ title, hint, rows, ctl }: { title: string; hint?: string; rows: PatientMedication[]; ctl: Ctl }) {
  if (rows.length === 0) return null;
  return (
    <div className="card overflow-hidden">
      <div className="border-b px-4 py-3" style={{ borderColor: "var(--dashboard-border)" }}>
        <p className="eyebrow">{title}</p>
        {hint && <p className="text-muted mt-0.5 text-xs">{hint}</p>}
      </div>
      <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
        {rows.map((m) => <Row key={`${m.origin}-${m.id}`} m={m} ctl={ctl} />)}
      </ul>
    </div>
  );
}

/** Every medicine shows WHERE it came from. Extracted/unverified items are never styled as prescribed.
 *  Doctors can delete a medicine that was confirmed or prescribed by mistake. */
export default function MedicationList({
  meds, patientRef, role, onChanged,
}: { meds: PatientMedication[]; patientRef?: string; role?: Role | null; onChanged?: () => void }) {
  const [target, setTarget] = useState<PatientMedication | null>(null);
  const ctl: Ctl = { canRemove: !!patientRef && role === "doctor", onRemove: setTarget };

  const active = meds.filter((m) => m.is_current);
  const pending = meds.filter((m) => m.source === "ocr" && (m.status === "pending" || m.status === "needs_review"));
  const reported = meds.filter((m) => m.source === "patient_reported" && m.status !== "rejected");
  const past = meds.filter((m) => !active.includes(m) && !pending.includes(m) && !reported.includes(m));

  if (meds.length === 0) {
    return <div className="card"><EmptyState icon="℞" title="No medications on record" hint="Verified prescriptions and patient-reported medicines appear here." /></div>;
  }
  return (
    <div className="space-y-4">
      <Group title="Active — clinician confirmed" rows={active} ctl={ctl} />
      <Group title="Pending verification" hint="Extracted from an uploaded prescription. Not confirmed by a clinician yet. Review it in the Prescriptions tab." rows={pending} ctl={ctl} />
      <Group title="Reported by the patient" hint="Told to Voxera on a call. Not verified." rows={reported} ctl={ctl} />
      <Group title="Past, stopped or rejected" rows={past} ctl={ctl} />

      <ConfirmRemove open={!!target} title="Delete this medication?" onClose={() => setTarget(null)}
        onConfirm={async () => {
          if (!target || !patientRef) return;
          await removeMedication(patientRef, target.id, target.origin as "patient_medications" | "prescriptions");
          onChanged?.();
        }}>
        <p><b>{target?.medicine_name}</b> {target?.strength} will be removed from this patient&apos;s active record and from record answers.</p>
        <p className="text-muted">Use this for a medicine that was entered or confirmed by mistake. The removal is logged with your name.</p>
      </ConfirmRemove>
    </div>
  );
}
