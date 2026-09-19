"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useStaff } from "@/lib/staff";
import {
  getPatientsMap, isEmergencyCall, isLiveCall, todayISO,
  type PatientLite, type VoxeraCall,
} from "@/lib/voxera";
import { fmtTime, timeAgo, urgencyBadge, urgencyRank, waiting } from "@/lib/format";
import { Avatar, Badge, EmptyState, LoadingRows, Page, PageHeader, SectionCard, StatCard } from "./components/ui";
import Icon from "./components/Icon";

type EmergencyCase = {
  id: string; patient_id: string; referral_id: string | null; priority: string; status: string;
  symptoms_summary: string | null; immediate_action: string | null; created_at: string;
};
type Referral = {
  id: string; patient_id: string; reason: string; urgency: string; status: string;
  required_service: string | null; recommended_department: string | null; created_at: string;
};
type Appt = {
  id: string; patient_id: string; appointment_time: string | null; department: string | null;
  doctor_name: string | null; status: string;
};
type Bed = { id: string; bed_type: string; total_beds: number; occupied_beds: number };

type QueueItem =
  | { kind: "emergency"; at: string; rank: number; row: EmergencyCase }
  | { kind: "referral"; at: string; rank: number; row: Referral };

export default function CommandCenter() {
  const { facility, facilityId, tick, counts, refresh } = useStaff();
  const [loaded, setLoaded] = useState(false);
  const [emergencies, setEmergencies] = useState<EmergencyCase[]>([]);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [calls, setCalls] = useState<VoxeraCall[]>([]);
  const [appts, setAppts] = useState<Appt[]>([]);
  const [beds, setBeds] = useState<Bed[]>([]);
  const [callsToday, setCallsToday] = useState(0);
  const [patients, setPatients] = useState<Record<string, PatientLite>>({});
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!facilityId) return;
    const today = todayISO();
    const startOfDay = new Date(); startOfDay.setHours(0, 0, 0, 0);

    const [em, rf, cl, ap, bd, ct] = await Promise.all([
      supabase.from("emergency_cases")
        .select("id, patient_id, referral_id, priority, status, symptoms_summary, immediate_action, created_at")
        .eq("facility_id", facilityId).eq("status", "active").order("created_at", { ascending: false }),
      supabase.from("referrals")
        .select("id, patient_id, reason, urgency, status, required_service, recommended_department, created_at")
        .eq("receiving_facility_id", facilityId).eq("status", "pending").order("created_at", { ascending: true }),
      supabase.from("calls").select("*")
        .or(`facility_id.eq.${facilityId},facility_id.is.null`)
        .order("created_at", { ascending: false }).limit(6),
      supabase.from("appointments")
        .select("id, patient_id, appointment_time, department, doctor_name, status")
        .eq("facility_id", facilityId).eq("appointment_date", today).order("appointment_time", { ascending: true }),
      supabase.from("facility_beds").select("id, bed_type, total_beds, occupied_beds").eq("facility_id", facilityId),
      supabase.from("calls").select("id", { count: "exact", head: true })
        .gte("created_at", startOfDay.toISOString())
        .or(`facility_id.eq.${facilityId},facility_id.is.null`),
    ]);

    const firstErr = em.error ?? rf.error ?? cl.error ?? ap.error ?? bd.error;
    if (firstErr) { setError(firstErr.message); }
    else setError("");

    const emRows = (em.data ?? []) as EmergencyCase[];
    const rfRows = (rf.data ?? []) as Referral[];
    const clRows = (cl.data ?? []) as unknown as VoxeraCall[];
    const apRows = (ap.data ?? []) as Appt[];

    setEmergencies(emRows);
    setReferrals(rfRows);
    setCalls(clRows);
    setAppts(apRows);
    setBeds((bd.data ?? []) as Bed[]);
    setCallsToday(ct.count ?? 0);

    setPatients(await getPatientsMap([
      ...emRows.map((r) => r.patient_id), ...rfRows.map((r) => r.patient_id),
      ...clRows.map((r) => r.patient_id), ...apRows.map((r) => r.patient_id),
    ]));
    setLoaded(true);
  }, [facilityId]);

  useEffect(() => { void load(); }, [load, tick]);

  const name = (id: string | null | undefined) => (id && patients[id]?.full_name) || "Unknown patient";
  const phone = (id: string | null | undefined) => (id ? patients[id]?.phone : null);

  // Emergencies are always first. A referral that already has an emergency
  // case is folded into that case (no duplicate rows).
  const coveredReferralIds = new Set(emergencies.map((e) => e.referral_id).filter(Boolean) as string[]);
  const queue: QueueItem[] = [
    ...emergencies.map((row): QueueItem => ({ kind: "emergency", at: row.created_at, rank: -1, row })),
    ...referrals.filter((r) => !coveredReferralIds.has(r.id))
      .map((row): QueueItem => ({ kind: "referral", at: row.created_at, rank: urgencyRank(row.urgency), row })),
  ].sort((a, b) => a.rank - b.rank || new Date(a.at).getTime() - new Date(b.at).getTime());

  const emergencyBeds = beds.find((b) => /emergency/i.test(b.bed_type));
  const icuBeds = beds.find((b) => /icu/i.test(b.bed_type));
  const avail = (b?: Bed) => (b ? Math.max(0, b.total_beds - b.occupied_beds) : null);

  return (
    <Page wide>
      <PageHeader
        eyebrow={facility?.name ?? "Hospital"}
        title="Command Center"
        subtitle="Everything that needs a decision, most urgent first. Updates live — no need to refresh."
        actions={
          <button className="btn" onClick={() => { refresh(); void load(); }}>
            <Icon name="refresh" size={16} /> Refresh
          </button>
        }
      />

      {error && <div className="callout callout-warning mb-4">Some data could not be loaded: {error}</div>}

      {/* Key numbers ------------------------------------------------ */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard label="Active emergencies" value={counts.emergencies}
          tone={counts.emergencies > 0 ? "critical" : "success"} pulse={counts.emergencies > 0}
          hint={counts.emergencies > 0 ? "Respond now" : "None right now"} href="/dashboard/emergency" />
        <StatCard label="Pending referrals" value={counts.pendingReferrals}
          tone={counts.pendingReferrals > 0 ? "warning" : "neutral"}
          hint={counts.pendingReferrals > 0 ? "Awaiting your decision" : "Nothing waiting"} href="/dashboard/referrals" />
        <StatCard label="Voxera calls live" value={counts.liveCalls}
          tone={counts.liveCalls > 0 ? "info" : "neutral"} hint={`${callsToday} call${callsToday === 1 ? "" : "s"} today`}
          href="/dashboard/calls" />
        <StatCard label="Appointments today" value={loaded ? appts.length : "—"}
          hint={loaded ? `${appts.filter((a) => a.status === "scheduled" || a.status === "confirmed").length} upcoming` : undefined}
          href="/dashboard/appointments" />
        <StatCard label="Beds free · ER / ICU"
          value={<>{avail(emergencyBeds) ?? "—"}<span className="text-faint text-xl font-bold"> / </span>{avail(icuBeds) ?? "—"}</>}
          tone={(avail(emergencyBeds) === 0 || avail(icuBeds) === 0) ? "critical" : "neutral"}
          hint="Emergency / ICU" href="/dashboard/beds" />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        {/* Needs action now ------------------------------------------ */}
        <div className="lg:col-span-2">
          <SectionCard
            title="Needs action now"
            subtitle="Emergencies first, then referrals by urgency and waiting time."
            actions={<Link href="/dashboard/referrals" className="btn btn-sm">All referrals</Link>}
            padded={false}
          >
            {!loaded ? (
              <div className="p-5"><LoadingRows rows={3} /></div>
            ) : queue.length === 0 ? (
              <EmptyState icon="✓" title="All clear" hint="No active emergencies and no referrals waiting for a decision." />
            ) : (
              <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
                {queue.map((item) =>
                  item.kind === "emergency" ? (
                    <li key={`e-${item.row.id}`} className="triage triage-critical px-5 py-4">
                      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div className="flex min-w-0 items-start gap-3">
                          <Avatar name={name(item.row.patient_id)} tone="critical" />
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <Link href={`/dashboard/patients/${item.row.patient_id}`} className="text-base font-bold hover:underline">
                                {name(item.row.patient_id)}
                              </Link>
                              <span className="badge badge-critical"><span className="dot dot-critical" /> EMERGENCY</span>
                              <Badge tone="critical">{item.row.priority}</Badge>
                            </div>
                            <p className="mt-1 text-sm font-medium">{item.row.symptoms_summary ?? "No symptoms recorded"}</p>
                            <p className="text-muted mt-0.5 text-xs">
                              Waiting <b>{waiting(item.row.created_at)}</b> · reported {timeAgo(item.row.created_at)}
                              {phone(item.row.patient_id) ? ` · ${phone(item.row.patient_id)}` : ""}
                            </p>
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-wrap gap-2">
                          {phone(item.row.patient_id) && (
                            <a href={`tel:${phone(item.row.patient_id)}`} className="btn btn-sm"><Icon name="phone" size={14} /> Call</a>
                          )}
                          {item.row.referral_id && (
                            <Link href={`/dashboard/referrals/${item.row.referral_id}`} className="btn btn-danger btn-sm">Open referral</Link>
                          )}
                          <Link href="/dashboard/emergency" className="btn btn-sm">Case</Link>
                        </div>
                      </div>
                    </li>
                  ) : (
                    <li key={`r-${item.row.id}`} className={`triage px-5 py-4 ${item.rank <= 1 ? "triage-warning" : "triage-info"}`}>
                      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div className="flex min-w-0 items-start gap-3">
                          <Avatar name={name(item.row.patient_id)} />
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <Link href={`/dashboard/patients/${item.row.patient_id}`} className="text-base font-bold hover:underline">
                                {name(item.row.patient_id)}
                              </Link>
                              <span className={urgencyBadge(item.row.urgency)}>{item.row.urgency}</span>
                            </div>
                            <p className="mt-1 truncate text-sm">{item.row.reason}</p>
                            <p className="text-muted mt-0.5 text-xs">
                              {item.row.recommended_department ?? item.row.required_service ?? "General"} · waiting <b>{waiting(item.row.created_at)}</b>
                            </p>
                          </div>
                        </div>
                        <Link href={`/dashboard/referrals/${item.row.id}`} className="btn btn-primary btn-sm shrink-0">Review referral</Link>
                      </div>
                    </li>
                  )
                )}
              </ul>
            )}
          </SectionCard>
        </div>

        {/* Side column ------------------------------------------------ */}
        <div className="space-y-6">
          <SectionCard title="Voxera calls" subtitle="Live and most recent"
            actions={<Link href="/dashboard/calls" className="btn btn-sm">All</Link>} padded={false}>
            {!loaded ? <div className="p-5"><LoadingRows rows={3} /></div> : calls.length === 0 ? (
              <EmptyState icon="☎" title="No calls yet" hint="Calls appear here as patients speak with Voxera." />
            ) : (
              <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
                {calls.map((c) => (
                  <li key={c.id}>
                    <Link href={`/dashboard/calls/${c.id}`} className="flex items-center gap-3 px-5 py-3 hover:bg-[var(--dashboard-surface-hover)]">
                      <Avatar name={name(c.patient_id)} size={34} tone={isEmergencyCall(c) ? "critical" : undefined} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">{name(c.patient_id)}</p>
                        <p className="text-muted truncate text-xs">{timeAgo(c.created_at)} · {c.ai_response_count ?? 0} AI replies</p>
                      </div>
                      {isLiveCall(c) ? <span className="badge badge-info"><span className="dot dot-live" /> LIVE</span>
                        : isEmergencyCall(c) ? <Badge tone="critical">Emergency</Badge>
                        : <Badge tone="success">Done</Badge>}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          <SectionCard title="Bed availability" actions={<Link href="/dashboard/beds" className="btn btn-sm">Manage</Link>}>
            {!loaded ? <LoadingRows rows={2} /> : beds.length === 0 ? (
              <p className="text-muted text-sm">No bed data for this hospital yet.</p>
            ) : (
              <div className="space-y-4">
                {beds.map((b) => {
                  const free = Math.max(0, b.total_beds - b.occupied_beds);
                  const pct = b.total_beds ? Math.round((b.occupied_beds / b.total_beds) * 100) : 0;
                  const tone = free === 0 ? "var(--critical)" : pct >= 85 ? "var(--warning)" : "var(--success)";
                  return (
                    <div key={b.id}>
                      <div className="mb-1 flex items-baseline justify-between text-sm">
                        <span className="font-semibold">{b.bed_type}</span>
                        <span className="text-muted"><b style={{ color: tone }}>{free}</b> free of {b.total_beds}</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full" style={{ background: "var(--dashboard-surface-muted)" }}>
                        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: tone }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </SectionCard>

          <SectionCard title="Today's appointments" actions={<Link href="/dashboard/appointments" className="btn btn-sm">All</Link>} padded={false}>
            {!loaded ? <div className="p-5"><LoadingRows rows={2} /></div> : appts.length === 0 ? (
              <p className="text-muted px-5 py-6 text-sm">Nothing scheduled for today.</p>
            ) : (
              <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
                {appts.slice(0, 6).map((a) => (
                  <li key={a.id} className="flex items-center gap-3 px-5 py-3">
                    <span className="w-16 shrink-0 text-sm font-bold">{fmtTime(a.appointment_time)}</span>
                    <div className="min-w-0 flex-1">
                      <Link href={`/dashboard/patients/${a.patient_id}`} className="block truncate text-sm font-semibold hover:underline">{name(a.patient_id)}</Link>
                      <p className="text-muted truncate text-xs">{a.department ?? "General"}{a.doctor_name ? ` · ${a.doctor_name}` : ""}</p>
                    </div>
                    <Badge tone={a.status === "confirmed" || a.status === "completed" ? "success" : "warning"}>{a.status}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </div>
      </div>
    </Page>
  );
}
