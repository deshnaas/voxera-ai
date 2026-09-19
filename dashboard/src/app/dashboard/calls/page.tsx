"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useStaff } from "@/lib/staff";
import { getPatientsMap, humanDuration, isEmergencyCall, isLiveCall, type PatientLite, type VoxeraCall } from "@/lib/voxera";
import { fmtDateTime, timeAgo } from "@/lib/format";
import { Avatar, Callout, Chip, EmptyState, LoadingRows, Page, PageHeader } from "../components/ui";
import { CallStatusBadges } from "../components/CallHistory";
import Icon from "../components/Icon";

type Filter = "all" | "live" | "emergency";

export default function CallsPage() {
  const { facilityId, tick, refresh } = useStaff();
  const [calls, setCalls] = useState<VoxeraCall[]>([]);
  const [patients, setPatients] = useState<Record<string, PatientLite>>({});
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(async () => {
    if (!facilityId) return;
    // Calls routed to this facility plus calls with no facility yet (routine calls).
    const { data, error: err } = await supabase.from("calls").select("*")
      .or(`facility_id.eq.${facilityId},facility_id.is.null`)
      .order("created_at", { ascending: false }).limit(100);
    if (err) { setError("Calls could not be loaded: " + err.message); setLoaded(true); return; }
    setError("");
    const rows = (data ?? []) as unknown as VoxeraCall[];
    setCalls(rows);
    setPatients(await getPatientsMap(rows.map((r) => r.patient_id)));
    setLoaded(true);
  }, [facilityId]);

  useEffect(() => { void load(); }, [load, tick]);

  const shown = calls.filter((c) => filter === "all" || (filter === "live" ? isLiveCall(c) : isEmergencyCall(c)));

  return (
    <Page>
      <PageHeader
        eyebrow="Voxera voice agent"
        title="Calls"
        subtitle="Every call Voxera handled — routine or emergency — with transcript and summary."
        actions={<button className="btn" onClick={() => { refresh(); void load(); }}><Icon name="refresh" size={16} /> Refresh</button>}
      />
      <div className="mb-4 flex flex-wrap gap-2">
        <Chip active={filter === "all"} onClick={() => setFilter("all")}>All ({calls.length})</Chip>
        <Chip active={filter === "live"} onClick={() => setFilter("live")}>Live now ({calls.filter(isLiveCall).length})</Chip>
        <Chip active={filter === "emergency"} onClick={() => setFilter("emergency")}>Emergency ({calls.filter(isEmergencyCall).length})</Chip>
      </div>
      {error && <Callout tone="critical" className="mb-4">{error}</Callout>}

      {!loaded ? <LoadingRows rows={5} /> : shown.length === 0 ? (
        <div className="card"><EmptyState icon="☎" title="No calls" hint="Calls appear here as patients speak with Voxera." /></div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="table">
            <thead><tr><th>Patient</th><th>Started</th><th>Type</th><th>Outcome</th><th>Duration</th><th /></tr></thead>
            <tbody>
              {shown.map((c) => {
                const p = c.patient_id ? patients[c.patient_id] : undefined;
                return (
                  <tr key={c.id} className={isEmergencyCall(c) ? "triage triage-critical" : undefined}>
                    <td>
                      <div className="flex items-center gap-3">
                        <Avatar name={p?.full_name} size={32} />
                        <div>
                          <p className="font-semibold">{p?.full_name ?? "Unknown patient"}</p>
                          <p className="text-muted text-xs">{p?.phone ?? ""}</p>
                        </div>
                      </div>
                    </td>
                    <td className="whitespace-nowrap"><span title={fmtDateTime(c.created_at)}>{timeAgo(c.created_at)}</span></td>
                    <td><div className="flex flex-wrap gap-1.5"><CallStatusBadges call={c} /></div></td>
                    <td className="text-muted">{c.outcome ?? "—"}</td>
                    <td className="whitespace-nowrap">{humanDuration(c.call_duration_seconds)}</td>
                    <td><Link href={`/dashboard/calls/${c.id}`} className="btn btn-sm">Open</Link></td>
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
