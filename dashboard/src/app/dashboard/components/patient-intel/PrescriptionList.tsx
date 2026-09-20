"use client";

import { useState } from "react";
import { deleteDocument } from "@/lib/patientApi";
import type { MedicalDocument, PatientMedication, Role } from "@/lib/patientTypes";
import { fmtDateTime } from "@/lib/format";
import { Badge, EmptyState } from "../ui";
import ConfirmRemove from "./ConfirmRemove";
import PrescriptionViewer from "./PrescriptionViewer";

/** Uploaded prescription documents, with what was extracted from each and its verification state. */
export default function PrescriptionList({
  patientRef, documents, meds, role, onChanged,
}: { patientRef: string; documents: MedicalDocument[]; meds: PatientMedication[]; role?: Role | null; onChanged?: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const [target, setTarget] = useState<MedicalDocument | null>(null);
  const docs = documents.filter((d) => d.document_type === "prescription");
  const canDelete = role === "doctor" || role === "nurse";

  if (docs.length === 0) {
    return <div className="card"><EmptyState icon="📄" title="No uploaded prescriptions" hint="Upload a photo or PDF of a prescription to add its medicines for review." /></div>;
  }
  const targetVerified = target ? meds.filter((m) => m.document_id === target.id && m.is_clinician_confirmed).length : 0;
  return (
    <>
      <div className="card overflow-hidden">
        <div className="border-b px-4 py-3" style={{ borderColor: "var(--dashboard-border)" }}><p className="eyebrow">Uploaded prescriptions</p></div>
        <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
          {docs.map((d) => {
            const items = meds.filter((m) => m.document_id === d.id);
            const verified = items.filter((m) => m.is_clinician_confirmed).length;
            return (
              <li key={d.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="truncate font-semibold">{d.filename ?? "Prescription"}</p>
                  <p className="text-muted text-xs">{fmtDateTime(d.created_at)} · {items.length} medicine(s), {verified} verified</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={d.ocr_status === "done" ? "success" : "warning"}>{d.ocr_status === "done" ? "Read" : d.ocr_status.replace("_", " ")}</Badge>
                  <button className="btn btn-sm" onClick={() => setOpen(d.id)}>View prescription</button>
                  {canDelete && <button className="btn btn-danger btn-sm" onClick={() => setTarget(d)}>Delete</button>}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
      <PrescriptionViewer patientRef={patientRef} docId={open} onClose={() => setOpen(null)} />
      <ConfirmRemove open={!!target} title="Delete this prescription?" onClose={() => setTarget(null)}
        onConfirm={async () => { if (target) { await deleteDocument(patientRef, target.id); onChanged?.(); } }}>
        <p>The uploaded file <b>{target?.filename ?? "prescription"}</b> and the medicines read from it will be deleted.</p>
        {targetVerified > 0 && (
          role === "doctor"
            ? <p className="font-semibold">{targetVerified} medicine(s) already confirmed from it will also be removed from the active record.</p>
            : <p className="font-semibold" style={{ color: "var(--critical-ink)" }}>{targetVerified} medicine(s) were confirmed from it, so only a doctor can delete it.</p>
        )}
        <p className="text-muted">This can&apos;t be undone. The deletion is logged with your name.</p>
      </ConfirmRemove>
    </>
  );
}
