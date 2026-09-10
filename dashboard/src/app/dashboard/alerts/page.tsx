"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

type AlertItem = {
  id: string;
  type: "emergency" | "referral" | "beds" | "appointment" | "followup";
  priority: "high" | "medium";
  title: string;
  description: string;
  href: string | null;
  createdAt: string;
};

type Bed = {
  bed_type: string;
  total_beds: number;
  occupied_beds: number;
};

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    loadAlerts();
  }, []);

  async function loadAlerts() {
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
      // 2. Get hospital/facility
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

      const generatedAlerts: AlertItem[] = [];

      // --------------------------------------------------
      // 3. Emergency cases
      // --------------------------------------------------

      const {
        data: emergencyData,
        error: emergencyError,
      } = await supabase
        .from("emergency_cases")
        .select(
          `
            id,
            patient_id,
            referral_id,
            priority,
            status,
            symptoms_summary,
            immediate_action,
            created_at
          `
        )
        .eq("facility_id", facilityId)
        .eq("status", "active")
        .order("created_at", {
          ascending: false,
        });

      if (emergencyError) {
        console.error(
          "Emergency alerts could not be loaded:",
          emergencyError
        );
      } else {
        for (const emergency of emergencyData ?? []) {
          generatedAlerts.push({
            id: `emergency-${emergency.id}`,
            type: "emergency",
            priority: "high",
            title: "Active Emergency Case",
            description:
              emergency.symptoms_summary ??
              emergency.immediate_action ??
              "An active emergency case requires attention.",
            href: emergency.referral_id
              ? `/dashboard/referrals/${emergency.referral_id}`
              : null,
            createdAt: emergency.created_at,
          });
        }
      }

      // --------------------------------------------------
      // 4. Pending high/emergency referrals
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
            reason,
            urgency,
            status,
            required_service,
            created_at
          `
        )
        .eq("receiving_facility_id", facilityId)
        .eq("status", "pending")
        .in("urgency", ["high", "emergency"])
        .order("created_at", {
          ascending: false,
        });

      if (referralError) {
        console.error(
          "Referral alerts could not be loaded:",
          referralError
        );
      } else {
        for (const referral of referralData ?? []) {
          generatedAlerts.push({
            id: `referral-${referral.id}`,
            type: "referral",
            priority: "high",
            title: "High-Priority Referral Awaiting Action",
            description:
              referral.reason ??
              referral.required_service ??
              "A high-priority referral is waiting for hospital action.",
            href: `/dashboard/referrals/${referral.id}`,
            createdAt: referral.created_at,
          });
        }
      }

      // --------------------------------------------------
      // 5. Bed availability
      // --------------------------------------------------

      const {
        data: bedData,
        error: bedError,
      } = await supabase
        .from("facility_beds")
        .select(
          `
            bed_type,
            total_beds,
            occupied_beds
          `
        )
        .eq("facility_id", facilityId);

      if (bedError) {
        console.error(
          "Bed alerts could not be loaded:",
          bedError
        );
      } else {
        const beds = (bedData ?? []) as Bed[];

        for (const bed of beds) {
          const available =
            bed.total_beds - bed.occupied_beds;

          // Full
          if (available <= 0) {
            generatedAlerts.push({
              id: `beds-full-${bed.bed_type}`,
              type: "beds",
              priority: "high",
              title: `${bed.bed_type} Beds Full`,
              description:
                `There are currently no available ${bed.bed_type} beds.`,
              href: "/dashboard/beds",
              createdAt: new Date().toISOString(),
            });
          }

          // Low availability
          else if (available <= 3) {
            generatedAlerts.push({
              id: `beds-low-${bed.bed_type}`,
              type: "beds",
              priority: "medium",
              title: `Low ${bed.bed_type} Bed Availability`,
              description:
                `Only ${available} ${bed.bed_type} bed${
                  available === 1 ? "" : "s"
                } currently available.`,
              href: "/dashboard/beds",
              createdAt: new Date().toISOString(),
            });
          }
        }
      }

      // --------------------------------------------------
      // 6. Missed first-treatment appointments
      //
      // IMPORTANT:
      // Follow-up appointments are identified through
      // follow_ups.appointment_id and are excluded.
      // --------------------------------------------------

      const {
        data: followUpData,
        error: followUpError,
      } = await supabase
        .from("follow_ups")
        .select("appointment_id")
        .eq("facility_id", facilityId);

      if (followUpError) {
        console.error(
          "Follow-up information could not be loaded:",
          followUpError
        );
      }

      const followUpAppointmentIds = new Set(
        (followUpData ?? [])
          .map((item) => item.appointment_id)
          .filter(
            (id): id is string => Boolean(id)
          )
      );

      const {
        data: missedAppointmentData,
        error: missedAppointmentError,
      } = await supabase
        .from("appointments")
        .select(
          `
            id,
            patient_id,
            appointment_date,
            appointment_time,
            reason,
            status,
            created_at
          `
        )
        .eq("facility_id", facilityId)
        .eq("status", "missed")
        .order("appointment_date", {
          ascending: false,
        });

      if (missedAppointmentError) {
        console.error(
          "Missed appointment alerts could not be loaded:",
          missedAppointmentError
        );
      } else {
        for (const appointment of
          missedAppointmentData ?? []) {
          if (
            followUpAppointmentIds.has(appointment.id)
          ) {
            continue;
          }

          generatedAlerts.push({
            id: `appointment-${appointment.id}`,
            type: "appointment",
            priority: "medium",
            title: "Missed First Appointment",
            description:
              appointment.reason ??
              "A first treatment or consultation appointment was missed. Voxera AI handles patient follow-up.",
            href: null,
            createdAt: appointment.created_at,
          });
        }
      }

      // --------------------------------------------------
      // 7. Missed follow-ups
      //
      // Hospital should see these but does not take
      // patient-contact action. AI handles them.
      // --------------------------------------------------

      const {
        data: missedFollowUps,
        error: missedFollowUpError,
      } = await supabase
        .from("follow_ups")
        .select(
          `
            id,
            reason,
            status,
            created_at,
            appointment_id
          `
        )
        .eq("facility_id", facilityId)
        .eq("status", "missed")
        .order("created_at", {
          ascending: false,
        });

      if (missedFollowUpError) {
        console.error(
          "Missed follow-up alerts could not be loaded:",
          missedFollowUpError
        );
      } else {
        for (const followUp of missedFollowUps ?? []) {
          generatedAlerts.push({
            id: `followup-${followUp.id}`,
            type: "followup",
            priority: "medium",
            title: "Missed Follow-up",
            description:
              followUp.reason ??
              "A follow-up appointment was missed. Voxera AI handles patient contact and next steps.",
            href: null,
            createdAt: followUp.created_at,
          });
        }
      }

      // --------------------------------------------------
      // 8. Sort alerts
      //
      // High priority first, then newest.
      // --------------------------------------------------

      generatedAlerts.sort((a, b) => {
        if (
          a.priority === "high" &&
          b.priority !== "high"
        ) {
          return -1;
        }

        if (
          a.priority !== "high" &&
          b.priority === "high"
        ) {
          return 1;
        }

        return (
          new Date(b.createdAt).getTime() -
          new Date(a.createdAt).getTime()
        );
      });

      setAlerts(generatedAlerts);
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while loading alerts."
      );
    } finally {
      setLoading(false);
    }
  }

  // --------------------------------------------------
  // Helpers
  // --------------------------------------------------

  function formatDateTime(value: string) {
    return new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  }

  function alertBorder(alert: AlertItem) {
    if (alert.priority === "high") {
      return "border-red-500";
    }

    return "border-orange-400";
  }

  function alertBadge(alert: AlertItem) {
    if (alert.priority === "high") {
      return "border-red-500 text-red-600";
    }

    return "border-orange-400 text-orange-600";
  }

  function alertIcon(type: AlertItem["type"]) {
    switch (type) {
      case "emergency":
        return "🚨";

      case "referral":
        return "🔴";

      case "beds":
        return "🛏️";

      case "appointment":
        return "📅";

      case "followup":
        return "🔄";

      default:
        return "⚠️";
    }
  }

  const highPriorityCount = alerts.filter(
    (alert) => alert.priority === "high"
  ).length;

  const mediumPriorityCount = alerts.filter(
    (alert) => alert.priority === "medium"
  ).length;

  const emergencyCount = alerts.filter(
    (alert) => alert.type === "emergency"
  ).length;

  const bedAlertCount = alerts.filter(
    (alert) => alert.type === "beds"
  ).length;

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading alerts...
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
                ⚠️ Alerts
              </h1>

              <p className="dashboard-muted mt-2 text-sm">
                Important operational events requiring hospital
                awareness.
              </p>
            </div>

            <button
              type="button"
              onClick={loadAlerts}
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

        {/* Summary */}

        <section className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-red-500 p-6">
            <p className="dashboard-muted text-sm font-semibold">
              High Priority
            </p>

            <p className="mt-3 text-4xl font-bold">
              {highPriorityCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Immediate hospital attention
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-orange-400 p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Medium Priority
            </p>

            <p className="mt-3 text-4xl font-bold">
              {mediumPriorityCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Operational attention
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-red-500 p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Emergency
            </p>

            <p className="mt-3 text-4xl font-bold">
              {emergencyCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Active emergency cases
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Bed Alerts
            </p>

            <p className="mt-3 text-4xl font-bold">
              {bedAlertCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Capacity warnings
            </p>
          </div>
        </section>

        {/* Alerts List */}

        <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">

          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Attention Required
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Current Alerts
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              Alerts are generated from your hospital's
              operational data.
            </p>
          </div>

          {alerts.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-10 text-center">

              <div className="text-5xl">
                ✅
              </div>

              <h3 className="mt-4 text-xl font-bold">
                No active alerts
              </h3>

              <p className="dashboard-muted mt-2 text-sm">
                Your hospital currently has no operational
                alerts requiring attention.
              </p>
            </div>
          ) : (
            <div className="mt-6 space-y-5">

              {alerts.map((alert) => (
                <div
                  key={alert.id}
                  className={`dashboard-hover-glow rounded-2xl border-2 ${alertBorder(
                    alert
                  )} p-6`}
                >

                  <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">

                    <div className="flex gap-4">

                      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-gray-300 text-2xl">
                        {alertIcon(alert.type)}
                      </div>

                      <div>

                        <div className="flex flex-wrap items-center gap-3">

                          <h3 className="text-xl font-bold">
                            {alert.title}
                          </h3>

                          <span
                            className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${alertBadge(
                              alert
                            )}`}
                          >
                            {alert.priority}
                          </span>
                        </div>

                        <p className="dashboard-muted mt-3 max-w-3xl text-sm leading-6">
                          {alert.description}
                        </p>

                        <p className="dashboard-muted mt-3 text-xs">
                          {formatDateTime(alert.createdAt)}
                        </p>
                      </div>
                    </div>

                    {alert.href && (
                      <Link
                        href={alert.href}
                        className="shrink-0 rounded-lg border-2 border-[#D4AF37] px-4 py-2 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black"
                      >
                        View Details
                      </Link>
                    )}

                    {!alert.href &&
                      (alert.type === "appointment" ||
                        alert.type === "followup") && (
                        <div className="shrink-0 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-center text-xs font-semibold text-gray-600">
                          AI Handling
                        </div>
                      )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Responsibility Information */}

        <section className="dashboard-subtle mt-8 rounded-2xl border border-gray-300 p-6">

          <h3 className="font-bold">
            🧭 Alert Responsibility
          </h3>

          <div className="mt-4 grid gap-4 md:grid-cols-2">

            <div>
              <p className="font-semibold">
                Hospital Action
              </p>

              <p className="dashboard-muted mt-1 text-sm leading-6">
                Emergency cases, pending high-priority referrals,
                and hospital capacity alerts require hospital
                awareness and operational action.
              </p>
            </div>

            <div>
              <p className="font-semibold">
                Voxera AI Action
              </p>

              <p className="dashboard-muted mt-1 text-sm leading-6">
                Missed patient appointments and missed follow-ups
                are handled by Voxera AI for patient contact and
                next-step coordination.
              </p>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}