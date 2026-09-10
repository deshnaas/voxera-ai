"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

type Facility = {
  id: string;
  name: string;
  type: string;
  location: string;
  district: string;
};

type Bed = {
  total_beds: number;
  occupied_beds: number;
};

type Appointment = {
  id: string;
};

type Referral = {
  id: string;
  patient_id: string;
  reason: string;
  urgency: string;
  status: string;
  created_at: string;
};

type Patient = {
  id: string;
  full_name: string;
};

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

type IncomingReferral = Referral & {
  patient: Patient | null;
};

export default function DashboardPage() {
  const [facility, setFacility] = useState<Facility | null>(null);
  const [availableBeds, setAvailableBeds] = useState(0);
  const [appointmentsToday, setAppointmentsToday] = useState(0);
  const [awaitingAction, setAwaitingAction] = useState(0);
  const [highRiskCases, setHighRiskCases] = useState(0);
  const [activeEmergencies, setActiveEmergencies] = useState(0);
  const [incomingReferrals, setIncomingReferrals] = useState<
    IncomingReferral[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function loadDashboard() {
      setLoading(true);
      setMessage("");

      // --------------------------------------------------
      // 1. Get logged-in user
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
      // 2. Find hospital belonging to logged-in user
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
      // 3. Load hospital information
      // --------------------------------------------------

      const { data: facilityData, error: facilityError } =
        await supabase
          .from("facilities")
          .select("id, name, type, location, district")
          .eq("id", facilityId)
          .single();

      if (facilityError || !facilityData) {
        setMessage("Hospital details could not be loaded.");
        setLoading(false);
        return;
      }

      // --------------------------------------------------
      // 4. Load bed availability
      // --------------------------------------------------

      const { data: bedsData, error: bedsError } =
        await supabase
          .from("facility_beds")
          .select("total_beds, occupied_beds")
          .eq("facility_id", facilityId);

      if (bedsError) {
        setMessage("Bed information could not be loaded.");
        setLoading(false);
        return;
      }

      const beds = (bedsData ?? []) as Bed[];

      const totalAvailableBeds = beds.reduce(
        (total, bed) =>
          total + (bed.total_beds - bed.occupied_beds),
        0
      );

      // --------------------------------------------------
      // 5. Load today's appointments
      // --------------------------------------------------

      const today = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Kolkata",
      }).format(new Date());

      const {
        data: appointmentsData,
        error: appointmentsError,
      } = await supabase
        .from("appointments")
        .select("id")
        .eq("facility_id", facilityId)
        .eq("appointment_date", today);

      if (appointmentsError) {
        setMessage("Appointment information could not be loaded.");
        setLoading(false);
        return;
      }

      const appointments =
        (appointmentsData ?? []) as Appointment[];

      // --------------------------------------------------
      // 6. Load incoming pending referrals
      // --------------------------------------------------

      const { data: referralsData, error: referralsError } =
        await supabase
          .from("referrals")
          .select(
            "id, patient_id, reason, urgency, status, created_at"
          )
          .eq("receiving_facility_id", facilityId)
          .eq("status", "pending")
          .order("created_at", {
            ascending: false,
          });

      if (referralsError) {
        setMessage("Referral information could not be loaded.");
        setLoading(false);
        return;
      }

      const referrals = (referralsData ?? []) as Referral[];

      // --------------------------------------------------
      // 7. Load patients belonging to referrals
      // --------------------------------------------------

      const patientIds = referrals.map(
        (referral) => referral.patient_id
      );

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
      // 8. Combine referrals with patients
      // --------------------------------------------------

      const combinedReferrals: IncomingReferral[] =
        referrals.map((referral) => ({
          ...referral,
          patient:
            patients.find(
              (patient) =>
                patient.id === referral.patient_id
            ) ?? null,
        }));

      // --------------------------------------------------
      // 9. Calculate high-risk incoming referrals
      //
      // IMPORTANT:
      // High-Risk Cases only counts HIGH or EMERGENCY
      // referrals that are still PENDING.
      //
      // Once the hospital accepts the referral,
      // it is removed from this number.
      // --------------------------------------------------

      const highRiskCount = referrals.filter(
        (referral) => {
          const urgency =
            referral.urgency?.toLowerCase();

          return (
            urgency === "high" ||
            urgency === "emergency"
          );
        }
      ).length;

      // --------------------------------------------------
      // 10. Load active emergency cases
      //
      // IMPORTANT:
      // Emergency cases are independent from referral
      // status.
      //
      // An emergency can remain ACTIVE even after
      // the hospital accepts the referral.
      // --------------------------------------------------

      const {
        data: emergencyData,
        error: emergencyError,
      } = await supabase
        .from("emergency_cases")
        .select("id, patient_id, facility_id, referral_id, priority, status, symptoms_summary, immediate_action, created_at")
        .eq("facility_id", facilityId)
        .eq("status", "active");

      if (emergencyError) {
        setMessage("Emergency information could not be loaded.");
        setLoading(false);
        return;
      }

      const emergencyCases =
        (emergencyData ?? []) as EmergencyCase[];

      // --------------------------------------------------
      // 11. Set dashboard data
      // --------------------------------------------------

      setFacility(facilityData);
      setAvailableBeds(totalAvailableBeds);
      setAppointmentsToday(appointments.length);
      setAwaitingAction(referrals.length);
      setHighRiskCases(highRiskCount);
      setActiveEmergencies(emergencyCases.length);
      setIncomingReferrals(combinedReferrals);

      setLoading(false);
    }

    loadDashboard();
  }, []);

  // --------------------------------------------------
  // Loading
  // --------------------------------------------------

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading hospital dashboard...
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
        <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">
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
  // Dashboard
  // --------------------------------------------------

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* Hospital Header */}
        <section className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">
          <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
            VOXERA Hospital Dashboard
          </p>

          <h1 className="mt-2 text-4xl font-bold">
            {facility?.name}
          </h1>

          <p className="dashboard-muted mt-2">
            {facility?.location} • {facility?.district}
          </p>

          <div className="mt-6 inline-block rounded-full border-2 border-[#D4AF37] px-4 py-2 text-sm font-semibold">
            {facility?.type}
          </div>
        </section>

        {/* Statistics */}
        <section className="mt-8 grid gap-6 md:grid-cols-2 lg:grid-cols-5">

          {/* High Risk */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              High-Risk Cases
            </p>

            <p className="mt-3 text-4xl font-bold">
              {highRiskCases}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              High-urgency referrals received
            </p>
          </div>

          {/* Awaiting Action */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Awaiting Action
            </p>

            <p className="mt-3 text-4xl font-bold">
              {awaitingAction}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Pending referrals
            </p>
          </div>

          {/* Active Emergencies */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Active Emergencies
            </p>

            <p className="mt-3 text-4xl font-bold">
              {activeEmergencies}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Emergencies currently active
            </p>
          </div>

          {/* Appointments */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Appointments Today
            </p>

            <p className="mt-3 text-4xl font-bold">
              {appointmentsToday}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Scheduled for today
            </p>
          </div>

          {/* Beds */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Available Beds
            </p>

            <p className="mt-3 text-4xl font-bold">
              {availableBeds}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Total beds currently available
            </p>
          </div>
        </section>

        {/* Incoming Referrals */}
        <section className="dashboard-panel dashboard-hover-glow mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">

          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                Referral Management
              </p>

              <h2 className="mt-2 text-2xl font-bold">
                Incoming Referrals
              </h2>
            </div>

            <Link
              href="/dashboard/referrals"
              className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] px-4 py-2 text-center text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
            >
              View All
            </Link>
          </div>

          {incomingReferrals.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-6 text-center">
              <p className="font-semibold">
                No incoming referrals
              </p>

              <p className="dashboard-muted mt-2 text-sm">
                New referrals will appear here.
              </p>
            </div>
          ) : (
            <div className="mt-6 space-y-4">
              {incomingReferrals.map((referral) => (
                <div
                  key={referral.id}
                  className="dashboard-hover-glow flex flex-col gap-4 rounded-xl border border-gray-300 p-5 transition-all md:flex-row md:items-center md:justify-between"
                >
                  <div>
                    <p className="dashboard-muted text-sm">
                      Patient
                    </p>

                    <h3 className="mt-1 text-xl font-bold">
                      {referral.patient?.full_name ??
                        "Unknown Patient"}
                    </h3>

                    <p className="dashboard-muted mt-2 text-sm">
                      {referral.reason}
                    </p>
                  </div>

                  <div className="flex items-center gap-3">
                    <span className="rounded-full border-2 border-red-500 px-3 py-1 text-sm font-bold">
                      {referral.urgency}
                    </span>

                    <Link
                      href={`/dashboard/referrals/${referral.id}`}
                      className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] px-5 py-2 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
                    >
                      View Referral
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}