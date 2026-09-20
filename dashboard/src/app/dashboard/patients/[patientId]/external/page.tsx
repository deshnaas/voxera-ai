"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { openRecord, PatientApiError, type ReasonRequired } from "@/lib/patientApi";
import type { AccessReason, PatientRecord } from "@/lib/patientTypes";
import { Callout, LoadingRows, Page, PageHeader, SectionCard } from "../../../components/ui";
import PatientOverview from "../../../components/patient-intel/PatientOverview";
import MedicationList from "../../../components/patient-intel/MedicationList";
import ConsultationHistory from "../../../components/patient-intel/ConsultationHistory";
import RecordQuestionBox from "../../../components/patient-intel/RecordQuestionBox";
import ReasonDialog from "../../../components/patient-intel/ReasonDialog";
import { fmtDate } from "@/lib/format";

const KEY = (ref: string) => `voxera-access-reason:${ref}`;

type Stored = { reason: AccessReason; text: string };

function readStored(ref: string): Stored | null {
  try {
    const raw = sessionStorage.getItem(KEY(ref));
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch { return null; }
}

/** Another facility's patient: essentials only, reason required, every open is audited. */
export default function ExternalRecordPage() {
  const { patientId } = useParams<{ patientId: string }>();
  const ref = decodeURIComponent(patientId);
  const [record, setRecord] = useState<PatientRecord | null>(null);
  const [stored, setStored] = useState<Stored | null>(null);
  const [error, setError] = useState("");
  const [needReason, setNeedReason] = useState<ReasonRequired | null>(null);
  const [loading, setLoading] = useState(true);

  const open = useCallback(async (s: Stored | null) => {
    setLoading(true); setError("");
    try {
      const r = await openRecord(ref, s?.reason, s?.text);
      setRecord(r); setNeedReason(null);
      if (s) setStored(s);
    } catch (e) {
      if (e instanceof PatientApiError && e.reasonRequired) setNeedReason(e.reasonRequired);
      else setError(e instanceof PatientApiError ? (e.offline ? "The patient-records service is not running." : e.message) : "Could not open the record.");
    } finally { setLoading(false); }
  }, [ref]);

  useEffect(() => { void open(readStored(ref)); }, [open, ref]);

  function confirm(reason: AccessReason, text: string) {
    const s = { reason, text };
    try { sessionStorage.setItem(KEY(ref), JSON.stringify(s)); } catch { /* ignore */ }
    void open(s);
  }

  const p = record?.patient;
  return (
    <Page>
      <Link href="/dashboard/patients/search" className="text-muted text-sm font-semibold hover:underline">← Find patient</Link>
      <div className="mt-3">
        <PageHeader eyebrow="External patient record" title={p?.full_name ?? "Patient record"}
          subtitle="Essential information shared for continuity of care. Your access is recorded." />
      </div>

      {error && <Callout tone="critical" className="mb-4">{error}</Callout>}
      {loading && !record && <LoadingRows rows={3} />}

      {record && (
        <div className="space-y-5">
          <PatientOverview record={record} />
          {record.medications && (
            <SectionCard title="Medications" subtitle="With their source. Only clinician-confirmed items are current."><MedicationList meds={record.medications} /></SectionCard>
          )}
          {record.prescriptions && record.prescriptions.length > 0 && (
            <SectionCard title="Recent prescriptions" padded={false}>
              <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
                {record.prescriptions.slice(0, 6).map((r) => (
                  <li key={r.id} className="px-5 py-3 text-sm">
                    <b>{r.medication_name}</b> <span className="text-muted">{[r.dosage, r.route, r.frequency].filter(Boolean).join(" · ")}</span>
                    <span className="text-faint ml-2 text-xs">{fmtDate(r.prescribed_at)} · {r.status}</span>
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}
          {record.consultations && <SectionCard title="Recent consultations"><ConsultationHistory items={record.consultations} /></SectionCard>}
          {record.referrals && record.referrals.length > 0 && (
            <SectionCard title="Referrals">
              <ul className="space-y-1 text-sm">
                {record.referrals.map((r) => <li key={r.id}>{fmtDate(r.created_at)} · {r.recommended_department ?? "Referral"} · {r.status}</li>)}
              </ul>
            </SectionCard>
          )}
          {(record.sections.includes("medications") || record.sections.includes("consultations")) && (
            <RecordQuestionBox patientRef={ref} reason={stored?.reason} reasonText={stored?.text} />
          )}
        </div>
      )}

      <ReasonDialog open={!!needReason} onClose={() => setNeedReason(null)} onConfirm={confirm} patientLabel={ref} />
    </Page>
  );
}
