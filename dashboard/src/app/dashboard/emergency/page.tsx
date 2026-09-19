"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useStaff } from "@/lib/staff";
import { getPatientsMap, type PatientLite } from "@/lib/voxera";
import { fmtDateTime, timeAgo, waiting } from "@/lib/format";
import { Avatar, Badge, Callout, EmptyState, LoadingRows, Page, PageHeader, SectionCard, StatCard } from "../components/ui";
import Icon from "../components/Icon";

type Case = {
  id: string; patient_id: string; referral_id: string | null; priority: string; status: string;
  symptoms_summary: string | null; immediate_action: string | null; created_at: string;
  updated_at?: string | null;
};

export default function EmergencyPage() {
  const { facilityId, tick, refresh } = useStaff();
  const [cases, setCases] = useState<Case[]>([]);
  const [patients, setPatients] = useState<Record<string, PatientLite>>({});
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "warning"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [, force] = useState(0);

  // keep the "waiting" timers ticking
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);

  const load = useCallback(async () => {
    if (!facilityId) return;
    const { data, error: err } = await supabase
      .from("emergency_cases").select("*")
      .eq("facility_id", facilityId).order("created_at", { ascending: false }).limit(200);
    if (err) { setError("Unable to load emergency cases: " + err.message); setLoaded(true); return; }
    setError("");
    const rows = (data ?? []) as Case[];
    setCases(rows);
    setPatients(await getPatientsMap(rows.map((r) => r.patient_id)));
    setLoaded(true);
  }, [facilityId]);

  useEffect(() => { void load(); }, [load, tick]);

  async function resolve(c: Case) {
    if (!confirm(`Mark the emergency for ${patients[c.patient_id]?.full_name ?? "this patient"} as resolved?`)) return;
    setBusy(c.id); setNotice(null);
    const { data, error: err } = await supabase
      .from("emergency_cases").update({ status: "resolved" }).eq("id", c.id).select("id");
    setBusy(null);
    if (err || !data || data.length === 0) {
      setNotice({
        tone: "warning",
        text: "Could not mark resolved — your account isn't allowed to update emergency cases yet. Run sql/2026_dashboard_v2.sql once in the Supabase SQL Editor.",
      });
      return;
    }
    setNotice({ tone: "success", text: "Emergency marked as resolved." });
    refresh();
    void load();
  }

  const active = cases.filter((c) => c.status === "active");
  const closed = cases.filter((c) => c.status !== "active");

  return (
    <Page>
      <PageHeader
        eyebrow="Emergency management"
        title="Emergency board"
        subtitle="Patients Voxera flagged as emergencies and routed to this hospital. Oldest waiting first."
        actions={<button className="btn" onClick={() => { refresh(); void load(); }}><Icon name="refresh" size={16} /> Refresh</button>}
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard label="Active now" value={loaded ? active.length : "—"} tone={active.length ? "critical" : "success"} pulse={active.length > 0} />
        <StatCard label="Resolved" value={loaded ? closed.filter((c) => c.status === "resolved").length : "—"} />
        <StatCard label="All time" value={loaded ? cases.length : "—"} />
      </div>

      {error && <Callout tone="critical" className="mb-4">{error}</Callout>}
      {notice && <Callout tone={notice.tone} className="mb-4">{notice.text}</Callout>}

      {!loaded ? <LoadingRows rows={3} /> : (
        <div className="space-y-6">
          <SectionCard title="Active emergencies" padded={false}>
            {active.length === 0 ? (
              <EmptyState icon="✓" title="No active emergencies" hint="New emergencies appear here instantly with an alert." />
            ) : (
              <ul className="divide-y" style={{ borderColor: "var(--dashboard-border)" }}>
                {[...active].reverse().map((c) => {
                  const p = patients[c.patient_id];
                  return (
                    <li key={c.id} className="triage triage-critical px-5 py-5">
                      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                        <div className="flex min-w-0 gap-3">
                          <Avatar name={p?.full_name} tone="critical" size={46} />
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <Link href={`/dashboard/patients/${c.patient_id}`} className="text-lg font-bold hover:underline">
                                {p?.full_name ?? "Unknown patient"}
                              </Link>
                              <Badge tone="critical">{c.priority}</Badge>
                              <span className="badge badge-critical"><Icon name="clock" size={12} /> waiting {waiting(c.created_at)}</span>
                            </div>
                            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                              <div>
                                <p className="eyebrow">Symptoms</p>
                                <p className="mt-1 text-sm font-medium">{c.symptoms_summary ?? "Not recorded"}</p>
                              </div>
                              <div>
                                <p className="eyebrow">Immediate action</p>
                                <p className="mt-1 text-sm font-medium">{c.immediate_action ?? "Not recorded"}</p>
                              </div>
                            </div>
                            <p className="text-muted mt-3 text-xs">Flagged {fmtDateTime(c.created_at)} ({timeAgo(c.created_at)})</p>
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-wrap gap-2 lg:flex-col lg:items-stretch">
                          {p?.phone && <a href={`tel:${p.phone}`} className="btn btn-lg"><Icon name="phone" size={16} /> {p.phone}</a>}
                          {c.referral_id && <Link href={`/dashboard/referrals/${c.referral_id}`} className="btn btn-danger">Open referral</Link>}
                          <Link href={`/dashboard/patients/${c.patient_id}`} className="btn">Patient record</Link>
                          <button className="btn btn-success" disabled={busy === c.id} onClick={() => void resolve(c)}>
                            <Icon name="check" size={16} /> {busy === c.id ? "Saving…" : "Mark resolved"}
                          </button>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </SectionCard>

          {closed.length > 0 && (
            <SectionCard title="Previous cases" padded={false}>
              <div className="overflow-x-auto">
                <table className="table">
                  <thead><tr><th>Patient</th><th>Symptoms</th><th>Status</th><th>Flagged</th><th /></tr></thead>
                  <tbody>
                    {closed.map((c) => (
                      <tr key={c.id}>
                        <td className="font-semibold">
                          <Link href={`/dashboard/patients/${c.patient_id}`} className="hover:underline">{patients[c.patient_id]?.full_name ?? "Unknown"}</Link>
                        </td>
                        <td className="text-muted max-w-xs truncate">{c.symptoms_summary ?? "—"}</td>
                        <td><Badge tone={c.status === "resolved" ? "success" : undefined}>{c.status}</Badge></td>
                        <td className="text-muted whitespace-nowrap">{fmtDateTime(c.created_at)}</td>
                        <td>{c.referral_id && <Link href={`/dashboard/referrals/${c.referral_id}`} className="btn btn-sm">Referral</Link>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          )}
        </div>
      )}
    </Page>
  );
}
