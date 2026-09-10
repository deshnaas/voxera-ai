"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

type Notification = {
  id: string;
  referral_id: string | null;
  target_facility_id: string;
  type: string;
  message: string;
  is_read: boolean;
  created_at: string;
};

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState<
    Notification[]
  >([]);

  const [loading, setLoading] = useState(true);

  const [message, setMessage] = useState("");

  const [updatingId, setUpdatingId] =
    useState<string | null>(null);

  const [facilityId, setFacilityId] =
    useState<string | null>(null);

  useEffect(() => {
    loadNotifications();
  }, []);

  // ==================================================
  // LOAD NOTIFICATIONS
  // ==================================================

  async function loadNotifications() {
    setLoading(true);
    setMessage("");

    try {
      // ------------------------------------------------
      // 1. Get logged-in user
      // ------------------------------------------------

      const {
        data: { user },
        error: userError,
      } = await supabase.auth.getUser();

      if (userError || !user) {
        setMessage("You are not logged in.");
        setLoading(false);
        return;
      }

      // ------------------------------------------------
      // 2. Find hospital facility
      // ------------------------------------------------

      const {
        data: hospitalUser,
        error: hospitalUserError,
      } = await supabase
        .from("hospital_users")
        .select("facility_id")
        .eq("user_id", user.id)
        .single();

      if (hospitalUserError || !hospitalUser) {
        setMessage(
          "Hospital information could not be found."
        );
        setLoading(false);
        return;
      }

      const currentFacilityId =
        hospitalUser.facility_id;

      setFacilityId(currentFacilityId);

      // ------------------------------------------------
      // 3. Load notifications
      // ------------------------------------------------

      const {
        data: notificationData,
        error: notificationError,
      } = await supabase
        .from("referral_notifications")
        .select(
          `
            id,
            referral_id,
            target_facility_id,
            type,
            message,
            is_read,
            created_at
          `
        )
        .eq(
          "target_facility_id",
          currentFacilityId
        )
        .order("created_at", {
          ascending: false,
        });

      if (notificationError) {
        setMessage(
          `Notifications could not be loaded: ${notificationError.message}`
        );
        setLoading(false);
        return;
      }

      setNotifications(
        (notificationData ?? []) as Notification[]
      );
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while loading notifications."
      );
    } finally {
      setLoading(false);
    }
  }

  // ==================================================
  // MARK ONE NOTIFICATION AS READ
  // ==================================================

  async function markAsRead(
    notificationId: string
  ) {
    setUpdatingId(notificationId);
    setMessage("");

    const { error } = await supabase
      .from("referral_notifications")
      .update({
        is_read: true,
      })
      .eq("id", notificationId);

    if (error) {
      setMessage(
        `Could not mark notification as read: ${error.message}`
      );
      setUpdatingId(null);
      return;
    }

    setNotifications((current) =>
      current.map((notification) =>
        notification.id === notificationId
          ? {
              ...notification,
              is_read: true,
            }
          : notification
      )
    );

    setUpdatingId(null);
  }

  // ==================================================
  // MARK ALL AS READ
  // ==================================================

  async function markAllAsRead() {
    if (!facilityId) {
      return;
    }

    const unreadNotifications =
      notifications.filter(
        (notification) => !notification.is_read
      );

    if (unreadNotifications.length === 0) {
      setMessage("All notifications are already read.");
      return;
    }

    setUpdatingId("all");
    setMessage("");

    const { error } = await supabase
      .from("referral_notifications")
      .update({
        is_read: true,
      })
      .eq(
        "target_facility_id",
        facilityId
      )
      .eq("is_read", false);

    if (error) {
      setMessage(
        `Could not mark notifications as read: ${error.message}`
      );
      setUpdatingId(null);
      return;
    }

    setNotifications((current) =>
      current.map((notification) => ({
        ...notification,
        is_read: true,
      }))
    );

    setMessage(
      "All notifications marked as read."
    );

    setUpdatingId(null);
  }

  // ==================================================
  // FORMAT DATE
  // ==================================================

  function formatDateTime(value: string) {
    return new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  }

  // ==================================================
  // NOTIFICATION ICON
  // ==================================================

  function notificationIcon(type: string) {
    switch (type.toLowerCase()) {
      case "new_referral":
        return "🏥";

      case "referral":
        return "📋";

      case "emergency":
        return "🚨";

      default:
        return "🔔";
    }
  }

  // ==================================================
  // COUNTS
  // ==================================================

  const unreadCount = notifications.filter(
    (notification) => !notification.is_read
  ).length;

  const readCount = notifications.filter(
    (notification) => notification.is_read
  ).length;

  // ==================================================
  // LOADING
  // ==================================================

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading notifications...
        </p>
      </main>
    );
  }

  // ==================================================
  // PAGE
  // ==================================================

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* ==================================================
            HEADER
            ================================================== */}

        <section className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">

          <div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">

            <div>
              <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                Hospital Communication
              </p>

              <h1 className="mt-2 text-4xl font-bold">
                🔔 Notifications
              </h1>

              <p className="dashboard-muted mt-2 text-sm">
                Notifications and referral messages sent to
                this hospital by Voxera AI.
              </p>
            </div>

            <div className="flex flex-wrap gap-3">

              <button
                type="button"
                onClick={loadNotifications}
                disabled={updatingId === "all"}
                className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
              >
                Refresh
              </button>

              <button
                type="button"
                onClick={markAllAsRead}
                disabled={
                  updatingId === "all" ||
                  unreadCount === 0
                }
                className="rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-3 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {updatingId === "all"
                  ? "Updating..."
                  : "Mark All Read"}
              </button>
            </div>
          </div>
        </section>

        {/* ==================================================
            MESSAGE
            ================================================== */}

        {message && (
          <div className="mt-5 rounded-xl border-2 border-[#D4AF37] bg-white px-5 py-4 text-sm font-semibold">
            {message}
          </div>
        )}

        {/* ==================================================
            SUMMARY
            ================================================== */}

        <section className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Total Notifications
            </p>

            <p className="mt-3 text-4xl font-bold">
              {notifications.length}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Notifications received
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Unread
            </p>

            <p className="mt-3 text-4xl font-bold">
              {unreadCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Require attention
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Read
            </p>

            <p className="mt-3 text-4xl font-bold">
              {readCount}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Previously reviewed
            </p>
          </div>
        </section>

        {/* ==================================================
            NOTIFICATION LIST
            ================================================== */}

        <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">

          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Notification History
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Hospital Notifications
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              New referrals from Voxera AI will remain here
              after the popup is dismissed.
            </p>
          </div>

          {notifications.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-10 text-center">

              <div className="text-5xl">
                🔔
              </div>

              <h3 className="mt-4 text-xl font-bold">
                No notifications
              </h3>

              <p className="dashboard-muted mt-2 text-sm">
                When Voxera AI sends a referral to this
                hospital, the notification will appear here.
              </p>
            </div>
          ) : (
            <div className="mt-6 space-y-5">

              {notifications.map((notification) => (

                <div
                  key={notification.id}
                  className={`dashboard-hover-glow rounded-2xl border-2 p-6 ${
                    notification.is_read
                      ? "border-gray-300"
                      : "border-[#D4AF37]"
                  }`}
                >

                  <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">

                    {/* ------------------------------------------------
                        Notification information
                        ------------------------------------------------ */}

                    <div className="flex gap-4">

                      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-gray-300 text-2xl">
                        {notificationIcon(
                          notification.type
                        )}
                      </div>

                      <div className="min-w-0">

                        <div className="flex flex-wrap items-center gap-3">

                          <h3 className="text-xl font-bold">
                            {notification.type ===
                            "new_referral"
                              ? "New Patient Referral"
                              : "Hospital Notification"}
                          </h3>

                          {!notification.is_read && (
                            <span className="rounded-full border-2 border-[#D4AF37] px-3 py-1 text-xs font-bold uppercase text-[#9a7b13]">
                              New
                            </span>
                          )}

                          {notification.is_read && (
                            <span className="rounded-full border border-gray-400 px-3 py-1 text-xs font-bold uppercase text-gray-500">
                              Read
                            </span>
                          )}
                        </div>

                        <p className="mt-3 text-sm leading-6">
                          {notification.message}
                        </p>

                        <p className="dashboard-muted mt-3 text-xs">
                          Received:{" "}
                          {formatDateTime(
                            notification.created_at
                          )}
                        </p>

                        {/* Referral */}

                        {notification.referral_id && (
                          <div className="mt-5 flex flex-wrap gap-3">

                            <Link
                              href={`/dashboard/referrals/${notification.referral_id}`}
                              onClick={() => {
                                if (
                                  !notification.is_read
                                ) {
                                  markAsRead(
                                    notification.id
                                  );
                                }
                              }}
                              className="rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-2.5 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)]"
                            >
                              View Referral
                            </Link>

                          </div>
                        )}

                      </div>
                    </div>

                    {/* ------------------------------------------------
                        Mark read
                        ------------------------------------------------ */}

                    <div className="shrink-0">

                      {!notification.is_read ? (
                        <button
                          type="button"
                          onClick={() =>
                            markAsRead(
                              notification.id
                            )
                          }
                          disabled={
                            updatingId ===
                            notification.id
                          }
                          className="rounded-lg border-2 border-[#D4AF37] px-4 py-2.5 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {updatingId ===
                          notification.id
                            ? "Updating..."
                            : "Mark as Read"}
                        </button>
                      ) : (
                        <div className="rounded-lg border border-gray-300 bg-gray-50 px-4 py-2.5 text-center text-xs font-semibold text-gray-500">
                          Already Read
                        </div>
                      )}

                    </div>
                  </div>
                </div>

              ))}
            </div>
          )}
        </section>

        {/* ==================================================
            INFORMATION
            ================================================== */}

        <section className="dashboard-subtle mt-8 rounded-2xl border border-gray-300 p-6">

          <h3 className="font-bold">
            🤖 Voxera AI → Hospital
          </h3>

          <p className="dashboard-muted mt-2 text-sm leading-6">
            These notifications represent referrals sent to
            this hospital by Voxera AI. The hospital receives
            the referral and decides whether to accept or
            reject it. Referral routing and rerouting remain
            under Voxera AI responsibility.
          </p>
        </section>

      </div>
    </main>
  );
}