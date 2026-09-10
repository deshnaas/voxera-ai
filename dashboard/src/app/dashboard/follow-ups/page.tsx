"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

type FollowUp = {
  id: string;
  patient_id: string;
  facility_id: string;
  appointment_id: string | null;
  referral_id: string | null;
  follow_up_date: string;
  reason: string | null;
  notes: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

type Patient = {
  id: string;
  full_name: string;
  phone: string | null;
};

type TreatmentAppointment = {
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
};

type FollowUpWithPatient = FollowUp & {
  patient: Patient | null;
};

type CompletedTreatment = TreatmentAppointment & {
  patient: Patient | null;
};

type FollowUpForm = {
  date: string;
  time: string;
  reason: string;
  notes: string;
};

type RescheduleForm = {
  date: string;
  time: string;
};

export default function FollowUpsPage() {
  const [followUps, setFollowUps] = useState<
    FollowUpWithPatient[]
  >([]);

  const [completedTreatments, setCompletedTreatments] =
    useState<CompletedTreatment[]>([]);

  const [loading, setLoading] = useState(true);

  const [message, setMessage] = useState("");

  const [updatingId, setUpdatingId] = useState<string | null>(
    null
  );

  const [creatingForId, setCreatingForId] = useState<
    string | null
  >(null);

  const [reschedulingId, setReschedulingId] = useState<
    string | null
  >(null);

  const [followUpForm, setFollowUpForm] =
    useState<FollowUpForm>({
      date: "",
      time: "",
      reason: "",
      notes: "",
    });

  const [rescheduleForm, setRescheduleForm] =
    useState<RescheduleForm>({
      date: "",
      time: "",
    });

  useEffect(() => {
    loadFollowUps();
  }, []);

  // --------------------------------------------------
  // Load follow-ups page data
  // --------------------------------------------------

  async function loadFollowUps() {
    setLoading(true);
    setMessage("");

    try {
      // --------------------------------------------------
      // 1. Logged-in user
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
      // 2. Hospital / facility
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
      // 3. Load FOLLOW-UP records
      // --------------------------------------------------

      const {
        data: followUpData,
        error: followUpError,
      } = await supabase
        .from("follow_ups")
        .select(
          `
            id,
            patient_id,
            facility_id,
            appointment_id,
            referral_id,
            follow_up_date,
            reason,
            notes,
            status,
            created_at,
            updated_at
          `
        )
        .eq("facility_id", facilityId)
        .order("follow_up_date", {
          ascending: true,
        });

      if (followUpError) {
        setMessage(
          `Follow-ups could not be loaded: ${followUpError.message}`
        );
        setLoading(false);
        return;
      }

      const rawFollowUps =
        (followUpData ?? []) as FollowUp[];

      // --------------------------------------------------
      // 4. Load patients for follow-ups
      // --------------------------------------------------

      const followUpPatientIds = [
        ...new Set(
          rawFollowUps.map(
            (followUp) => followUp.patient_id
          )
        ),
      ];

      let followUpPatients: Patient[] = [];

      if (followUpPatientIds.length > 0) {
        const {
          data: patientData,
          error: patientError,
        } = await supabase
          .from("patients")
          .select("id, full_name, phone")
          .in("id", followUpPatientIds);

        if (patientError) {
          setMessage(
            `Patient information could not be loaded: ${patientError.message}`
          );
          setLoading(false);
          return;
        }

        followUpPatients =
          (patientData ?? []) as Patient[];
      }

      const combinedFollowUps: FollowUpWithPatient[] =
        rawFollowUps.map((followUp) => ({
          ...followUp,
          patient:
            followUpPatients.find(
              (patient) =>
                patient.id === followUp.patient_id
            ) ?? null,
        }));

      setFollowUps(combinedFollowUps);

      // --------------------------------------------------
      // 5. Load COMPLETED FIRST/TREATMENT appointments
      //
      // These are the only appointments eligible to
      // create a follow-up.
      // --------------------------------------------------

      const {
        data: treatmentData,
        error: treatmentError,
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
            status
          `
        )
        .eq("facility_id", facilityId)
        .eq("status", "completed")
        .order("appointment_date", {
          ascending: false,
        });

      if (treatmentError) {
        setMessage(
          `Completed treatment appointments could not be loaded: ${treatmentError.message}`
        );
        setLoading(false);
        return;
      }

      const allCompletedAppointments =
        (treatmentData ?? []) as TreatmentAppointment[];

      // --------------------------------------------------
      // 6. Exclude appointments that are themselves
      // follow-up appointments.
      //
      // This prevents a follow-up appointment from being
      // treated as a new completed treatment.
      // --------------------------------------------------

      const followUpAppointmentIds = new Set(
        rawFollowUps
          .map((followUp) => followUp.appointment_id)
          .filter(
            (id): id is string => Boolean(id)
          )
      );

      const completedTreatmentAppointments =
        allCompletedAppointments.filter(
          (appointment) =>
            !followUpAppointmentIds.has(appointment.id)
        );

      // --------------------------------------------------
      // 7. Load patients for completed treatments
      // --------------------------------------------------

      const completedPatientIds = [
        ...new Set(
          completedTreatmentAppointments.map(
            (appointment) => appointment.patient_id
          )
        ),
      ];

      let completedPatients: Patient[] = [];

      if (completedPatientIds.length > 0) {
        const {
          data: patientData,
          error: patientError,
        } = await supabase
          .from("patients")
          .select("id, full_name, phone")
          .in("id", completedPatientIds);

        if (patientError) {
          setMessage(
            `Completed treatment patients could not be loaded: ${patientError.message}`
          );
          setLoading(false);
          return;
        }

        completedPatients =
          (patientData ?? []) as Patient[];
      }

      // --------------------------------------------------
      // 8. Only show completed treatment that does NOT
      // already have an upcoming follow-up.
      //
      // A cancelled or missed follow-up does not block
      // creation of a new follow-up.
      // --------------------------------------------------

      const eligibleCompletedTreatments: CompletedTreatment[] =
        completedTreatmentAppointments
          .map((appointment) => ({
            ...appointment,
            patient:
              completedPatients.find(
                (patient) =>
                  patient.id === appointment.patient_id
              ) ?? null,
          }))
          .filter((appointment) => {
            const hasUpcomingFollowUp =
              rawFollowUps.some(
                (followUp) =>
                  followUp.patient_id ===
                    appointment.patient_id &&
                  followUp.status.toLowerCase() ===
                    "upcoming"
              );

            return !hasUpcomingFollowUp;
          });

      setCompletedTreatments(
        eligibleCompletedTreatments
      );
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while loading follow-ups."
      );
    } finally {
      setLoading(false);
    }
  }

  // --------------------------------------------------
  // Create Follow-up
  // --------------------------------------------------

  async function createFollowUp(
    treatmentAppointment: CompletedTreatment
  ) {
    if (!followUpForm.date) {
      setMessage("Please select a follow-up date.");
      return;
    }

    if (!followUpForm.time) {
      setMessage("Please select a follow-up time.");
      return;
    }

    if (!followUpForm.reason.trim()) {
      setMessage("Please enter the follow-up reason.");
      return;
    }

    setUpdatingId(treatmentAppointment.id);
    setMessage("");

    try {
      // --------------------------------------------------
      // Create follow-up appointment
      // --------------------------------------------------

      const {
        data: newAppointment,
        error: appointmentError,
      } = await supabase
        .from("appointments")
        .insert({
          facility_id: treatmentAppointment.facility_id,
          patient_id: treatmentAppointment.patient_id,
          referral_id: treatmentAppointment.referral_id,
          appointment_date: followUpForm.date,
          appointment_time: followUpForm.time,
          department: treatmentAppointment.department,
          doctor_name: treatmentAppointment.doctor_name,
          reason: followUpForm.reason.trim(),
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
            status
          `
        )
        .single();

      if (appointmentError) {
        setMessage(
          `Follow-up appointment could not be created: ${appointmentError.message}`
        );
        setUpdatingId(null);
        return;
      }

      if (!newAppointment) {
        setMessage(
          "Follow-up appointment could not be created."
        );
        setUpdatingId(null);
        return;
      }

      // --------------------------------------------------
      // Create follow-up record
      // --------------------------------------------------

      const { error: followUpError } =
        await supabase.from("follow_ups").insert({
          patient_id: treatmentAppointment.patient_id,
          facility_id: treatmentAppointment.facility_id,
          appointment_id: newAppointment.id,
          referral_id: treatmentAppointment.referral_id,
          follow_up_date: followUpForm.date,
          reason: followUpForm.reason.trim(),
          notes: followUpForm.notes.trim() || null,
          status: "upcoming",
        });

      if (followUpError) {
        setMessage(
          `Follow-up record could not be created: ${followUpError.message}`
        );
        setUpdatingId(null);
        return;
      }

      closeCreateFollowUp();

      setMessage(
        "Follow-up appointment scheduled successfully."
      );

      await loadFollowUps();
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while creating the follow-up."
      );
    } finally {
      setUpdatingId(null);
    }
  }

  // --------------------------------------------------
  // Update follow-up status
  // --------------------------------------------------

  async function updateFollowUpStatus(
    followUpId: string,
    status: string
  ) {
    const followUp = followUps.find(
      (item) => item.id === followUpId
    );

    if (!followUp) {
      return;
    }

    // Missed follow-ups are handled by AI.
    if (followUp.status.toLowerCase() === "missed") {
      setMessage(
        "Missed follow-ups are handled by Voxera AI."
      );
      return;
    }

    // A cancelled follow-up that has already been
    // rescheduled is historical and cannot be edited.
    if (
      followUp.status.toLowerCase() === "cancelled" &&
      hasReplacementFollowUp(followUp)
    ) {
      setMessage(
        "This cancelled follow-up is locked because it has already been rescheduled."
      );
      return;
    }

    setUpdatingId(followUpId);
    setMessage("");

    const updatedAt = new Date().toISOString();

    const { error } = await supabase
      .from("follow_ups")
      .update({
        status,
        updated_at: updatedAt,
      })
      .eq("id", followUpId);

    if (error) {
      setMessage(
        `Could not update follow-up: ${error.message}`
      );
      setUpdatingId(null);
      return;
    }

    // If the follow-up is completed, also mark its
    // follow-up appointment as completed.
    if (
      status.toLowerCase() === "completed" &&
      followUp.appointment_id
    ) {
      const { error: appointmentError } =
        await supabase
          .from("appointments")
          .update({
            status: "completed",
            updated_at: updatedAt,
          })
          .eq("id", followUp.appointment_id);

      if (appointmentError) {
        console.error(
          "Could not update follow-up appointment:",
          appointmentError
        );
      }
    }

    // If cancelled, mark the linked follow-up
    // appointment cancelled as well.
    if (
      status.toLowerCase() === "cancelled" &&
      followUp.appointment_id
    ) {
      const { error: appointmentError } =
        await supabase
          .from("appointments")
          .update({
            status: "cancelled",
            updated_at: updatedAt,
          })
          .eq("id", followUp.appointment_id);

      if (appointmentError) {
        console.error(
          "Could not update follow-up appointment:",
          appointmentError
        );
      }
    }

    // If missed, mark the linked appointment missed.
    if (
      status.toLowerCase() === "missed" &&
      followUp.appointment_id
    ) {
      const { error: appointmentError } =
        await supabase
          .from("appointments")
          .update({
            status: "missed",
            updated_at: updatedAt,
          })
          .eq("id", followUp.appointment_id);

      if (appointmentError) {
        console.error(
          "Could not update follow-up appointment:",
          appointmentError
        );
      }
    }

    setFollowUps((current) =>
      current.map((item) =>
        item.id === followUpId
          ? {
              ...item,
              status,
              updated_at: updatedAt,
            }
          : item
      )
    );

    setMessage(
      `Follow-up status updated to ${status}.`
    );

    setUpdatingId(null);
  }

  // --------------------------------------------------
  // Check whether cancelled follow-up already has
  // a replacement follow-up
  // --------------------------------------------------

  function hasReplacementFollowUp(
    followUp: FollowUpWithPatient
  ) {
    return followUps.some(
      (otherFollowUp) =>
        otherFollowUp.id !== followUp.id &&
        otherFollowUp.patient_id ===
          followUp.patient_id &&
        otherFollowUp.referral_id ===
          followUp.referral_id &&
        new Date(otherFollowUp.created_at).getTime() >
          new Date(followUp.created_at).getTime()
    );
  }

  // --------------------------------------------------
  // Open create form
  // --------------------------------------------------

  function openCreateFollowUp(
    appointment: CompletedTreatment
  ) {
    setCreatingForId(appointment.id);

    setFollowUpForm({
      date: "",
      time: "",
      reason: "",
      notes: "",
    });

    setMessage("");
  }

  // --------------------------------------------------
  // Close create form
  // --------------------------------------------------

  function closeCreateFollowUp() {
    setCreatingForId(null);

    setFollowUpForm({
      date: "",
      time: "",
      reason: "",
      notes: "",
    });
  }

  // --------------------------------------------------
  // Open reschedule form
  // --------------------------------------------------

  function openRescheduleForm(
    followUp: FollowUpWithPatient
  ) {
    if (hasReplacementFollowUp(followUp)) {
      setMessage(
        "This cancelled follow-up has already been rescheduled and is locked."
      );
      return;
    }

    setReschedulingId(followUp.id);

    setRescheduleForm({
      date: followUp.follow_up_date,
      time: "",
    });

    setMessage("");
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
  // Reschedule cancelled follow-up
  // --------------------------------------------------

  async function rescheduleFollowUp(
    followUp: FollowUpWithPatient
  ) {
    if (hasReplacementFollowUp(followUp)) {
      setMessage(
        "This cancelled follow-up has already been rescheduled."
      );

      closeRescheduleForm();
      return;
    }

    if (!rescheduleForm.date) {
      setMessage(
        "Please select a new follow-up date."
      );
      return;
    }

    if (!rescheduleForm.time) {
      setMessage(
        "Please select a new follow-up time."
      );
      return;
    }

    setUpdatingId(followUp.id);
    setMessage("");

    try {
      // --------------------------------------------------
      // Create NEW follow-up appointment
      // --------------------------------------------------

      const {
        data: newAppointment,
        error: appointmentError,
      } = await supabase
        .from("appointments")
        .insert({
          facility_id: followUp.facility_id,
          patient_id: followUp.patient_id,
          referral_id: followUp.referral_id,
          appointment_date: rescheduleForm.date,
          appointment_time: rescheduleForm.time,
          reason:
            followUp.reason ??
            "Follow-up appointment",
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
            status
          `
        )
        .single();

      if (appointmentError) {
        setMessage(
          `Follow-up appointment could not be rescheduled: ${appointmentError.message}`
        );
        setUpdatingId(null);
        return;
      }

      if (!newAppointment) {
        setMessage(
          "New follow-up appointment could not be created."
        );
        setUpdatingId(null);
        return;
      }

      // --------------------------------------------------
      // Create NEW upcoming follow-up record
      // --------------------------------------------------

      const { error: newFollowUpError } =
        await supabase.from("follow_ups").insert({
          patient_id: followUp.patient_id,
          facility_id: followUp.facility_id,
          appointment_id: newAppointment.id,
          referral_id: followUp.referral_id,
          follow_up_date: rescheduleForm.date,
          reason: followUp.reason,
          notes: followUp.notes,
          status: "upcoming",
        });

      if (newFollowUpError) {
        setMessage(
          `New follow-up record could not be created: ${newFollowUpError.message}`
        );
        setUpdatingId(null);
        return;
      }

      closeRescheduleForm();

      setMessage(
        "Follow-up rescheduled successfully. The previous cancelled follow-up remains as history."
      );

      await loadFollowUps();
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while rescheduling the follow-up."
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
      case "upcoming":
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

  const upcomingCount = followUps.filter(
    (followUp) =>
      followUp.status.toLowerCase() === "upcoming"
  ).length;

  const completedCount = followUps.filter(
    (followUp) =>
      followUp.status.toLowerCase() === "completed"
  ).length;

  const missedCount = followUps.filter(
    (followUp) =>
      followUp.status.toLowerCase() === "missed"
  ).length;

  const cancelledCount = followUps.filter(
    (followUp) =>
      followUp.status.toLowerCase() === "cancelled"
  ).length;

  // --------------------------------------------------
  // Loading
  // --------------------------------------------------

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading follow-ups...
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
                Patient Care Coordination
              </p>

              <h1 className="mt-2 text-4xl font-bold">
                🔄 Follow-ups
              </h1>

              <p className="dashboard-muted mt-2 text-sm">
                Manage follow-up care and follow-up
                appointments after completed treatment.
              </p>
            </div>

            <button
              type="button"
              onClick={loadFollowUps}
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

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Upcoming
            </p>

            <p className="mt-3 text-4xl font-bold">
              {upcomingCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Follow-ups scheduled
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Completed
            </p>

            <p className="mt-3 text-4xl font-bold">
              {completedCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Completed follow-up care
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Missed
            </p>

            <p className="mt-3 text-4xl font-bold">
              {missedCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              AI-handled missed follow-ups
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Cancelled
            </p>

            <p className="mt-3 text-4xl font-bold">
              {cancelledCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Cancelled follow-ups
            </p>
          </div>
        </section>

        {/* Completed Treatment */}
        <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">

          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Completed Treatment
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Create Follow-up Care
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              Only completed first treatment or consultation
              appointments can start the follow-up workflow.
            </p>
          </div>

          {completedTreatments.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-8 text-center">
              <div className="text-5xl">
                ✅
              </div>

              <h3 className="mt-4 text-xl font-bold">
                No treatment awaiting follow-up
              </h3>

              <p className="dashboard-muted mt-2 text-sm">
                A completed treatment will appear here when
                follow-up care needs to be scheduled.
              </p>
            </div>
          ) : (
            <div className="mt-6 space-y-5">
              {completedTreatments.map((appointment) => (
                <div
                  key={appointment.id}
                  className="dashboard-hover-glow rounded-2xl border border-gray-300 p-6"
                >
                  <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">

                    <div>
                      <div className="flex flex-wrap items-center gap-3">
                        <h3 className="text-xl font-bold">
                          {appointment.patient?.full_name ??
                            "Unknown Patient"}
                        </h3>

                        <span className="rounded-full border-2 border-green-500 px-3 py-1 text-xs font-bold uppercase text-green-600">
                          Treatment Completed
                        </span>
                      </div>

                      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">

                        <div>
                          <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                            Completed Date
                          </p>

                          <p className="mt-1 font-medium">
                            {formatDate(
                              appointment.appointment_date
                            )}
                          </p>
                        </div>

                        <div>
                          <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                            Department
                          </p>

                          <p className="mt-1 font-medium">
                            {appointment.department ??
                              "Not specified"}
                          </p>
                        </div>

                        <div>
                          <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                            Doctor
                          </p>

                          <p className="mt-1 font-medium">
                            {appointment.doctor_name ??
                              "Not specified"}
                          </p>
                        </div>

                        <div>
                          <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                            Patient Phone
                          </p>

                          <p className="mt-1 font-medium">
                            {appointment.patient?.phone ??
                              "Not available"}
                          </p>
                        </div>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() =>
                        creatingForId === appointment.id
                          ? closeCreateFollowUp()
                          : openCreateFollowUp(
                              appointment
                            )
                      }
                      className="rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
                    >
                      {creatingForId === appointment.id
                        ? "Close"
                        : "Create Follow-up"}
                    </button>
                  </div>

                  {/* Create Follow-up Form */}
                  {creatingForId === appointment.id && (
                    <div className="mt-6 border-t border-[#D4AF37] pt-6">

                      <h4 className="text-lg font-bold">
                        Schedule Follow-up Appointment
                      </h4>

                      <div className="mt-5 grid gap-5 md:grid-cols-2">

                        <div>
                          <label
                            htmlFor={`followup-date-${appointment.id}`}
                            className="text-sm font-semibold"
                          >
                            Follow-up Date
                          </label>

                          <input
                            id={`followup-date-${appointment.id}`}
                            type="date"
                            value={followUpForm.date}
                            min={
                              new Date()
                                .toISOString()
                                .split("T")[0]
                            }
                            onChange={(event) =>
                              setFollowUpForm(
                                (current) => ({
                                  ...current,
                                  date: event.target.value,
                                })
                              )
                            }
                            className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor={`followup-time-${appointment.id}`}
                            className="text-sm font-semibold"
                          >
                            Follow-up Time
                          </label>

                          <input
                            id={`followup-time-${appointment.id}`}
                            type="time"
                            value={followUpForm.time}
                            onChange={(event) =>
                              setFollowUpForm(
                                (current) => ({
                                  ...current,
                                  time: event.target.value,
                                })
                              )
                            }
                            className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none"
                          />
                        </div>

                        <div className="md:col-span-2">
                          <label
                            htmlFor={`followup-reason-${appointment.id}`}
                            className="text-sm font-semibold"
                          >
                            Follow-up Reason
                          </label>

                          <input
                            id={`followup-reason-${appointment.id}`}
                            type="text"
                            value={followUpForm.reason}
                            placeholder="Example: Post-treatment review"
                            onChange={(event) =>
                              setFollowUpForm(
                                (current) => ({
                                  ...current,
                                  reason: event.target.value,
                                })
                              )
                            }
                            className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none"
                          />
                        </div>

                        <div className="md:col-span-2">
                          <label
                            htmlFor={`followup-notes-${appointment.id}`}
                            className="text-sm font-semibold"
                          >
                            Notes
                          </label>

                          <textarea
                            id={`followup-notes-${appointment.id}`}
                            value={followUpForm.notes}
                            placeholder="Optional follow-up notes"
                            rows={4}
                            onChange={(event) =>
                              setFollowUpForm(
                                (current) => ({
                                  ...current,
                                  notes: event.target.value,
                                })
                              )
                            }
                            className="mt-2 w-full resize-none rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none"
                          />
                        </div>
                      </div>

                      <div className="mt-5 flex flex-col gap-3 sm:flex-row">

                        <button
                          type="button"
                          onClick={() =>
                            createFollowUp(appointment)
                          }
                          disabled={
                            updatingId === appointment.id
                          }
                          className="rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-3 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {updatingId === appointment.id
                            ? "Scheduling..."
                            : "Schedule Follow-up"}
                        </button>

                        <button
                          type="button"
                          onClick={closeCreateFollowUp}
                          className="rounded-lg border-2 border-gray-400 px-5 py-3 text-sm font-bold transition-all hover:bg-gray-100"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Follow-up Records */}
        <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">

          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Follow-up Care
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Follow-up Appointments
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              Only follow-up appointments appear in this
              section.
            </p>
          </div>

          {followUps.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-8 text-center">
              <div className="text-5xl">
                🔄
              </div>

              <h3 className="mt-4 text-xl font-bold">
                No follow-up appointments
              </h3>

              <p className="dashboard-muted mt-2 text-sm">
                Follow-up appointments will appear here after
                completed treatment.
              </p>
            </div>
          ) : (
            <div className="mt-6 space-y-5">
              {followUps.map((followUp) => {
                const isCancelled =
                  followUp.status.toLowerCase() ===
                  "cancelled";

                const isMissed =
                  followUp.status.toLowerCase() ===
                  "missed";

                const isLockedCancelled =
                  isCancelled &&
                  hasReplacementFollowUp(followUp);

                return (
                  <div
                    key={followUp.id}
                    className="dashboard-hover-glow rounded-2xl border border-gray-300 p-6"
                  >
                    <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">

                      {/* Follow-up information */}
                      <div className="flex-1">

                        <div className="flex flex-wrap items-center gap-3">
                          <h3 className="text-xl font-bold">
                            {followUp.patient?.full_name ??
                              "Unknown Patient"}
                          </h3>

                          <span
                            className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${statusClass(
                              followUp.status
                            )}`}
                          >
                            {followUp.status}
                          </span>

                          {isLockedCancelled && (
                            <span className="rounded-full border-2 border-gray-400 px-3 py-1 text-xs font-bold uppercase text-gray-500">
                              Locked
                            </span>
                          )}
                        </div>

                        <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">

                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Follow-up Date
                            </p>

                            <p className="mt-1 font-medium">
                              {formatDate(
                                followUp.follow_up_date
                              )}
                            </p>
                          </div>

                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Patient Phone
                            </p>

                            <p className="mt-1 font-medium">
                              {followUp.patient?.phone ??
                                "Not available"}
                            </p>
                          </div>

                          <div>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Reason
                            </p>

                            <p className="mt-1 font-medium">
                              {followUp.reason ??
                                "Not specified"}
                            </p>
                          </div>
                        </div>

                        {followUp.notes && (
                          <div className="mt-5 rounded-xl border border-gray-300 p-4">
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Notes
                            </p>

                            <p className="mt-2 text-sm leading-6">
                              {followUp.notes}
                            </p>
                          </div>
                        )}

                        {followUp.referral_id && (
                          <Link
                            href={`/dashboard/referrals/${followUp.referral_id}`}
                            className="mt-5 inline-flex rounded-lg border border-[#D4AF37] px-4 py-2 text-sm font-semibold transition-all hover:bg-[#D4AF37] hover:text-black"
                          >
                            View Referral
                          </Link>
                        )}

                        {/* Missed */}
                        {isMissed && (
                          <div className="mt-5 rounded-xl border border-red-300 bg-red-50 p-4">
                            <p className="text-sm font-bold text-red-600">
                              AI Handling
                            </p>

                            <p className="mt-1 text-xs leading-5 text-red-600">
                              This follow-up was missed.
                              Voxera AI handles patient
                              contact and next steps. No
                              dashboard action is required.
                            </p>
                          </div>
                        )}

                        {/* Cancelled */}
                        {isCancelled &&
                          !isLockedCancelled && (
                            <div className="mt-6 rounded-xl border-2 border-[#D4AF37] bg-[#faf9f1] p-5">

                              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">

                                <div>
                                  <h4 className="font-bold">
                                    Follow-up Cancelled
                                  </h4>

                                  <p className="dashboard-muted mt-1 text-sm">
                                    Schedule a new follow-up
                                    appointment.
                                  </p>
                                </div>

                                <button
                                  type="button"
                                  onClick={() =>
                                    reschedulingId ===
                                    followUp.id
                                      ? closeRescheduleForm()
                                      : openRescheduleForm(
                                          followUp
                                        )
                                  }
                                  disabled={
                                    updatingId ===
                                    followUp.id
                                  }
                                  className="rounded-lg border-2 border-[#D4AF37] px-4 py-2 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  {reschedulingId ===
                                  followUp.id
                                    ? "Close"
                                    : "Reschedule Follow-up"}
                                </button>
                              </div>

                              {reschedulingId ===
                                followUp.id && (
                                <div className="mt-5 border-t border-[#D4AF37] pt-5">

                                  <div className="grid gap-5 md:grid-cols-2">

                                    <div>
                                      <label
                                        htmlFor={`reschedule-date-${followUp.id}`}
                                        className="text-sm font-semibold"
                                      >
                                        New Date
                                      </label>

                                      <input
                                        id={`reschedule-date-${followUp.id}`}
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
                                        className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none"
                                      />
                                    </div>

                                    <div>
                                      <label
                                        htmlFor={`reschedule-time-${followUp.id}`}
                                        className="text-sm font-semibold"
                                      >
                                        New Time
                                      </label>

                                      <input
                                        id={`reschedule-time-${followUp.id}`}
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
                                        className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-medium text-black outline-none"
                                      />
                                    </div>
                                  </div>

                                  <div className="mt-5 flex flex-col gap-3 sm:flex-row">

                                    <button
                                      type="button"
                                      onClick={() =>
                                        rescheduleFollowUp(
                                          followUp
                                        )
                                      }
                                      disabled={
                                        updatingId ===
                                        followUp.id
                                      }
                                      className="rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-3 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)] disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                      {updatingId ===
                                      followUp.id
                                        ? "Rescheduling..."
                                        : "Confirm Reschedule"}
                                    </button>

                                    <button
                                      type="button"
                                      onClick={
                                        closeRescheduleForm
                                      }
                                      className="rounded-lg border-2 border-gray-400 px-5 py-3 text-sm font-bold transition-all hover:bg-gray-100"
                                    >
                                      Cancel
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}

                        {/* Locked cancelled */}
                        {isLockedCancelled && (
                          <div className="mt-5 rounded-xl border border-gray-300 bg-gray-50 p-4">
                            <p className="text-sm font-semibold text-gray-600">
                              🔒 This cancelled follow-up
                              is locked.
                            </p>

                            <p className="dashboard-muted mt-1 text-xs">
                              A replacement follow-up has
                              already been scheduled. This
                              record is kept as history.
                            </p>
                          </div>
                        )}
                      </div>

                      {/* Status control */}
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
                              This historical follow-up cannot
                              be edited.
                            </p>
                          </>
                        ) : isMissed ? (
                          <>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Status
                            </p>

                            <div className="mt-2 rounded-lg border-2 border-red-300 bg-red-50 px-3 py-3 text-sm font-bold text-red-600">
                              Missed — AI Handling
                            </div>

                            <p className="dashboard-muted mt-3 text-xs leading-5">
                              No dashboard action is required
                              for a missed follow-up.
                            </p>
                          </>
                        ) : (
                          <>
                            <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                              Update Status
                            </p>

                            <select
                              value={followUp.status}
                              disabled={
                                updatingId ===
                                followUp.id
                              }
                              onChange={(event) =>
                                updateFollowUpStatus(
                                  followUp.id,
                                  event.target.value
                                )
                              }
                              className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-semibold text-black outline-none disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <option value="upcoming">
                                Upcoming
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
                              followUp.id && (
                              <p className="dashboard-muted mt-2 text-xs">
                                Updating...
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