"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { PatientApiError, searchPatients } from "@/lib/patientApi";
import type { AccessReason, SearchResult } from "@/lib/patientTypes";
import { fmtDate } from "@/lib/format";
import { Avatar, Badge, Callout, EmptyState, LoadingRows, Page, PageHeader } from "../../components/ui";
import Icon from "../../components/Icon";
import ReasonDialog from "../../components/patient-intel/ReasonDialog";

function SearchInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<SearchResult | null>(null);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setResults(null); setError(""); return; }
    setBusy(true);
    const t = setTimeout(async () => {                       // debounce 300 ms; never loads all patients
      try {
        setResults((await searchPatients(term)).results);
        setError("");
      } catch (e) {
        setResults(null);
        setError(e instanceof PatientApiError ? (e.offline ? "The patient-records service is not running. Start it to search across hospitals." : e.message) : "Search failed.");
      } finally { setBusy(false); }
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  function openResult(r: SearchResult) {
    if (r.relationship === "home") router.push(`/dashboard/patients/${r.id}`);
    else setPending(r);
  }

  function confirmReason(reason: AccessReason, text: string) {
    if (!pending) return;
    try { sessionStorage.setItem(`voxera-access-reason:${pending.patient_id}`, JSON.stringify({ reason, text })); } catch { /* ignore */ }
    router.push(`/dashboard/patients/${encodeURIComponent(pending.patient_id)}/external`);
  }

  return (
    <Page>
      <PageHeader eyebrow="Records" title="Find a patient"
        subtitle="Search by patient ID, name or phone number. Records from other hospitals show essentials only, need a reason, and are logged." />

      <div className="relative mb-5 max-w-2xl">
        <span className="text-faint pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"><Icon name="search" size={18} /></span>
        <input autoFocus className="input" style={{ paddingLeft: 40, minHeight: 48, fontSize: 16 }}
               placeholder="Search by Patient ID, name or phone   e.g. VX-000123" value={q}
               onChange={(e) => setQ(e.target.value)} aria-label="Search patients" />
      </div>

      {error && <Callout tone="warning" className="mb-4">{error}</Callout>}
      {busy && !results && <LoadingRows rows={2} />}
      {!busy && results === null && !error && <div className="card"><EmptyState icon="⌕" title="Start typing to search" hint="Patient ID is the fastest way. Nothing is listed until you search." /></div>}
      {results && results.length === 0 && <div className="card"><EmptyState icon="○" title="No patient found" hint="Check the ID or try the name or phone number." /></div>}

      {results && results.length > 0 && (
        <ul className="space-y-3">
          {results.map((r) => (
            <li key={r.id} className="card card-hover p-4">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar name={r.full_name} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-base font-bold">{r.full_name}</span>
                      <span className="badge font-mono">{r.patient_id}</span>
                      {r.relationship === "home" ? <Badge tone="success">Your patient</Badge> : <Badge tone="warning">Other facility</Badge>}
                    </div>
                    <p className="text-muted mt-1 text-sm">
                      {[r.age !== null && r.age !== undefined && `Age ${r.age}`, r.gender].filter(Boolean).join(" · ") || "—"}
                      {r.last_consultation ? ` · Last consultation ${fmtDate(r.last_consultation)}` : ""}
                    </p>
                    {r.relationship === "home" && (r.active_medications !== undefined || r.allergies !== undefined) && (
                      <p className="text-muted text-xs">
                        {r.active_medications ?? 0} active medication(s) · {r.allergies ?? 0} allerg{(r.allergies ?? 0) === 1 ? "y" : "ies"}
                      </p>
                    )}
                    {r.relationship === "external" && <p className="text-faint text-xs">Record access: reason required and logged</p>}
                  </div>
                </div>
                <button className="btn btn-primary shrink-0" onClick={() => openResult(r)}>View patient record</button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="text-faint mt-6 text-xs">
        Need only your own hospital&apos;s patients? <Link href="/dashboard/patients" className="underline">Open the patient list</Link>.
      </p>
      <ReasonDialog open={!!pending} onClose={() => setPending(null)} onConfirm={confirmReason} patientLabel={pending ? `${pending.full_name} (${pending.patient_id})` : ""} />
    </Page>
  );
}

export default function FindPatientPage() {
  return <Suspense fallback={<Page><LoadingRows rows={2} /></Page>}><SearchInner /></Suspense>;
}
