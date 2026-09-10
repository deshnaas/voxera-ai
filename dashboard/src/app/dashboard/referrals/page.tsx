"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

type Referral = {
  id: string;
  patient_id: string;
  reason: string;
  urgency: string;
  status: string;
  required_service: string | null;
  created_at: string;
};

type Patient = {
  id: string;
  full_name: string;
};

type IncomingReferral = Referral & {
  patient: Patient | null;
};

export default function ReferralsPage() {
  const [referrals, setReferrals] = useState<IncomingReferral[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function loadReferrals() {
      setLoading(true);
      setMessage("");

      // --------------------------------------------------
      // 1. Get logged-in hospital staff member
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
      // 3. Load referrals sent to this hospital
      //
      // The hospital does NOT select referrals.
      // AI has already selected the receiving hospital.
      // --------------------------------------------------

      const { data: referralsData, error: referralsError } =
        await supabase
          .from("referrals")
          .select(
            `
              id,
              patient_id,
              reason,
              urgency,
              status,
              required_service,
              created_at
            `
          )
          .eq("receiving_facility_id", facilityId)
          .order("created_at", {
            ascending: false,
          });

      if (referralsError) {
        setMessage("Referral information could not be loaded.");
        setLoading(false);
        return;
      }

      const referralRows = (referralsData ?? []) as Referral[];

      // --------------------------------------------------
      // 4. Load patients connected to referrals
      // --------------------------------------------------

      const patientIds = [
        ...new Set(
          referralRows.map(
            (referral) => referral.patient_id
          )
        ),
      ];

      let patients: Patient[] = [];

      if (patientIds.length > 0) {
        const { data: patientsData, error: patientsError } =
          await supabase
            .from("patients")
            .select("id, full_name")
            .in("id", patientIds);

        if (patientsError) {
          setMessage("Patient information could not be loaded.");
          setLoading(false);
          return;
        }

        patients = (patientsData ?? []) as Patient[];
      }

      // --------------------------------------------------
      // 5. Combine referral + patient information
      // --------------------------------------------------

      const combinedReferrals: IncomingReferral[] =
        referralRows.map((referral) => ({
          ...referral,
          patient:
            patients.find(
              (patient) =>
                patient.id === referral.patient_id
            ) ?? null,
        }));

      setReferrals(combinedReferrals);
      setLoading(false);
    }

    loadReferrals();
  }, []);

  // --------------------------------------------------
  // Loading state
  // --------------------------------------------------

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading incoming referrals...
        </p>
      </main>
    );
  }

  // --------------------------------------------------
  // Error state
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
  // Referral page
  // --------------------------------------------------

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* Page Header */}
        <section className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">
          <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
            Referral Management
          </p>

          <div className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-4xl font-bold">
                Incoming Referrals
              </h1>

              <p className="dashboard-muted mt-2">
                Patient referrals received from Voxera AI.
              </p>
            </div>

            <div className="rounded-full border-2 border-[#D4AF37] px-4 py-2 text-sm font-bold">
              {referrals.length}{" "}
              {referrals.length === 1
                ? "Referral"
                : "Referrals"}
            </div>
          </div>
        </section>

        {/* Referrals */}
        {referrals.length === 0 ? (
          <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-10 text-center">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full border-2 border-[#D4AF37] text-2xl">
              ✓
            </div>

            <h2 className="mt-5 text-2xl font-bold">
              No Incoming Referrals
            </h2>

            <p className="dashboard-muted mx-auto mt-2 max-w-lg">
              There are currently no referrals assigned to
              this hospital.
            </p>
          </section>
        ) : (
          <section className="mt-8 space-y-5">
            {referrals.map((referral) => {
              const urgency =
                referral.urgency?.toLowerCase();

              const urgencyClass =
                urgency === "emergency" ||
                urgency === "high"
                  ? "border-red-500 text-red-600"
                  : urgency === "medium-high"
                    ? "border-orange-500 text-orange-600"
                    : "border-[#D4AF37] text-[#9a7b13]";

              return (
                <article
                  key={referral.id}
                  className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6"
                >
                  <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">

                    {/* Patient / Referral Info */}
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-3">
                        <h2 className="text-2xl font-bold">
                          {referral.patient?.full_name ??
                            "Unknown Patient"}
                        </h2>

                        <span
                          className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${urgencyClass}`}
                        >
                          {referral.urgency}
                        </span>

                        <span className="rounded-full border border-gray-400 px-3 py-1 text-xs font-semibold uppercase">
                          {referral.status}
                        </span>
                      </div>

                      <p className="dashboard-muted mt-3 text-sm">
                        Referral ID: {referral.id}
                      </p>

                      <div className="mt-4 grid gap-4 sm:grid-cols-2">
                        <div>
                          <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                            Reason
                          </p>

                          <p className="mt-1 font-medium">
                            {referral.reason}
                          </p>
                        </div>

                        <div>
                          <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                            Required Service
                          </p>

                          <p className="mt-1 font-medium">
                            {referral.required_service ??
                              "Not specified"}
                          </p>
                        </div>
                      </div>
                    </div>

                    {/* Action */}
                    <div className="shrink-0">
                      <Link
                        href={`/dashboard/referrals/${referral.id}`}
                        className="dashboard-hover-glow inline-flex w-full items-center justify-center rounded-lg border-2 border-[#D4AF37] px-6 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black lg:w-auto"
                      >
                        View Referral
                      </Link>
                    </div>
                  </div>
                </article>
              );
            })}
          </section>
        )}
      </div>
    </main>
  );
}