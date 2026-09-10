"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
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

type Referral = {
  id: string;
  patient_id: string;
  ai_assessment_id: string | null;
  receiving_facility_id: string;
  reason: string;
  urgency: string;
  required_service: string | null;
  expected_arrival: string | null;
  notes: string | null;
  status: string;
  rejection_reason: string | null;
  ai_summary: string | null;
  ai_recommendation: string | null;
  recommended_department: string | null;
  created_at: string;
};

type Assessment = {
  id: string;
  severity: string;
  risk_level: string | null;
  symptoms_summary: string | null;
  ai_summary: string | null;
  ai_recommendation: string | null;
  recommended_department: string | null;
  referral_required: boolean;
  confidence_score: number | null;
  assessed_at: string;
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

export default function ReferralDetailsPage() {
  const params = useParams();
  const referralId = params.referralId as string;

  const [referral, setReferral] = useState<Referral | null>(null);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [assessment, setAssessment] =
    useState<Assessment | null>(null);

  const [appointments, setAppointments] =
    useState<Appointment[]>([]);

  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [appointmentLoading, setAppointmentLoading] =
    useState(false);

  const [message, setMessage] = useState("");
  const [rejectionReason, setRejectionReason] =
    useState("");

  // Appointment form
  const [appointmentDate, setAppointmentDate] =
    useState("");
  const [appointmentTime, setAppointmentTime] =
    useState("");
  const [appointmentDepartment, setAppointmentDepartment] =
    useState("");
  const [doctorName, setDoctorName] = useState("");
  const [appointmentReason, setAppointmentReason] =
    useState("");

  useEffect(() => {
    async function loadReferralDetails() {
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
      // 2. Get hospital mapping
      // --------------------------------------------------

      const {
        data: hospitalUser,
        error: hospitalUserError,
      } = await supabase
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
      // 3. Load referral
      // --------------------------------------------------

      const {
        data: referralData,
        error: referralError,
      } = await supabase
        .from("referrals")
        .select(
          `
            id,
            patient_id,
            ai_assessment_id,
            receiving_facility_id,
            reason,
            urgency,
            required_service,
            expected_arrival,
            notes,
            status,
            rejection_reason,
            ai_summary,
            ai_recommendation,
            recommended_department,
            created_at
          `
        )
        .eq("id", referralId)
        .eq("receiving_facility_id", facilityId)
        .single();

      if (referralError || !referralData) {
        setMessage(
          "Referral could not be found or you do not have access to it."
        );
        setLoading(false);
        return;
      }

      const currentReferral = referralData as Referral;

      // --------------------------------------------------
      // 4. Load patient
      // --------------------------------------------------

      const {
        data: patientData,
        error: patientError,
      } = await supabase
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
        .eq("id", currentReferral.patient_id)
        .single();

      if (patientError || !patientData) {
        setMessage("Patient information could not be loaded.");
        setLoading(false);
        return;
      }

      // --------------------------------------------------
      // 5. Load AI assessment
      // --------------------------------------------------

      let assessmentData: Assessment | null = null;

      if (currentReferral.ai_assessment_id) {
        const { data } = await supabase
          .from("ai_assessments")
          .select(
            `
              id,
              severity,
              risk_level,
              symptoms_summary,
              ai_summary,
              ai_recommendation,
              recommended_department,
              referral_required,
              confidence_score,
              assessed_at
            `
          )
          .eq("id", currentReferral.ai_assessment_id)
          .single();

        assessmentData =
          (data as Assessment) ?? null;
      }

      // --------------------------------------------------
      // 6. Load appointments for this referral
      // --------------------------------------------------

      const {
        data: appointmentData,
        error: appointmentError,
      } = await supabase
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
        .eq("referral_id", currentReferral.id)
        .eq("facility_id", facilityId)
        .order("appointment_date", {
          ascending: false,
        });

      if (appointmentError) {
        console.error(
          "Could not load appointments:",
          appointmentError
        );
      }

      setReferral(currentReferral);
      setPatient(patientData as Patient);
      setAssessment(assessmentData);
      setAppointments(
        (appointmentData ?? []) as Appointment[]
      );

      // Pre-fill department from AI recommendation
      setAppointmentDepartment(
        currentReferral.recommended_department ?? ""
      );

      setLoading(false);
    }

    if (referralId) {
      loadReferralDetails();
    }
  }, [referralId]);

  // --------------------------------------------------
  // Accept referral
  // --------------------------------------------------

  async function handleAccept() {
    if (!referral) {
      return;
    }

    setActionLoading(true);
    setMessage("");

    const { error: updateError } =
      await supabase
        .from("referrals")
        .update({
          status: "accepted",
          updated_at: new Date().toISOString(),
        })
        .eq("id", referral.id);

    if (updateError) {
      setMessage(
        `Could not accept referral: ${updateError.message}`
      );
      setActionLoading(false);
      return;
    }

    const { error: eventError } =
      await supabase
        .from("referral_events")
        .insert({
          referral_id: referral.id,
          facility_id: referral.receiving_facility_id,
          event_type: "accepted",
          previous_status: referral.status,
          new_status: "accepted",
          description: "Hospital accepted the referral.",
          actor_source: "hospital_dashboard",
        });

    if (eventError) {
      console.error(
        "Could not create acceptance event:",
        eventError
      );
    }

    setReferral({
      ...referral,
      status: "accepted",
    });

    setMessage(
      appointments.length > 0
        ? "Referral accepted. An appointment already exists for this referral."
        : "Referral accepted successfully. You can now schedule an appointment."
    );

    setActionLoading(false);
  }

  // --------------------------------------------------
  // Reject referral
  // --------------------------------------------------

  async function handleReject() {
    if (!referral) {
      return;
    }

    const reason = rejectionReason.trim();

    if (!reason) {
      setMessage("Please provide a rejection reason.");
      return;
    }

    setActionLoading(true);
    setMessage("");

    const { error: updateError } =
      await supabase
        .from("referrals")
        .update({
          status: "rejected",
          rejection_reason: reason,
          updated_at: new Date().toISOString(),
        })
        .eq("id", referral.id);

    if (updateError) {
      setMessage(
        `Could not reject referral: ${updateError.message}`
      );
      setActionLoading(false);
      return;
    }

    const { error: eventError } =
      await supabase
        .from("referral_events")
        .insert({
          referral_id: referral.id,
          facility_id: referral.receiving_facility_id,
          event_type: "rejected",
          previous_status: referral.status,
          new_status: "rejected",
          description: reason,
          actor_source: "hospital_dashboard",
        });

    if (eventError) {
      console.error(
        "Could not create rejection event:",
        eventError
      );
    }

    setReferral({
      ...referral,
      status: "rejected",
      rejection_reason: reason,
    });

    setMessage(
      "Referral rejected. Voxera AI can now handle the next routing decision."
    );

    setActionLoading(false);
  }

  // --------------------------------------------------
  // Schedule appointment
  // --------------------------------------------------

  async function handleScheduleAppointment() {
    if (!referral || !patient) {
      return;
    }

    if (referral.status !== "accepted") {
      setMessage(
        "The referral must be accepted before scheduling an appointment."
      );
      return;
    }

    // --------------------------------------------------
    // Prevent duplicate appointment creation
    // --------------------------------------------------

    if (appointments.length > 0) {
      setMessage(
        "An appointment already exists for this referral. Use the Appointments page to manage it."
      );
      return;
    }

    if (!appointmentDate) {
      setMessage("Please select an appointment date.");
      return;
    }

    if (!appointmentTime) {
      setMessage("Please select an appointment time.");
      return;
    }

    setAppointmentLoading(true);
    setMessage("");

    const {
      data: { user },
      error: userError,
    } = await supabase.auth.getUser();

    if (userError || !user) {
      setMessage("You are not logged in.");
      setAppointmentLoading(false);
      return;
    }

    const {
      data: hospitalUser,
      error: hospitalUserError,
    } = await supabase
      .from("hospital_users")
      .select("facility_id")
      .eq("user_id", user.id)
      .single();

    if (hospitalUserError || !hospitalUser) {
      setMessage("Hospital information could not be found.");
      setAppointmentLoading(false);
      return;
    }

    const { data: newAppointment, error } =
      await supabase
        .from("appointments")
        .insert({
          facility_id: hospitalUser.facility_id,
          patient_id: patient.id,
          referral_id: referral.id,
          appointment_date: appointmentDate,
          appointment_time:
            appointmentTime || null,
          department:
            appointmentDepartment.trim() || null,
          doctor_name:
            doctorName.trim() || null,
          reason:
            appointmentReason.trim() ||
            referral.reason,
          status: "scheduled",
        })
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
        .single();

    if (error) {
      setMessage(
        `Could not schedule appointment: ${error.message}`
      );
      setAppointmentLoading(false);
      return;
    }

    if (newAppointment) {
      setAppointments((current) => [
        newAppointment as Appointment,
        ...current,
      ]);
    }

    setAppointmentDate("");
    setAppointmentTime("");
    setDoctorName("");
    setAppointmentReason("");

    setMessage(
      "Appointment scheduled successfully."
    );

    setAppointmentLoading(false);
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

  function formatAppointmentDate(date: string) {
    return new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }).format(new Date(`${date}T00:00:00`));
  }

  function formatDateTime(date: string) {
    return new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(date));
  }

  function statusClass(status: string) {
    const normalized = status.toLowerCase();

    if (normalized === "accepted") {
      return "border-green-500 text-green-600";
    }

    if (normalized === "rejected") {
      return "border-red-500 text-red-600";
    }

    return "border-[#D4AF37] text-[#9a7b13]";
  }

  function appointmentStatusClass(status: string) {
    const normalized = status.toLowerCase();

    if (normalized === "completed") {
      return "border-green-500 text-green-600";
    }

    if (normalized === "missed") {
      return "border-red-500 text-red-600";
    }

    if (normalized === "cancelled") {
      return "border-gray-500 text-gray-600";
    }

    return "border-[#D4AF37] text-[#9a7b13]";
  }

  // --------------------------------------------------
  // Loading
  // --------------------------------------------------

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading referral details...
        </p>
      </main>
    );
  }

  // --------------------------------------------------
  // Error
  // --------------------------------------------------

  if (!referral || !patient) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <div className="dashboard-panel rounded-2xl border-2 border-[#D4AF37] p-8">
          <h1 className="text-2xl font-bold">
            Referral Not Found
          </h1>

          <p className="dashboard-muted mt-3">
            {message ||
              "The referral could not be loaded."}
          </p>

          <Link
            href="/dashboard/referrals"
            className="mt-6 inline-flex rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
          >
            Back to Referrals
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* Back */}
        <Link
          href="/dashboard/referrals"
          className="dashboard-muted inline-flex items-center gap-2 text-sm font-semibold hover:text-[#9a7b13]"
        >
          ← Back to Referrals
        </Link>

        {/* Header */}
        <section className="dashboard-panel dashboard-hover-glow mt-5 rounded-2xl border-2 border-[#D4AF37] p-8">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                Referral Details
              </p>

              <h1 className="mt-2 text-4xl font-bold">
                {patient.full_name}
              </h1>

              <p className="dashboard-muted mt-2 text-sm">
                Referral ID: {referral.id}
              </p>
            </div>

            <div className="flex flex-wrap gap-3">
              <span
                className={`rounded-full border-2 px-4 py-2 text-sm font-bold uppercase ${statusClass(
                  referral.status
                )}`}
              >
                {referral.status}
              </span>

              <span
                className={`rounded-full border-2 px-4 py-2 text-sm font-bold uppercase ${
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
          </div>
        </section>

        {/* Message */}
        {message && (
          <div className="mt-5 rounded-xl border-2 border-[#D4AF37] bg-white px-5 py-4 text-sm font-semibold">
            {message}
          </div>
        )}

        {/* Patient + Referral */}
        <div className="mt-6 grid gap-6 lg:grid-cols-2">

          {/* Patient */}
          <section className="dashboard-panel rounded-2xl border-2 border-[#D4AF37] p-7">
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Patient
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Patient Information
            </h2>

            <div className="mt-6 grid gap-5 sm:grid-cols-2">
              <Info
                label="Name"
                value={patient.full_name}
              />

              <Info
                label="Phone"
                value={patient.phone ?? "Not available"}
              />

              <Info
                label="Gender"
                value={patient.gender ?? "Not available"}
              />

              <Info
                label="Date of Birth"
                value={formatDate(patient.date_of_birth)}
              />

              <Info
                label="Language"
                value={
                  patient.preferred_language ??
                  "Not available"
                }
              />

              <Info
                label="Locality"
                value={
                  patient.village_or_locality ??
                  "Not available"
                }
              />

              <Info
                label="District"
                value={
                  patient.district ??
                  "Not available"
                }
              />
            </div>
          </section>

          {/* Referral */}
          <section className="dashboard-panel rounded-2xl border-2 border-[#D4AF37] p-7">
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Referral
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Referral Information
            </h2>

            <div className="mt-6 space-y-5">
              <Info
                label="Reason"
                value={referral.reason}
              />

              <Info
                label="Required Service"
                value={
                  referral.required_service ??
                  "Not specified"
                }
              />

              <Info
                label="Recommended Department"
                value={
                  referral.recommended_department ??
                  "Not specified"
                }
              />

              <Info
                label="Expected Arrival"
                value={
                  referral.expected_arrival
                    ? formatDateTime(
                        referral.expected_arrival
                      )
                    : "Not specified"
                }
              />

              {referral.notes && (
                <Info
                  label="Notes"
                  value={referral.notes}
                />
              )}
            </div>
          </section>
        </div>

        {/* AI Assessment */}
        <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-7">
          <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
            AI Assessment
          </p>

          <h2 className="mt-2 text-2xl font-bold">
            Clinical Assessment
          </h2>

          {assessment ? (
            <>
              <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
                <Info
                  label="Severity"
                  value={assessment.severity}
                />

                <Info
                  label="Risk Level"
                  value={
                    assessment.risk_level ??
                    "Not specified"
                  }
                />

                <Info
                  label="Department"
                  value={
                    assessment.recommended_department ??
                    "Not specified"
                  }
                />

                <Info
                  label="Confidence"
                  value={
                    assessment.confidence_score !== null
                      ? `${(
                          assessment.confidence_score * 100
                        ).toFixed(0)}%`
                      : "Not available"
                  }
                />
              </div>

              <div className="mt-6 space-y-4">
                {assessment.symptoms_summary && (
                  <InfoBox
                    label="Symptoms / Clinical Summary"
                    value={
                      assessment.symptoms_summary
                    }
                  />
                )}

                {assessment.ai_summary && (
                  <InfoBox
                    label="AI Summary"
                    value={assessment.ai_summary}
                  />
                )}

                {assessment.ai_recommendation && (
                  <InfoBox
                    label="AI Recommendation"
                    value={
                      assessment.ai_recommendation
                    }
                  />
                )}
              </div>
            </>
          ) : (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-5">
              <p className="dashboard-muted">
                AI assessment details are not available.
              </p>
            </div>
          )}
        </section>

        {/* Hospital Decision */}
        {referral.status === "pending" && (
          <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-7">
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Hospital Decision
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Review Referral
            </h2>

            <div className="mt-6 flex flex-col gap-4 lg:flex-row">
              <button
                type="button"
                disabled={actionLoading}
                onClick={handleAccept}
                className="rounded-lg border-2 border-[#D4AF37] bg-black px-6 py-3 text-sm font-bold text-white transition-all hover:bg-[#D4AF37] hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
              >
                {actionLoading
                  ? "Processing..."
                  : "Accept Referral"}
              </button>

              <div className="flex flex-1 flex-col gap-3 sm:flex-row">
                <input
                  type="text"
                  value={rejectionReason}
                  onChange={(event) =>
                    setRejectionReason(
                      event.target.value
                    )
                  }
                  placeholder="Reason for rejection"
                  className="flex-1 rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-sm text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                />

                <button
                  type="button"
                  disabled={actionLoading}
                  onClick={handleReject}
                  className="rounded-lg border-2 border-red-500 px-6 py-3 text-sm font-bold text-red-600 transition-all hover:bg-red-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Reject Referral
                </button>
              </div>
            </div>
          </section>
        )}

        {/* --------------------------------------------------
            Schedule Appointment
            ONLY if accepted AND no appointment exists
        -------------------------------------------------- */}
        {referral.status === "accepted" &&
          appointments.length === 0 && (
            <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-7">
              <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                Appointment Management
              </p>

              <h2 className="mt-2 text-2xl font-bold">
                📅 Schedule Appointment
              </h2>

              <p className="dashboard-muted mt-2 text-sm">
                Schedule the patient's appointment after accepting the referral.
              </p>

              <div className="mt-6 grid gap-5 md:grid-cols-2">

                {/* Date */}
                <div>
                  <label className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                    Appointment Date
                  </label>

                  <input
                    type="date"
                    value={appointmentDate}
                    onChange={(event) =>
                      setAppointmentDate(
                        event.target.value
                      )
                    }
                    className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-sm text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                  />
                </div>

                {/* Time */}
                <div>
                  <label className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                    Appointment Time
                  </label>

                  <input
                    type="time"
                    value={appointmentTime}
                    onChange={(event) =>
                      setAppointmentTime(
                        event.target.value
                      )
                    }
                    className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-sm text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                  />
                </div>

                {/* Department */}
                <div>
                  <label className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                    Department
                  </label>

                  <input
                    type="text"
                    value={appointmentDepartment}
                    onChange={(event) =>
                      setAppointmentDepartment(
                        event.target.value
                      )
                    }
                    placeholder="e.g. Cardiology"
                    className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-sm text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                  />
                </div>

                {/* Doctor */}
                <div>
                  <label className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                    Doctor
                  </label>

                  <input
                    type="text"
                    value={doctorName}
                    onChange={(event) =>
                      setDoctorName(
                        event.target.value
                      )
                    }
                    placeholder="Doctor name"
                    className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-sm text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                  />
                </div>

                {/* Reason */}
                <div className="md:col-span-2">
                  <label className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                    Appointment Reason
                  </label>

                  <textarea
                    value={appointmentReason}
                    onChange={(event) =>
                      setAppointmentReason(
                        event.target.value
                      )
                    }
                    placeholder="Reason for appointment"
                    rows={3}
                    className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-sm text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                  />
                </div>
              </div>

              <button
                type="button"
                disabled={appointmentLoading}
                onClick={handleScheduleAppointment}
                className="mt-6 rounded-lg border-2 border-[#D4AF37] bg-black px-6 py-3 text-sm font-bold text-white transition-all hover:bg-[#D4AF37] hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
              >
                {appointmentLoading
                  ? "Scheduling..."
                  : "Schedule Appointment"}
              </button>
            </section>
          )}

        {/* --------------------------------------------------
            Existing Appointments
        -------------------------------------------------- */}
        {appointments.length > 0 && (
          <section className="dashboard-panel mt-6 rounded-2xl border-2 border-[#D4AF37] p-7">
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Appointment Management
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Patient Appointments
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              An appointment has already been created for this referral.
              Manage its status from the Appointments page.
            </p>

            <div className="mt-6 space-y-4">
              {appointments.map((appointment) => (
                <div
                  key={appointment.id}
                  className="dashboard-hover-glow rounded-xl border border-gray-300 p-5"
                >
                  <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">

                    <div>
                      <h3 className="font-bold">
                        {formatAppointmentDate(
                          appointment.appointment_date
                        )}

                        {appointment.appointment_time &&
                          ` at ${appointment.appointment_time}`}
                      </h3>

                      <div className="dashboard-muted mt-2 space-y-1 text-sm">

                        {appointment.department && (
                          <p>
                            Department:{" "}
                            {appointment.department}
                          </p>
                        )}

                        {appointment.doctor_name && (
                          <p>
                            Doctor:{" "}
                            {appointment.doctor_name}
                          </p>
                        )}

                        {appointment.reason && (
                          <p>
                            Reason:{" "}
                            {appointment.reason}
                          </p>
                        )}
                      </div>
                    </div>

                    <span
                      className={`rounded-full border-2 px-4 py-2 text-xs font-bold uppercase ${appointmentStatusClass(
                        appointment.status
                      )}`}
                    >
                      {appointment.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <Link
              href="/dashboard/appointments"
              className="mt-6 inline-flex rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
            >
              Manage Appointments
            </Link>
          </section>
        )}

        {/* Rejection */}
        {referral.status === "rejected" &&
          referral.rejection_reason && (
            <section className="dashboard-panel mt-6 rounded-2xl border-2 border-red-500 p-7">
              <p className="text-sm font-semibold uppercase tracking-widest text-red-600">
                Rejection
              </p>

              <h2 className="mt-2 text-2xl font-bold">
                Rejection Reason
              </h2>

              <p className="mt-4 leading-7">
                {referral.rejection_reason}
              </p>

              <p className="dashboard-muted mt-4 text-sm">
                Voxera AI is responsible for deciding
                the next hospital after rejection.
              </p>
            </section>
          )}
      </div>
    </main>
  );
}

// --------------------------------------------------
// Reusable information component
// --------------------------------------------------

function Info({
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

// --------------------------------------------------
// Reusable information box
// --------------------------------------------------

function InfoBox({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-gray-300 p-5">
      <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
        {label}
      </p>

      <p className="mt-2 text-sm leading-6">
        {value}
      </p>
    </div>
  );
}