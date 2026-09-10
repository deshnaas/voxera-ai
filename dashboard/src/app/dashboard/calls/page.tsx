"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { humanDuration, type VoxeraCall } from "@/lib/voxera";

type Patient = { id: string; full_name: string; phone: string | null };
type CallWithPatient = VoxeraCall & { patient: Patient | null };

export default function CallsPage() {
  const [calls, setCalls] = useState<CallWithPatient[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setMessage("");

    const {
      data: { user },
      error: userError,
    } = await supabase.auth.getUser();
    if (userError || !user) {
      setMessage("You are not logged in.");
      setLoading(false);
      return;
    }

    const { data: hospitalUser, error: huError } = await supabase
      .from("hospital_users")
      .select("facility_id")
      .eq("user_id", user.id)
      .single();
    if (huError || !hospitalUser) {
      setMessage("Hospital information could not be found.");
      setLoading(false);
      return;
    }
    const facilityId = hospitalUser.facility_id;

    // Calls Voxera routed to this facility. Calls without a facility_id
    // (older rows / calls before a referral existed) are also shown.
    const { data: callData, error: callError } = await supabase
      .from("calls")
      .select("*")
      .or(`facility_id.eq.${facilityId},facility_id.is.null`)
      .order("created_at", { ascending: false })
      .limit(50);

    if (callError) {
      setMessage(`Calls could not be loaded: ${callError.message}`);
      setLoading(false);
      return;
    }

    const rows = (callData ?? []) as unknown as VoxeraCall[];
    const patientIds = [
      ...new Set(rows.map((c) => c.patient_id).filter(Boolean)),
    ] as string[];

    let patients: Patient[] = [];
    if (patientIds.length > 0) {
      const { data: pData } = await supabase
        .from("patients")
        .select("id, full_name, phone")
        .in("id", patientIds);
      patients = (pData ?? []) as Patient[];
    }

    setCalls(
      rows.map((c) => ({
        ...c,
        patient: patients.find((p) => p.id === c.patient_id) ?? null,
      }))
    );
    setLoading(false);
  }

  const total = calls.length;
  const emergencies = calls.filter(
    (c) =>
      (c.outcome ?? "").includes("emergency") ||
      (c.emergency_checks_count ?? 0) > 0
  ).length;
  const completed = calls.filter((c) => c.status === "completed").length;
  const avgAi =
    total > 0
      ? Math.round(
          calls.reduce((n, c) => n + (c.ai_response_count ?? 0), 0) / total
        )
      : 0;

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">
        <section className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">
          <div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                Voxera Voice Agent
              </p>
              <h1 className="mt-2 text-4xl font-bold">☎ Calls</h1>
              <p className="dashboard-muted mt-2 text-sm">
                Every call Voxera handled. Open a patient to see the full
                conversation and summary.
              </p>
            </div>
            <button
              type="button"
              onClick={load}
              className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
            >
              Refresh
            </button>
          </div>
        </section>

        {message && (
          <div className="mt-5 rounded-xl border-2 border-[#D4AF37] bg-white px-5 py-4 text-sm font-semibold">
            {message}
          </div>
        )}

        <section className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Total Calls" value={loading ? "—" : total} />
          <Stat
            label="Emergency Escalations"
            value={loading ? "—" : emergencies}
          />
          <Stat label="Completed" value={loading ? "—" : completed} />
          <Stat label="Avg AI Replies / Call" value={loading ? "—" : avgAi} />
        </section>

        <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">
          <h2 className="text-2xl font-bold">Recent Calls</h2>

          {loading ? (
            <p className="dashboard-muted mt-6 text-sm">Loading…</p>
          ) : calls.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-8 text-center">
              <p className="font-semibold">No calls yet</p>
              <p className="dashboard-muted mt-2 text-sm">
                Calls appear here after Voxera speaks with a patient.
              </p>
            </div>
          ) : (
            <div className="mt-6 overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse">
                <thead>
                  <tr className="border-b-2 border-[#D4AF37] text-left">
                    <th className="px-3 py-3 text-sm font-bold">When</th>
                    <th className="px-3 py-3 text-sm font-bold">Patient</th>
                    <th className="px-3 py-3 text-sm font-bold">Type</th>
                    <th className="px-3 py-3 text-sm font-bold">Outcome</th>
                    <th className="px-3 py-3 text-sm font-bold">Duration</th>
                    <th className="px-3 py-3 text-sm font-bold">AI / Interrupts</th>
                    <th className="px-3 py-3 text-sm font-bold"></th>
                  </tr>
                </thead>
                <tbody>
                  {calls.map((c) => {
                    const emergency =
                      (c.outcome ?? "").includes("emergency") ||
                      (c.emergency_checks_count ?? 0) > 0;
                    return (
                      <tr key={c.id} className="border-b border-gray-300">
                        <td className="px-3 py-3 text-sm">
                          {new Date(c.created_at).toLocaleString()}
                        </td>
                        <td className="px-3 py-3 text-sm font-medium">
                          {c.patient?.full_name ?? "Unknown"}
                        </td>
                        <td className="px-3 py-3 text-sm">
                          {c.call_type ?? "—"}
                        </td>
                        <td className="px-3 py-3 text-sm">
                          {emergency && (
                            <span className="mr-2 rounded-full border-2 border-red-500 px-2 py-0.5 text-xs font-bold uppercase text-red-600">
                              Emergency
                            </span>
                          )}
                          {c.outcome ?? c.status ?? "—"}
                        </td>
                        <td className="px-3 py-3 text-sm">
                          {humanDuration(c.call_duration_seconds)}
                        </td>
                        <td className="px-3 py-3 text-sm">
                          {c.ai_response_count ?? 0} / {c.interruption_count ?? 0}
                        </td>
                        <td className="px-3 py-3 text-right">
                          {c.patient_id && (
                            <Link
                              href={`/dashboard/patients/${c.patient_id}`}
                              className="rounded-lg border-2 border-[#D4AF37] px-4 py-1.5 text-xs font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
                            >
                              Open Patient
                            </Link>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
      <p className="dashboard-muted text-sm font-semibold">{label}</p>
      <p className="mt-3 text-4xl font-bold">{value}</p>
    </div>
  );
}
