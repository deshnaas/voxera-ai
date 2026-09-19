"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useStaff } from "@/lib/staff";
import { getPatientsMap, type PatientLite } from "@/lib/voxera";
import { statusBadge, urgencyBadge, urgencyRank, waiting, timeAgo } from "@/lib/format";
import { Avatar, Callout, Chip, EmptyState, LoadingRows, Page, PageHeader } from "../components/ui";

type Referral = {
  id: string; patient_id: string; reason: string; urgency: string; status: string;
  required_service: string | null; recommended_department: string | null; created_at: string;
};
type Filter = "pending" | "accepted" | "scheduled" | "rejected" | "all";

export default function ReferralsPage() {
  const { facilityId, tick } = useStaff();
  const [rows, setRows] = useState<Referral[]>([]);
  const [patients, setPatients] = useState<Record<string, PatientLite>>({});
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("pending");

  const load = useCallback(async () => {
    if (!facilityId) return;
    const { data, error: err } = await supabase.from("referrals")
      .select("id, patient_id, reason, urgency, status, required_service, recommended_department, created_at")
      .eq("receiving_facility_id", facilityId).order("created_at", { ascending: false }).limit(300);
    if (err) { setError("Referrals could not be loaded: " + err.message); setLoaded(true); return; }
    setError("");
    const list = (data ?? []) as Referral[];
    setRows(list);
    setPatients(await getPatientsMap(list.map((r) => r.patient_id)));
    setLoaded(true);
  }, [facilityId]);

  useEffect(() => { void load(); }, [load, tick]);

  const count = (s: Filter) => rows.filter((r) => s === "all" || r.status === s).length;
  const shown = useMemo(() => {
    const list = rows.filter((r) => filter === "all" || r.status === filter);
    // Pending: most urgent, then longest waiting. Others: newest first.
    return filter === "pending"
      ? [...list].sort((a, b) => urgencyRank(a.urgency) - urgencyRank(b.urgency) || a.created_at.localeCompare(b.created_at))
      : list;
  }, [rows, filter]);

  return (
    <Page>
      <PageHeader
        eyebrow="Incoming"
        title="Referrals"
        subtitle="Voxera has already chosen this hospital as the destination. Review, accept or reject."
      />
      <div className="mb-4 flex flex-wrap gap-2">
        {(["pending", "accepted", "scheduled", "rejected", "all"] as Filter[]).map((f) => (
          <Chip key={f} active={filter === f} onClick={() => setFilter(f)}>
            {f[0].toUpperCase() + f.slice(1)} ({count(f)})
          </Chip>
        ))}
      </div>
      {error && <Callout tone="critical" className="mb-4">{error}</Callout>}

      {!loaded ? <LoadingRows rows={4} /> : shown.length === 0 ? (
        <div className="card"><EmptyState icon="✓" title={filter === "pending" ? "No referrals waiting" : "No referrals here"} hint="New referrals appear instantly." /></div>
      ) : (
        <ul className="space-y-3">
          {shown.map((r) => {
            const p = patients[r.patient_id];
            const hot = r.status === "pending" && urgencyRank(r.urgency) <= 1;
            return (
              <li key={r.id} className={`card triage ${hot ? "triage-critical" : r.status === "pending" ? "triage-warning" : "triage-success"} p-4`}>
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="flex min-w-0 items-start gap-3">
                    <Avatar name={p?.full_name} tone={hot ? "critical" : undefined} />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-base font-bold">{p?.full_name ?? "Unknown patient"}</span>
                        <span className={urgencyBadge(r.urgency)}>{r.urgency}</span>
                        <span className={statusBadge(r.status)}>{r.status}</span>
                      </div>
                      <p className="mt-1 truncate text-sm">{r.reason}</p>
                      <p className="text-muted mt-0.5 text-xs">
                        {r.recommended_department ?? r.required_service ?? "General"} ·{" "}
                        {r.status === "pending" ? <>waiting <b>{waiting(r.created_at)}</b></> : timeAgo(r.created_at)}
                      </p>
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Link href={`/dashboard/patients/${r.patient_id}`} className="btn btn-sm">Patient</Link>
                    <Link href={`/dashboard/referrals/${r.id}`} className={`btn btn-sm ${r.status === "pending" ? "btn-primary" : ""}`}>
                      {r.status === "pending" ? "Review" : "Open"}
                    </Link>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Page>
  );
}
