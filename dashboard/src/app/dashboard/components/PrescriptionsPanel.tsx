"use client";

import { useState } from "react";
import { removeMedication } from "@/lib/patientApi";
import type { Role } from "@/lib/patientTypes";
import type { Prescription } from "@/lib/voxera";
import { fmtDateTime } from "@/lib/format";
import { Badge } from "./ui";
import ConfirmRemove from "./patient-intel/ConfirmRemove";

/** Existing clinician-entered prescriptions. Prescriptions now come in by uploading a document and having a
 *  doctor confirm it, so there is no manual "add" here; a wrongly entered one can be deleted by a doctor. */
export default function PrescriptionsPanel({
  patientRef, rows, role, onChanged,
}: { patientRef: string; rows: Prescription[]; role?: Role | null; onChanged: () => void }) {
  const [target, setTarget] = useState<Prescription | null>(null);
  const live = rows.filter((r) => r.status !== "entered_in_error");
  if (live.length === 0) return null;

  return (
    <div className="card overflow-hidden">
      <div className="border-b px-4 py-3" style={{ borderColor: "var(--dashboard-border)" }}>
        <p className="eyebrow">Clinician prescriptions</p>
        <p className="text-muted mt-0.5 text-xs">Entered by hospital staff. Voxera never prescribes.</p>
      </div>
      <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
        {live.map((p) => (
          <li key={p.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-bold">{p.medication_name}</span>
                <Badge tone={p.status === "active" ? "success" : undefined}>{p.status}</Badge>
              </div>
              <p className="mt-0.5 text-sm">{[p.dosage, p.route, p.frequency, p.duration && `for ${p.duration}`].filter(Boolean).join(" · ")}</p>
              {p.instructions && <p className="text-muted mt-0.5 text-sm">{p.instructions}</p>}
              <p className="text-faint mt-1 text-xs">{fmtDateTime(p.prescribed_at)}{p.prescribed_by ? ` · ${p.prescribed_by}` : ""}</p>
            </div>
            {role === "doctor" && <button className="btn btn-danger btn-sm shrink-0" onClick={() => setTarget(p)}>Delete</button>}
          </li>
        ))}
      </ul>
      <ConfirmRemove open={!!target} title="Delete this prescription?" onClose={() => setTarget(null)}
        onConfirm={async () => { if (target) { await removeMedication(patientRef, target.id, "prescriptions"); onChanged(); } }}>
        <p><b>{target?.medication_name}</b> {target?.dosage} will be removed from this patient&apos;s record and from record answers.</p>
        <p className="text-muted">Use this for a prescription entered by mistake. The deletion is logged with your name.</p>
      </ConfirmRemove>
    </div>
  );
}
