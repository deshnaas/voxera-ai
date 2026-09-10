"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

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

export default function PatientsPage() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function loadPatients() {
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
      // 2. Find the hospital assigned to this user
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
      // 3. Find patients connected to active referrals
      //    received by this hospital.
      //
      // Rejected referrals are excluded.
      // --------------------------------------------------

      const { data: referralsData, error: referralsError } =
        await supabase
          .from("referrals")
          .select("patient_id")
          .eq("receiving_facility_id", facilityId)
          .neq("status", "rejected");

      if (referralsError) {
        setMessage("Patient referrals could not be loaded.");
        setLoading(false);
        return;
      }

      const patientIds = [
        ...new Set(
          (referralsData ?? []).map(
            (referral) => referral.patient_id
          )
        ),
      ];

      if (patientIds.length === 0) {
        setPatients([]);
        setLoading(false);
        return;
      }

      // --------------------------------------------------
      // 4. Load patient information
      // --------------------------------------------------

      const { data: patientsData, error: patientsError } =
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
          .in("id", patientIds)
          .order("full_name", {
            ascending: true,
          });

      if (patientsError) {
        setMessage("Patient information could not be loaded.");
        setLoading(false);
        return;
      }

      setPatients((patientsData ?? []) as Patient[]);
      setLoading(false);
    }

    loadPatients();
  }, []);

  // --------------------------------------------------
  // Loading
  // --------------------------------------------------

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading patients...
        </p>
      </main>
    );
  }

  // --------------------------------------------------
  // Error
  // --------------------------------------------------

  if (message) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <div className="dashboard-panel rounded-2xl border-2 border-[#D4AF37] p-8">
          <h1 className="text-2xl font-bold">
            VOXERA
          </h1>

          <p className="mt-4">
            {message}
          </p>
        </div>
      </main>
    );
  }

  // --------------------------------------------------
  // Patients page
  // --------------------------------------------------

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* Header */}
        <section className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">
          <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
            Patient Management
          </p>

          <div className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-4xl font-bold">
                Patients
              </h1>

              <p className="dashboard-muted mt-2">
                Patients with active referrals at this hospital.
              </p>
            </div>

            <div className="rounded-full border-2 border-[#D4AF37] px-4 py-2 text-sm font-bold">
              {patients.length}{" "}
              {patients.length === 1
                ? "Patient"
                : "Patients"}
            </div>
          </div>
        </section>

        {/* Empty State */}
        {patients.length === 0 ? (
          <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-10 text-center">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full border-2 border-[#D4AF37] text-2xl">
              ♙
            </div>

            <h2 className="mt-5 text-2xl font-bold">
              No Patients Yet
            </h2>

            <p className="dashboard-muted mx-auto mt-2 max-w-lg">
              Patients will appear here when Voxera AI
              sends an active referral to this hospital.
            </p>
          </section>
        ) : (
          /* Patient List */
          <section className="mt-8 grid gap-5 md:grid-cols-2">
            {patients.map((patient) => (
              <article
                key={patient.id}
                className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                      Patient
                    </p>

                    <h2 className="mt-2 text-2xl font-bold">
                      {patient.full_name}
                    </h2>
                  </div>

                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 border-[#D4AF37]">
                    ♙
                  </span>
                </div>

                <div className="mt-6 grid gap-4 sm:grid-cols-2">
                  <PatientInfo
                    label="Phone"
                    value={
                      patient.phone ??
                      "Not available"
                    }
                  />

                  <PatientInfo
                    label="Gender"
                    value={
                      patient.gender ??
                      "Not available"
                    }
                  />

                  <PatientInfo
                    label="Language"
                    value={
                      patient.preferred_language ??
                      "Not available"
                    }
                  />

                  <PatientInfo
                    label="District"
                    value={
                      patient.district ??
                      "Not available"
                    }
                  />
                </div>

                {/* View Patient Details */}
                <div className="mt-6 border-t border-gray-300 pt-5">
                  <Link
                    href={`/dashboard/patients/${patient.id}`}
                    className="dashboard-hover-glow inline-flex w-full items-center justify-center rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
                  >
                    View Patient Details
                  </Link>
                </div>
              </article>
            ))}
          </section>
        )}
      </div>
    </main>
  );
}

// --------------------------------------------------
// Reusable patient information item
// --------------------------------------------------

function PatientInfo({
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