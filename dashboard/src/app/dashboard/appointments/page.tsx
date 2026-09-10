"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

type Appointment = {
  id: string;
  facility_id: string;
  patient_id: string;
  referral_id: string | null;
  appointment_date: string;
  appointment_time: string | null;
  department: string | null;
  doctor_name: string | null;
  reason: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

type Patient = {
  id: string;
  full_name: string;
  phone: string | null;
};

type AppointmentWithPatient = Appointment & {
  patient: Patient | null;
};

type RescheduleForm = {
  date: string;
  time: string;
};

export default function AppointmentsPage() {
  const [appointments, setAppointments] = useState<
    AppointmentWithPatient[]
  >([]);

  const [loading, setLoading] = useState(true);

  const [updatingId, setUpdatingId] = useState<string | null>(
    null
  );

  const [reschedulingId, setReschedulingId] = useState<
    string | null
  >(null);

  const [rescheduleForm, setRescheduleForm] =
    useState<RescheduleForm>({
      date: "",
      time: "",
    });

  const [message, setMessage] = useState("");

  useEffect(() => {
    loadAppointments();
  }, []);

  // --------------------------------------------------
  // Load FIRST / TREATMENT appointments only
  // --------------------------------------------------

  async function loadAppointments() {
    setLoading(true);
    setMessage("");

    try {
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
      // 3. Get all follow-up appointment IDs
      //
      // Any appointment referenced by follow_ups is a
      // FOLLOW-UP appointment and must NOT appear on
      // this page.
      // --------------------------------------------------

      const {
        data: followUpData,
        error: followUpError,
      } = await supabase
        .from("follow_ups")
        .select("appointment_id")
        .eq("facility_id", facilityId);

      if (followUpError) {
        setMessage(
          `Follow-up information could not be loaded: ${followUpError.message}`
        );
        setLoading(false);
        return;
      }

      const followUpAppointmentIds = new Set(
        (followUpData ?? [])
          .map((item) => item.appointment_id)
          .filter(
            (id): id is string => Boolean(id)
          )
      );

      // --------------------------------------------------
      // 4. Load appointments for this hospital
      // --------------------------------------------------

      const {
        data: appointmentData,
        error: appointmentError,
      } = await supabase
        .from("appointments")
        .select(
          `
            id,
            facility_id,
            patient_id,
            referral_id,
            appointment_date,
            appointment_time,
            department,
            doctor_name,
            reason,
            status,
            created_at,
            updated_at
          `
        )
        .eq("facility_id", facilityId)
        .order("appointment_date", {
          ascending: true,
        })
        .order("appointment_time", {
          ascending: true,
        });

      if (appointmentError) {
        setMessage(
          `Appointments could not be loaded: ${appointmentError.message}`
        );
        setLoading(false);
        return;
      }

      const rawAppointments =
        (appointmentData ?? []) as Appointment[];

      // --------------------------------------------------
      // 5. IMPORTANT:
      // Remove every appointment that belongs to the
      // follow-up workflow.
      // --------------------------------------------------

      const treatmentAppointments =
        rawAppointments.filter(
          (appointment) =>
            !followUpAppointmentIds.has(appointment.id)
        );

      // --------------------------------------------------
      // 6. Load patients
      // --------------------------------------------------

      const patientIds = [
        ...new Set(
          treatmentAppointments.map(
            (appointment) => appointment.patient_id
          )
        ),
      ];

      let patients: Patient[] = [];

      if (patientIds.length > 0) {
        const {
          data: patientData,
          error: patientError,
        } = await supabase
          .from("patients")
          .select("id, full_name, phone")
          .in("id", patientIds);

        if (patientError) {
          setMessage(
            `Patient information could not be loaded: ${patientError.message}`
          );
          setLoading(false);
          return;
        }

        patients = (patientData ?? []) as Patient[];
      }

      // --------------------------------------------------
      // 7. Combine appointments with patients
      // --------------------------------------------------

      const combinedAppointments: AppointmentWithPatient[] =
        treatmentAppointments.map((appointment) => ({
          ...appointment,
          patient:
            patients.find(
              (patient) =>
                patient.id === appointment.patient_id
            ) ?? null,
        }));

      setAppointments(combinedAppointments);
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while loading appointments."
      );
    } finally {
      setLoading(false);
    }
  }

  // --------------------------------------------------
  // Check whether cancelled treatment appointment has
  // already been replaced
  // --------------------------------------------------

  function hasReplacementAppointment(
    appointment: AppointmentWithPatient
  ) {
    if (!appointment.referral_id) {
      return false;
    }

    return appointments.some(
      (otherAppointment) =>
        otherAppointment.id !== appointment.id &&
        otherAppointment.referral_id ===
          appointment.referral_id &&
        new Date(otherAppointment.created_at).getTime() >
          new Date(appointment.created_at).getTime()
    );
  }

  // --------------------------------------------------
  // Update appointment status
  // --------------------------------------------------

  async function updateAppointmentStatus(
    appointmentId: string,
    status: string
  ) {
    const appointment = appointments.find(
      (item) => item.id === appointmentId
    );

    if (!appointment) {
      return;
    }

    // Locked cancelled appointments cannot be edited.
    if (
      appointment.status.toLowerCase() === "cancelled" &&
      hasReplacementAppointment(appointment)
    ) {
      setMessage(
        "This cancelled appointment is locked because it has already been rescheduled."
      );
      return;
    }

    setUpdatingId(appointmentId);
    setMessage("");

    const updatedAt = new Date().toISOString();

    const { error } = await supabase
      .from("appointments")
      .update({
        status,
        updated_at: updatedAt,
      })
      .eq("id", appointmentId);

    if (error) {
      setMessage(
        `Could not update appointment: ${error.message}`
      );

      setUpdatingId(null);
      return;
    }

    setAppointments((current) =>
      current.map((item) =>
        item.id === appointmentId
          ? {
              ...item,
              status,
              updated_at: updatedAt,
            }
          : item
      )
    );

    if (status !== "cancelled") {
      setReschedulingId(null);

      setRescheduleForm({
        date: "",
        time: "",
      });
    }

    setMessage(
      `Appointment status updated to ${status}.`
    );

    setUpdatingId(null);
  }

  // --------------------------------------------------
  // Open reschedule form
  // --------------------------------------------------

  function openRescheduleForm(
    appointment: AppointmentWithPatient
  ) {
    if (hasReplacementAppointment(appointment)) {
      setMessage(
        "This cancelled appointment has already been rescheduled and is now locked."
      );
      return;
    }

    setMessage("");

    setReschedulingId(appointment.id);

    setRescheduleForm({
      date: appointment.appointment_date,
      time: appointment.appointment_time
        ? appointment.appointment_time.slice(0, 5)
        : "",
    });
  }

  // --------------------------------------------------
  // Close reschedule form
  // --------------------------------------------------

  function closeRescheduleForm() {
    setReschedulingId(null);

    setRescheduleForm({
      date: "",
      time: "",
    });
  }

  // --------------------------------------------------
  // Reschedule FIRST / TREATMENT appointment
  // --------------------------------------------------

  async function rescheduleAppointment(
    appointment: AppointmentWithPatient
  ) {
    if (hasReplacementAppointment(appointment)) {
      setMessage(
        "This cancelled appointment has already been rescheduled."
      );

      closeRescheduleForm();
      return;
    }

    if (!rescheduleForm.date) {
      setMessage("Please select a new appointment date.");
      return;
    }

    if (!rescheduleForm.time) {
      setMessage("Please select a new appointment time.");
      return;
    }

    setUpdatingId(appointment.id);
    setMessage("");

    try {
      // --------------------------------------------------
      // Create a NEW treatment appointment.
      //
      // The old appointment remains cancelled and becomes
      // locked after this replacement is created.
      // --------------------------------------------------

      const {
        data: newAppointment,
        error,
      } = await supabase
        .from("appointments")
        .insert({
          facility_id: appointment.facility_id,
          patient_id: appointment.patient_id,
          referral_id: appointment.referral_id,
          appointment_date: rescheduleForm.date,
          appointment_time: rescheduleForm.time,
          department: appointment.department,
          doctor_name: appointment.doctor_name,
          reason: appointment.reason,
          status: "scheduled",
        })
        .select(
          `
            id,
            facility_id,
            patient_id,
            referral_id,
            appointment_date,
            appointment_time,
            department,
            doctor_name,
            reason,
            status,
            created_at,
            updated_at
          `
        )
        .single();

      if (error) {
        setMessage(
          `Could not reschedule appointment: ${error.message}`
        );

        setUpdatingId(null);
        return;
      }

      if (!newAppointment) {
        setMessage(
          "The new appointment could not be created."
        );

        setUpdatingId(null);
        return;
      }

      const newAppointmentWithPatient: AppointmentWithPatient =
        {
          ...(newAppointment as Appointment),
          patient: appointment.patient,
        };

      setAppointments((current) => [
        ...current,
        newAppointmentWithPatient,
      ]);

      closeRescheduleForm();

      setMessage(
        "Appointment rescheduled successfully. The previous appointment is now locked."
      );
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while rescheduling the appointment."
      );
    } finally {
      setUpdatingId(null);
    }
  }

  // --------------------------------------------------
  // Helpers
  // --------------------------------------------------

  function formatDate(date: string) {
    return new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }).format(new Date(`${date}T00:00:00`));
  }

  function formatTime(time: string | null) {
    if (!time) {
      return "Time not specified";
    }

    const [hours, minutes] = time.split(":");

    const date = new Date();

    date.setHours(
      Number(hours),
      Number(minutes),
      0,
      0
    );

    return new Intl.DateTimeFormat("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function statusClass(status: string) {
    switch (status.toLowerCase()) {
      case "scheduled":
        return "border-[#D4AF37] text-[#9a7b13]";

      case "completed":
        return "border-green-500 text-green-600";

      case "missed":
        return "border-red-500 text-red-600";

      case "cancelled":
        return "border-gray-500 text-gray-600";

      default:
        return "border-gray-400 text-gray-600";
    }
  }

  // --------------------------------------------------
  // Statistics
  // --------------------------------------------------

  const scheduledCount = appointments.filter(
    (appointment) =>
      appointment.status.toLowerCase() === "scheduled"
  ).length;

  const completedCount = appointments.filter(
    (appointment) =>
      appointment.status.toLowerCase() === "completed"
  ).length;

  const missedCount = appointments.filter(
    (appointment) =>
      appointment.status.toLowerCase() === "missed"
  ).length;

  const cancelledCount = appointments.filter(
    (appointment) =>
      appointment.status.toLowerCase() === "cancelled"
  ).length;

  // --------------------------------------------------
  // Loading
  // --------------------------------------------------

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading appointments...
        </p>
      </main>
    );
  }

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* Header */}
        <section className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">
          <div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                Hospital Operations
              </p>

              <h1 className="mt-2 text-4xl font-bold">
                📅 Appointments
              </h1>

              <p className="dashboard-muted mt-2 text-sm">
                Manage first-time treatment and consultation
                appointments.
              </p>
            </div>

            <button
              type="button"
              onClick={loadAppointments}
              className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
            >
              Refresh
            </button>
          </div>
        </section>

        {/* Message */}
        {message && (
          <div className="mt-5 rounded-xl border-2 border-[#D4AF37] bg-white px-5 py-4 text-sm font-semibold">
            {message}
          </div>
        )}

        {/* Statistics */}
        <section className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">

          {/* Scheduled */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Scheduled
            </p>

            <p className="mt-3 text-4xl font-bold">
              {scheduledCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              First treatment appointments
            </p>
          </div>

          {/* Completed */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Completed
            </p>

            <p className="mt-3 text-4xl font-bold">
              {completedCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Completed treatments
            </p>
          </div>

          {/* Missed */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Missed
            </p>

            <p className="mt-3 text-4xl font-bold">
              {missedCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              First appointments not attended
            </p>
          </div>

          {/* Cancelled */}
          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Cancelled
            </p>

            <p className="mt-3 text-4xl font-bold">
              {cancelledCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              First appointments cancelled
            </p>
          </div>
        </section>

        {/* Appointment List */}
        <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">

          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              First Treatment / Consultation
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Treatment Appointments
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              Follow-up appointments are managed exclusively
              from the Follow-ups page.
            </p>
          </div>

          {appointments.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-8 text-center">
              <div className="text-5xl">
                📅
              </div>

              <h3 className="mt-4 text-xl font-bold">
                No treatment appointments
              </h3>

              <p className="dashboard-muted mt-2 text-sm">
                First treatment or consultation appointments
                will appear here after a referral is accepted
                and an appointment is scheduled.
              </p>
            </div>
          ) : (
            <div className="mt-6 space-y-5">
              {appointments.map((appointment) => {
                const isCancelled =
                  appointment.status.toLowerCase() ===
                  "cancelled";

                const isLockedCancelled =
                  isCancelled &&
                  hasReplacementAppointment(
                    appointment
                  );

                return (
                  <div
                    key={appointment.id}
                    className="dashboard-hover-glow rounded-2xl border border-gray-300 p-6"
                  >
                    <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">

                      {/* Appointment information */}
                      <div className="flex-1">

                        <div className="flex flex-wrap items-center gap-3">
                          <h3 className="text-xl font-bold">
                            {appointment.patient
                              ?.full_name ??
                              "Unknown Patient"}
                          </h3>

                          <span
                            className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${statusClass(
                              appointment.status
                            )}`}
                          >
                            {appointment.status}
                          </span>

                          {isLockedCancelled && (
                            <span className="rounded-full border-2 border-gray-400 px-3 py-1 text-xs font-bold uppercase text-gray-500">
                              Locked
                            </span>
                          )}
                        </div>

                        <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">

                          {/* Date */}
                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Date
                            </p>

                            <p className="mt-1 font-medium">
                              {formatDate(
                                appointment.appointment_date
                              )}
                            </p>
                          </div>

                          {/* Time */}
                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Time
                            </p>

                            <p className="mt-1 font-medium">
                              {formatTime(
                                appointment.appointment_time
                              )}
                            </p>
                          </div>

                          {/* Department */}
                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Department
                            </p>

                            <p className="mt-1 font-medium">
                              {appointment.department ??
                                "Not specified"}
                            </p>
                          </div>

                          {/* Doctor */}
                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Doctor
                            </p>

                            <p className="mt-1 font-medium">
                              {appointment.doctor_name ??
                                "Not specified"}
                            </p>
                          </div>

                          {/* Patient Phone */}
                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Patient Phone
                            </p>

                            <p className="mt-1 font-medium">
                              {appointment.patient?.phone ??
                                "Not available"}
                            </p>
                          </div>

                          {/* Reason */}
                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Reason
                            </p>

                            <p className="mt-1 font-medium">
                              {appointment.reason ??
                                "Not specified"}
                            </p>
                          </div>
                        </div>

                        {/* Referral */}
                        {appointment.referral_id && (
                          <Link
                            href={`/dashboard/referrals/${appointment.referral_id}`}
                            className="mt-5 inline-flex rounded-lg border border-[#D4AF37] px-4 py-2 text-sm font-semibold transition-all hover:bg-[#D4AF37] hover:text-black"
                          >
                            View Referral
                          </Link>
                        )}

                        {/* ------------------------------------------------
                            Cancelled appointment that has NOT yet been
                            rescheduled
                           ------------------------------------------------ */}
                        {isCancelled &&
                          !isLockedCancelled && (
                            <div className="mt-6 rounded-xl border-2 border-[#D4AF37] bg-[#faf9f1] p-5">

                              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">

                                <div>
                                  <h4 className="font-bold">
                                    Appointment Cancelled
                                  </h4>

                                  <p className="dashboard-muted mt-1 text-sm">
                                    Schedule a new first treatment
                                    or consultation appointment.
                                  </p>
                                </div>

                                <button
                                  type="button"
                                  onClick={() =>
                                    reschedulingId ===
                                    appointment.id
                                      ? closeRescheduleForm()
                                      : openRescheduleForm(
                                          appointment
                                        )
                                  }
                                  disabled={
                                    updatingId ===
                                    appointment.id
                                  }
                                  className="rounded-lg border-2 border-[#D4AF37] px-4 py-2 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  {reschedulingId ===
                                  appointment.id
                                    ? "Close"
                                    : "Reschedule Appointment"}
                                </button>
                              </div>

                              {/* Reschedule form */}
                              {reschedulingId ===
                                appointment.id && (
                                <div className="mt-5 border-t border-[#D4AF37] pt-5">

                                  <div className="grid gap-5 md:grid-cols-2">

                                    {/* Date */}
                                    <div>
                                      <label
                                        htmlFor={`date-${appointment.id}`}
                                        className="text-sm font-semibold"
                                      >
                                        New Date
                                      </label>

                                      <input
                                        id={`date-${appointment.id}`}
                                        type="date"
                                        value={
                                          rescheduleForm.date
                                        }
                                        min={
                                          new Date()
                                            .toISOString()
                                            .split("T")[0]
                                        }
                                        onChange={(event) =>
                                          setRescheduleForm(
                                            (current) => ({
                                              ...current,
                                              date: event.target
                                                .value,
                                            })
                                          )
                                        }
                                        className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                                      />
                                    </div>

                                    {/* Time */}
                                    <div>
                                      <label
                                        htmlFor={`time-${appointment.id}`}
                                        className="text-sm font-semibold"
                                      >
                                        New Time
                                      </label>

                                      <input
                                        id={`time-${appointment.id}`}
                                        type="time"
                                        value={
                                          rescheduleForm.time
                                        }
                                        onChange={(event) =>
                                          setRescheduleForm(
                                            (current) => ({
                                              ...current,
                                              time: event.target
                                                .value,
                                            })
                                          )
                                        }
                                        className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                                      />
                                    </div>
                                  </div>

                                  <div className="mt-5 flex flex-col gap-3 sm:flex-row">

                                    <button
                                      type="button"
                                      onClick={() =>
                                        rescheduleAppointment(
                                          appointment
                                        )
                                      }
                                      disabled={
                                        updatingId ===
                                        appointment.id
                                      }
                                      className="rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-3 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)] disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                      {updatingId ===
                                      appointment.id
                                        ? "Rescheduling..."
                                        : "Confirm Reschedule"}
                                    </button>

                                    <button
                                      type="button"
                                      onClick={
                                        closeRescheduleForm
                                      }
                                      disabled={
                                        updatingId ===
                                        appointment.id
                                      }
                                      className="rounded-lg border-2 border-gray-400 px-5 py-3 text-sm font-bold transition-all hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                      Cancel
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}

                        {/* ------------------------------------------------
                            Locked cancelled appointment
                           ------------------------------------------------ */}
                        {isLockedCancelled && (
                          <div className="mt-6 rounded-xl border border-gray-300 bg-gray-50 p-4">
                            <p className="text-sm font-semibold text-gray-600">
                              🔒 This cancelled appointment is
                              locked.
                            </p>

                            <p className="dashboard-muted mt-1 text-xs">
                              A replacement treatment appointment
                              has already been scheduled. This
                              record is kept only as appointment
                              history.
                            </p>
                          </div>
                        )}
                      </div>

                      {/* ------------------------------------------------
                          Status controls
                         ------------------------------------------------ */}
                      <div className="w-full lg:w-56">

                        {isLockedCancelled ? (
                          <>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Status
                            </p>

                            <div className="mt-2 rounded-lg border-2 border-gray-300 bg-gray-100 px-3 py-3 text-sm font-bold text-gray-500">
                              Cancelled — Locked
                            </div>

                            <p className="dashboard-muted mt-3 text-xs leading-5">
                              This historical appointment cannot
                              be edited.
                            </p>
                          </>
                        ) : (
                          <>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Update Status
                            </p>

                            <select
                              value={appointment.status}
                              disabled={
                                updatingId ===
                                appointment.id
                              }
                              onChange={(event) =>
                                updateAppointmentStatus(
                                  appointment.id,
                                  event.target.value
                                )
                              }
                              className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-semibold text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)] disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <option value="scheduled">
                                Scheduled
                              </option>

                              <option value="completed">
                                Completed
                              </option>

                              <option value="missed">
                                Missed
                              </option>

                              <option value="cancelled">
                                Cancelled
                              </option>
                            </select>

                            {updatingId ===
                              appointment.id && (
                              <p className="dashboard-muted mt-2 text-xs">
                                Updating...
                              </p>
                            )}

                            {/* Missed */}
                            {appointment.status.toLowerCase() ===
                              "missed" && (
                              <p className="dashboard-muted mt-3 text-xs leading-5">
                                This first appointment was
                                missed. Voxera AI handles the
                                patient contact and next steps.
                              </p>
                            )}

                            {/* Completed */}
                            {appointment.status.toLowerCase() ===
                              "completed" && (
                              <p className="dashboard-muted mt-3 text-xs leading-5">
                                Treatment/consultation is
                                completed. Follow-up care is
                                managed exclusively from the
                                Follow-ups page.
                              </p>
                            )}

                            {/* Cancelled */}
                            {isCancelled && (
                              <p className="dashboard-muted mt-3 text-xs leading-5">
                                This first appointment can be
                                rescheduled below.
                              </p>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}