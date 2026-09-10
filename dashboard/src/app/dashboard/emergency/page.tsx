"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

type EmergencyCase = {
  id: string;
  patient_id: string;
  facility_id: string;
  referral_id: string | null;
  priority: string;
  status: string;
  symptoms_summary: string | null;
  immediate_action: string | null;
  created_at: string;
};

type Patient = {
  id: string;
  full_name: string;
  phone: string | null;
  gender: string | null;
  age: number | null;
};

export default function EmergencyPage() {
  const [cases, setCases] = useState<EmergencyCase[]>([]);
  const [patients, setPatients] = useState<Record<string, Patient>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadEmergencyCases();
  }, []);

  async function loadEmergencyCases() {
    setLoading(true);
    setError("");

    try {
      const {
        data: { user },
      } = await supabase.auth.getUser();

      if (!user) {
        setError("You are not logged in.");
        setLoading(false);
        return;
      }

      const { data: hospitalUser, error: hospitalUserError } =
        await supabase
          .from("hospital_users")
          .select("facility_id")
          .eq("user_id", user.id)
          .single();

      if (hospitalUserError) {
        throw hospitalUserError;
      }

      const facilityId = hospitalUser.facility_id;

      const { data: emergencyData, error: emergencyError } = await supabase
        .from("emergency_cases")
        .select("*")
        .eq("facility_id", facilityId)
        .order("created_at", { ascending: false });

      if (emergencyError) {
        throw emergencyError;
      }

      const emergencyCases = emergencyData || [];
      setCases(emergencyCases);

      if (emergencyCases.length > 0) {
        const patientIds = [
          ...new Set(emergencyCases.map((item) => item.patient_id)),
        ];

        const { data: patientData, error: patientError } = await supabase
          .from("patients")
          .select("id, full_name, phone, gender")
          .in("id", patientIds);

        if (patientError) {
          throw patientError;
        }

        const patientMap: Record<string, Patient> = {};

        (patientData || []).forEach((patient) => {
          patientMap[patient.id] = {
            ...patient,
            age: calculateAge(patient.id, emergencyCases),
          };
        });

        setPatients(patientMap);
      } else {
        setPatients({});
      }
    } catch (err) {
      console.error(err);
      setError("Unable to load emergency cases.");
    } finally {
      setLoading(false);
    }
  }

  function calculateAge(
    patientId: string,
    emergencyCases: EmergencyCase[]
  ): number | null {
    void patientId;
    void emergencyCases;

    return null;
  }

  function getPriorityLabel(priority: string) {
    switch (priority.toLowerCase()) {
      case "critical":
        return "Critical";
      case "high":
        return "High";
      case "medium":
        return "Medium";
      case "low":
        return "Low";
      default:
        return priority;
    }
  }

  function getPriorityClasses(priority: string) {
    switch (priority.toLowerCase()) {
      case "critical":
        return "border-red-300 bg-red-50 text-red-700";
      case "high":
        return "border-orange-300 bg-orange-50 text-orange-700";
      case "medium":
        return "border-yellow-300 bg-yellow-50 text-yellow-700";
      default:
        return "border-gray-300 bg-gray-50 text-gray-700";
    }
  }

  function getStatusClasses(status: string) {
    switch (status.toLowerCase()) {
      case "active":
        return "border-red-300 bg-red-50 text-red-700";
      case "resolved":
        return "border-green-300 bg-green-50 text-green-700";
      case "cancelled":
        return "border-gray-300 bg-gray-50 text-gray-600";
      default:
        return "border-gray-300 bg-gray-50 text-gray-700";
    }
  }

  const activeCases = cases.filter(
    (item) => item.status.toLowerCase() === "active"
  );

  const resolvedCases = cases.filter(
    (item) => item.status.toLowerCase() === "resolved"
  );

  return (
    <div className="dashboard-page">
      <div className="mx-auto max-w-7xl px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="mb-2 text-sm font-medium uppercase tracking-wider dashboard-muted">
              Hospital Emergency Management
            </p>

            <h1 className="text-3xl font-bold tracking-tight">
              🚨 Emergency Cases
            </h1>

            <p className="mt-2 text-sm dashboard-muted">
              Monitor emergency cases currently assigned to your hospital.
            </p>
          </div>

          <button
            onClick={loadEmergencyCases}
            className="dashboard-hover-glow rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-surface)] px-4 py-2 text-sm font-semibold transition"
          >
            Refresh
          </button>
        </div>

        {/* Summary */}
        <div className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="dashboard-panel rounded-2xl border border-[var(--dashboard-border)] p-5 shadow-sm">
            <p className="text-sm dashboard-muted">Active Emergencies</p>

            <p className="mt-2 text-3xl font-bold">
              {loading ? "—" : activeCases.length}
            </p>
          </div>

          <div className="dashboard-panel rounded-2xl border border-[var(--dashboard-border)] p-5 shadow-sm">
            <p className="text-sm dashboard-muted">Total Emergency Cases</p>

            <p className="mt-2 text-3xl font-bold">
              {loading ? "—" : cases.length}
            </p>
          </div>

          <div className="dashboard-panel rounded-2xl border border-[var(--dashboard-border)] p-5 shadow-sm">
            <p className="text-sm dashboard-muted">Resolved Cases</p>

            <p className="mt-2 text-3xl font-bold">
              {loading ? "—" : resolvedCases.length}
            </p>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-6 rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Loading */}
        {loading ? (
          <div className="dashboard-panel rounded-2xl border border-[var(--dashboard-border)] p-10 text-center shadow-sm">
            <p className="text-sm dashboard-muted">
              Loading emergency cases...
            </p>
          </div>
        ) : cases.length === 0 ? (
          /* Empty State */
          <div className="dashboard-panel rounded-2xl border border-[var(--dashboard-border)] p-12 text-center shadow-sm">
            <div className="text-5xl">🚨</div>

            <h2 className="mt-4 text-xl font-bold">
              No emergency cases
            </h2>

            <p className="mx-auto mt-2 max-w-md text-sm dashboard-muted">
              Emergency cases will appear here when a case is assigned to
              this hospital.
            </p>
          </div>
        ) : (
          /* Emergency Cases */
          <div className="space-y-5">
            {cases.map((emergencyCase) => {
              const patient = patients[emergencyCase.patient_id];

              return (
                <div
                  key={emergencyCase.id}
                  className="dashboard-panel dashboard-hover-glow rounded-2xl border border-[var(--dashboard-border)] p-6 shadow-sm"
                >
                  <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                    {/* Patient / Case */}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-3">
                        <h2 className="text-xl font-bold">
                          {patient?.full_name || "Patient"}
                        </h2>

                        <span
                          className={`rounded-full border px-3 py-1 text-xs font-semibold ${getPriorityClasses(
                            emergencyCase.priority
                          )}`}
                        >
                          {getPriorityLabel(emergencyCase.priority)}
                        </span>

                        <span
                          className={`rounded-full border px-3 py-1 text-xs font-semibold ${getStatusClasses(
                            emergencyCase.status
                          )}`}
                        >
                          {emergencyCase.status}
                        </span>
                      </div>

                      <div className="mt-4 grid gap-4 sm:grid-cols-2">
                        <div>
                          <p className="text-xs uppercase tracking-wide dashboard-muted">
                            Symptoms
                          </p>

                          <p className="mt-1 text-sm">
                            {emergencyCase.symptoms_summary ||
                              "No symptoms summary available."}
                          </p>
                        </div>

                        <div>
                          <p className="text-xs uppercase tracking-wide dashboard-muted">
                            Immediate Action
                          </p>

                          <p className="mt-1 text-sm">
                            {emergencyCase.immediate_action ||
                              "No immediate action recorded."}
                          </p>
                        </div>
                      </div>

                      {patient && (
                        <div className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-sm dashboard-muted">
                          {patient.phone && (
                            <span>📞 {patient.phone}</span>
                          )}

                          {patient.gender && (
                            <span>Gender: {patient.gender}</span>
                          )}
                        </div>
                      )}

                      <p className="mt-4 text-xs dashboard-muted">
                        Created{" "}
                        {new Date(
                          emergencyCase.created_at
                        ).toLocaleString()}
                      </p>
                    </div>

                    {/* Action */}
                    {emergencyCase.referral_id && (
                      <Link
                        href={`/dashboard/referrals/${emergencyCase.referral_id}`}
                        className="shrink-0 rounded-xl border border-[var(--dashboard-border)] px-4 py-2 text-sm font-semibold transition hover:bg-[var(--dashboard-surface-muted)]"
                      >
                        View Referral
                      </Link>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}