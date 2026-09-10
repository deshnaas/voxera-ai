"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { supabase } from "@/lib/supabase";
import CallHistory from "../../components/CallHistory";

type Patient = {
  id: string;
  full_name: string;
  phone: string | null;
  date_of_birth: string | null;
  gender: string | null;
  preferred_language: string | null;
  village_or_locality: string | null;
  district: string | null;
};

type Referral = {
  id: string;
  reason: string;
  urgency: string;
  status: string;
  required_service: string | null;
  ai_summary: string | null;
  ai_recommendation: string | null;
  recommended_department: string | null;
  created_at: string;
};

type Appointment = {
  id: string;
  appointment_date: string;
  appointment_time: string | null;
  department: string | null;
  doctor_name: string | null;
  reason: string | null;
  status: string;
};

export default function PatientDetailsPage() {
  const params = useParams();
  const patientId = params.patientId as string;

  const [patient, setPatient] = useState<Patient | null>(null);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function loadPatientDetails() {
      setLoading(true);
      setMessage("");

      // --------------------------------------------------
      // 1. Get logged-in hospital user
      // --------------------------------------------------

      const {
        data: { user },
        error: userError,
      } = await supabase.auth.getUser();

      if (userError || !user) {
        setMessage("You are not logged in.");
        setLoading(false);
        return;
      }

      // --------------------------------------------------
      // 2. Find hospital assigned to this user
      // --------------------------------------------------

      const { data: hospitalUser, error: hospitalUserError } =
        await supabase
          .from("hospital_users")
          .select("facility_id")
          .eq("user_id", user.id)
          .single();

      if (hospitalUserError || !hospitalUser) {
        setMessage("Hospital information could not be found.");
        setLoading(false);
        return;
      }

      const facilityId = hospitalUser.facility_id;

      // --------------------------------------------------
      // 3. Load the selected patient
      // --------------------------------------------------

      const { data: patientData, error: patientError } =
        await supabase
          .from("patients")
          .select(
            `
              id,
              full_name,
              phone,
              date_of_birth,
              gender,
              preferred_language,
              village_or_locality,
              district
            `
          )
          .eq("id", patientId)
          .single();

      if (patientError || !patientData) {
        setMessage("Patient could not be found.");
        setLoading(false);
        return;
      }

      // --------------------------------------------------
      // 4. Verify this patient actually belongs to this
      //    hospital through a received referral.
      // --------------------------------------------------

      const { data: referralData, error: referralError } =
        await supabase
          .from("referrals")
          .select(
            `
              id,
              reason,
              urgency,
              status,
              required_service,
              ai_summary,
              ai_recommendation,
              recommended_department,
              created_at
            `
          )
          .eq("patient_id", patientId)
          .eq("receiving_facility_id", facilityId)
          .order("created_at", {
            ascending: false,
          });

      if (referralError) {
        setMessage("Patient referral history could not be loaded.");
        setLoading(false);
        return;
      }

      const patientReferrals =
        (referralData ?? []) as Referral[];

      // --------------------------------------------------
      // 5. Load appointments for this patient at this
      //    hospital.
      // --------------------------------------------------

      const { data: appointmentData, error: appointmentError } =
        await supabase
          .from("appointments")
          .select(
            `
              id,
              appointment_date,
              appointment_time,
              department,
              doctor_name,
              reason,
              status
            `
          )
          .eq("patient_id", patientId)
          .eq("facility_id", facilityId)
          .order("appointment_date", {
            ascending: false,
          });

      if (appointmentError) {
        setMessage("Patient appointments could not be loaded.");
        setLoading(false);
        return;
      }

      setPatient(patientData as Patient);
      setReferrals(patientReferrals);
      setAppointments(
        (appointmentData ?? []) as Appointment[]
      );

      setLoading(false);
    }

    if (patientId) {
      loadPatientDetails();
    }
  }, [patientId]);

  // --------------------------------------------------
  // Loading state
  // --------------------------------------------------

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading patient details...
        </p>
      </main>
    );
  }

  // --------------------------------------------------
  // Error state
  // --------------------------------------------------

  if (message || !patient) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <div className="dashboard-panel rounded-2xl border-2 border-[#D4AF37] p-8">
          <h1 className="text-2xl font-bold">
            VOXERA
          </h1>

          <p className="mt-4">
            {message || "Patient could not be found."}
          </p>

          <Link
            href="/dashboard/patients"
            className="mt-6 inline-flex rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
          >
            Back to Patients
          </Link>
        </div>
      </main>
    );
  }

  // --------------------------------------------------
  // Helpers
  // --------------------------------------------------

  function formatDate(date: string | null) {
    if (!date) {
      return "Not available";
    }

    return new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }).format(new Date(date));
  }

  function formatTime(time: string | null) {
    if (!time) {
      return "Not specified";
    }

    return time.slice(0, 5);
  }

  function getStatusClass(status: string) {
    const normalized = status.toLowerCase();

    if (normalized === "accepted") {
      return "border-green-500 text-green-600";
    }

    if (
      normalized === "rejected" ||
      normalized === "missed" ||
      normalized === "cancelled"
    ) {
      return "border-red-500 text-red-600";
    }

    if (
      normalized === "completed"
    ) {
      return "border-blue-500 text-blue-600";
    }

    return "border-[#D4AF37] text-[#9a7b13]";
  }

  // --------------------------------------------------
  // Page
  // --------------------------------------------------

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* Back */}
        <Link
          href="/dashboard/patients"
          className="dashboard-muted inline-flex items-center gap-2 text-sm font-semibold transition-colors hover:text-[#9a7b13]"
        >
          ← Back to Patients
        </Link>

        {/* Patient Header */}
        <section className="dashboard-panel dashboard-hover-glow mt-5 rounded-2xl border-2 border-[#D4AF37] p-8">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-5">
              <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border-2 border-[#D4AF37] text-2xl">
                ♙
              </div>

              <div>
                <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                  Patient Profile
                </p>

                <h1 className="mt-1 text-4xl font-bold">
                  {patient.full_name}
                </h1>

                <p className="dashboard-muted mt-2 text-sm">
                  Patient ID: {patient.id}
                </p>
              </div>
            </div>

            <div className="rounded-full border-2 border-[#D4AF37] px-4 py-2 text-sm font-bold">
              {referrals.length}{" "}
              {referrals.length === 1
                ? "Referral"
                : "Referrals"}
            </div>
          </div>
        </section>

        {/* Patient Information */}
        <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-8">
          <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
            Personal Information
          </p>

          <h2 className="mt-2 text-2xl font-bold">
            Patient Details
          </h2>

          <div className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <InfoItem
              label="Full Name"
              value={patient.full_name}
            />

            <InfoItem
              label="Phone"
              value={patient.phone ?? "Not available"}
            />

            <InfoItem
              label="Date of Birth"
              value={formatDate(patient.date_of_birth)}
            />

            <InfoItem
              label="Gender"
              value={patient.gender ?? "Not available"}
            />

            <InfoItem
              label="Preferred Language"
              value={
                patient.preferred_language ??
                "Not available"
              }
            />

            <InfoItem
              label="Locality"
              value={
                patient.village_or_locality ??
                "Not available"
              }
            />

            <InfoItem
              label="District"
              value={
                patient.district ??
                "Not available"
              }
            />
          </div>
        </section>

        {/* Referral History */}
        <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-8">
          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Referral History
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Patient Referrals
            </h2>
          </div>

          {referrals.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-6 text-center">
              <p className="font-semibold">
                No referrals found
              </p>
            </div>
          ) : (
            <div className="mt-6 space-y-5">
              {referrals.map((referral) => (
                <article
                  key={referral.id}
                  className="rounded-xl border border-gray-300 p-5"
                >
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-3">
                        <span
                          className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${getStatusClass(
                            referral.status
                          )}`}
                        >
                          {referral.status}
                        </span>

                        <span
                          className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${
                            referral.urgency.toLowerCase() ===
                              "high" ||
                            referral.urgency.toLowerCase() ===
                              "emergency"
                              ? "border-red-500 text-red-600"
                              : "border-[#D4AF37] text-[#9a7b13]"
                          }`}
                        >
                          {referral.urgency}
                        </span>
                      </div>

                      <p className="dashboard-muted mt-4 text-xs font-semibold uppercase tracking-wider">
                        Reason
                      </p>

                      <p className="mt-1 font-medium">
                        {referral.reason}
                      </p>
                    </div>

                    <div className="dashboard-muted text-sm">
                      {formatDate(referral.created_at)}
                    </div>
                  </div>

                  <div className="mt-5 grid gap-5 border-t border-gray-300 pt-5 sm:grid-cols-2">
                    <InfoItem
                      label="Required Service"
                      value={
                        referral.required_service ??
                        "Not specified"
                      }
                    />

                    <InfoItem
                      label="Recommended Department"
                      value={
                        referral.recommended_department ??
                        "Not specified"
                      }
                    />
                  </div>

                  {referral.ai_summary && (
                    <div className="mt-5 rounded-lg border border-gray-300 p-4">
                      <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                        AI Clinical Summary
                      </p>

                      <p className="mt-2 text-sm leading-6">
                        {referral.ai_summary}
                      </p>
                    </div>
                  )}

                  {referral.ai_recommendation && (
                    <div className="mt-4 rounded-lg border border-gray-300 p-4">
                      <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                        AI Recommendation
                      </p>

                      <p className="mt-2 text-sm leading-6">
                        {referral.ai_recommendation}
                      </p>
                    </div>
                  )}

                  <div className="mt-5">
                    <Link
                      href={`/dashboard/referrals/${referral.id}`}
                      className="dashboard-hover-glow inline-flex rounded-lg border-2 border-[#D4AF37] px-5 py-2.5 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
                    >
                      View Referral
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        {/* Voxera calls, conversations & summaries */}
        <CallHistory patientId={patient.id} />

        {/* Appointments */}
        <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-8">
          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Appointment History
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Appointments
            </h2>
          </div>

          {appointments.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-6 text-center">
              <p className="font-semibold">
                No appointments found
              </p>

              <p className="dashboard-muted mt-2 text-sm">
                Appointments created for this patient will appear here.
              </p>
            </div>
          ) : (
            <div className="mt-6 overflow-x-auto">
              <table className="w-full min-w-[700px] border-collapse">
                <thead>
                  <tr className="border-b-2 border-[#D4AF37] text-left">
                    <th className="px-4 py-3 text-sm font-bold">
                      Date
                    </th>

                    <th className="px-4 py-3 text-sm font-bold">
                      Time
                    </th>

                    <th className="px-4 py-3 text-sm font-bold">
                      Department
                    </th>

                    <th className="px-4 py-3 text-sm font-bold">
                      Doctor
                    </th>

                    <th className="px-4 py-3 text-sm font-bold">
                      Status
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {appointments.map((appointment) => (
                    <tr
                      key={appointment.id}
                      className="border-b border-gray-300"
                    >
                      <td className="px-4 py-4 text-sm">
                        {formatDate(
                          appointment.appointment_date
                        )}
                      </td>

                      <td className="px-4 py-4 text-sm">
                        {formatTime(
                          appointment.appointment_time
                        )}
                      </td>

                      <td className="px-4 py-4 text-sm">
                        {appointment.department ??
                          "Not specified"}
                      </td>

                      <td className="px-4 py-4 text-sm">
                        {appointment.doctor_name ??
                          "Not specified"}
                      </td>

                      <td className="px-4 py-4">
                        <span
                          className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${getStatusClass(
                            appointment.status
                          )}`}
                        >
                          {appointment.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

      </div>
    </main>
  );
}

// --------------------------------------------------
// Reusable information component
// --------------------------------------------------

function InfoItem({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div>
      <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
        {label}
      </p>

      <p className="mt-1 font-medium">
        {value}
      </p>
    </div>
  );
}