"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useStaff } from "@/lib/staff";
import type { Patient } from "@/lib/voxera";
import { ageFromDob, timeAgo } from "@/lib/format";
import { Avatar, Badge, Callout, Chip, EmptyState, LoadingRows, Page, PageHeader } from "../components/ui";
import Icon from "../components/Icon";

type Filter = "all" | "emergency" | "referral" | "calls";

export default function PatientsPage() {
  const { facilityId, tick } = useStaff();
  const [patients, setPatients] = useState<Patient[]>([]);
  const [emergencyIds, setEmergencyIds] = useState<Set<string>>(new Set());
  const [referralIds, setReferralIds] = useState<Set<string>>(new Set());
  const [lastCall, setLastCall] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(async () => {
    if (!facilityId) return;
    const [pt, em, rf, cl] = await Promise.all([
      supabase.from("patients").select("*").order("created_at", { ascending: false }).limit(1000),
      supabase.from("emergency_cases").select("patient_id").eq("facility_id", facilityId).eq("status", "active"),
      supabase.from("referrals").select("patient_id").eq("receiving_facility_id", facilityId).neq("status", "rejected"),
      supabase.from("calls").select("patient_id, created_at").order("created_at", { ascending: false }).limit(2000),
    ]);
    if (pt.error) { setError("Unable to load patients: " + pt.error.message); setLoaded(true); return; }
    setError("");
    setPatients((pt.data ?? []) as Patient[]);
    setEmergencyIds(new Set((em.data ?? []).map((r) => r.patient_id as string)));
    setReferralIds(new Set((rf.data ?? []).map((r) => r.patient_id as string)));
    const last: Record<string, string> = {};
    (cl.data ?? []).forEach((c) => {
      const pid = c.patient_id as string | null;
      if (pid && !last[pid]) last[pid] = c.created_at as string;
    });
    setLastCall(last);
    setLoaded(true);
  }, [facilityId]);

  useEffect(() => { void load(); }, [load, tick]);

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    return patients
      .filter((p) => {
        if (filter === "emergency" && !emergencyIds.has(p.id)) return false;
        if (filter === "referral" && !referralIds.has(p.id)) return false;
        if (filter === "calls" && !lastCall[p.id]) return false;
        if (!term) return true;
        return (p.full_name ?? "").toLowerCase().includes(term) || (p.phone ?? "").includes(term)
          || (p.patient_id ?? "").toLowerCase().includes(term)
          || (p.village_or_locality ?? "").toLowerCase().includes(term);
      })
      .sort((a, b) => {
        const ea = emergencyIds.has(a.id) ? 1 : 0, eb = emergencyIds.has(b.id) ? 1 : 0;
        if (ea !== eb) return eb - ea;
        const la = lastCall[a.id] ?? a.created_at ?? "", lb = lastCall[b.id] ?? b.created_at ?? "";
        return lb.localeCompare(la);
      });
  }, [patients, q, filter, emergencyIds, referralIds, lastCall]);

  return (
    <Page>
      <PageHeader
        eyebrow="Records"
        title="Patients"
        subtitle="Everyone who has spoken with Voxera or been referred to this hospital. Active emergencies are pinned to the top."
        actions={<Link href="/dashboard/patients/search" className="btn"><Icon name="search" size={16} /> Search all hospitals</Link>}
      />

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative w-full md:max-w-sm">
          <span className="text-faint pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"><Icon name="search" size={16} /></span>
          <input className="input" style={{ paddingLeft: 36 }} placeholder="Filter by name, ID, phone or locality" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Chip active={filter === "all"} onClick={() => setFilter("all")}>All ({patients.length})</Chip>
          <Chip active={filter === "emergency"} onClick={() => setFilter("emergency")}>Active emergency ({emergencyIds.size})</Chip>
          <Chip active={filter === "referral"} onClick={() => setFilter("referral")}>Referred here</Chip>
          <Chip active={filter === "calls"} onClick={() => setFilter("calls")}>Has Voxera calls</Chip>
        </div>
      </div>

      {error && <Callout tone="critical" className="mb-4">{error}</Callout>}

      {!loaded ? <LoadingRows rows={5} /> : rows.length === 0 ? (
        <div className="card"><EmptyState icon="♙" title="No patients match" hint={q ? "Try a different name or phone number." : "Patients appear here after their first Voxera call."} /></div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="table">
            <thead><tr><th>Patient</th><th>Patient ID</th><th>Age / gender</th><th>Phone</th><th>Locality</th><th>Last contact</th><th>Flags</th></tr></thead>
            <tbody>
              {rows.map((p) => {
                const emergency = emergencyIds.has(p.id);
                const age = ageFromDob(p.date_of_birth);
                return (
                  <tr key={p.id} className={emergency ? "triage triage-critical" : undefined}>
                    <td>
                      <Link href={`/dashboard/patients/${p.id}`} className="flex items-center gap-3 font-semibold hover:underline">
                        <Avatar name={p.full_name} size={34} tone={emergency ? "critical" : undefined} /> {p.full_name}
                      </Link>
                    </td>
                    <td className="font-mono text-xs">{p.patient_id ?? "—"}</td>
                    <td>{[age !== null && `${age}`, p.gender].filter(Boolean).join(" · ") || "—"}</td>
                    <td>{p.phone ?? "—"}</td>
                    <td className="text-muted">{[p.village_or_locality, p.district].filter(Boolean).join(", ") || "—"}</td>
                    <td className="text-muted whitespace-nowrap">{lastCall[p.id] ? timeAgo(lastCall[p.id]) : "—"}</td>
                    <td>
                      <div className="flex flex-wrap gap-1.5">
                        {emergency && <Badge tone="critical">Emergency</Badge>}
                        {referralIds.has(p.id) && <Badge tone="warning">Referred</Badge>}
                        {p.allergies && <Badge tone="warning">Allergy</Badge>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
