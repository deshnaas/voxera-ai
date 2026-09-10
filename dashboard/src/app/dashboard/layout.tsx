"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Sidebar from "./components/Sidebar";
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

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const router = useRouter();

  const [theme, setTheme] =
    useState<"light" | "dark">("light");

  const [notification, setNotification] =
    useState<Notification | null>(null);

  const [showNotification, setShowNotification] =
    useState(false);

  // ==================================================
  // LOAD SAVED THEME
  // ==================================================

  useEffect(() => {
    const savedTheme =
      localStorage.getItem("voxera-theme");

    if (savedTheme === "dark") {
      setTheme("dark");
    } else {
      setTheme("light");
    }
  }, []);

  // ==================================================
  // APPLY THEME
  // ==================================================

  useEffect(() => {
    document.documentElement.classList.remove(
      "dashboard-light",
      "dashboard-dark"
    );

    document.documentElement.classList.add(
      theme === "dark"
        ? "dashboard-dark"
        : "dashboard-light"
    );
  }, [theme]);

  // ==================================================
  // REAL-TIME REFERRAL NOTIFICATIONS
  // ==================================================

  useEffect(() => {
    let channel:
      | ReturnType<typeof supabase.channel>
      | null = null;

    let cancelled = false;

    async function setupNotificationListener() {
      // ------------------------------------------------
      // 1. Get logged-in user
      // ------------------------------------------------

      const {
        data: { user },
        error: userError,
      } = await supabase.auth.getUser();

      if (cancelled) {
        return;
      }

      if (userError || !user) {
        console.log(
          "🔔 No logged-in hospital user found."
        );
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

      if (cancelled) {
        return;
      }

      if (hospitalUserError || !hospitalUser) {
        console.error(
          "🔔 Hospital information could not be found:",
          hospitalUserError
        );
        return;
      }

      const currentFacilityId =
        hospitalUser.facility_id;

      console.log(
        "🏥 Hospital facility:",
        currentFacilityId
      );

      // ------------------------------------------------
      // 3. Create channel
      // ------------------------------------------------

      channel = supabase.channel(
        `hospital-referral-notifications-${currentFacilityId}`
      );

      console.log(
        "🔔 Notification channel created."
      );

      // ------------------------------------------------
      // 4. Attach Postgres INSERT listener
      //
      // IMPORTANT:
      // This happens BEFORE subscribe().
      // ------------------------------------------------

      channel.on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "referral_notifications",
          filter: `target_facility_id=eq.${currentFacilityId}`,
        },
        (payload) => {
          console.log(
            "🔔 NEW VOXERA AI REFERRAL:",
            payload
          );

          const newNotification =
            payload.new as Notification;

          setNotification(newNotification);
          setShowNotification(true);
        }
      );

      console.log(
        "🔔 Notification listener attached."
      );

      // ------------------------------------------------
      // 5. NOW subscribe
      // ------------------------------------------------

      channel.subscribe((status) => {
        console.log(
          "🔔 Referral notification realtime status:",
          status
        );

        if (status === "SUBSCRIBED") {
          console.log(
            "✅ Voxera referral notification listener is active."
          );
        }

        if (status === "CHANNEL_ERROR") {
          console.error(
            "❌ Realtime channel error."
          );
        }

        if (status === "TIMED_OUT") {
          console.error(
            "❌ Realtime subscription timed out."
          );
        }
      });
    }

    setupNotificationListener();

    // ------------------------------------------------
    // Cleanup
    // ------------------------------------------------

    return () => {
      cancelled = true;

      if (channel) {
        console.log(
          "🔕 Removing referral notification channel."
        );

        supabase.removeChannel(channel);
        channel = null;
      }
    };
  }, []);

  // ==================================================
  // CLOSE POPUP
  // ==================================================

  function closeNotification() {
    setShowNotification(false);
  }

  // ==================================================
  // OPEN REFERRAL
  // ==================================================

  function openReferral() {
    if (!notification?.referral_id) {
      setShowNotification(false);
      return;
    }

    setShowNotification(false);

    router.push(
      `/dashboard/referrals/${notification.referral_id}`
    );
  }

  // ==================================================
  // THEME
  // ==================================================

  function handleThemeChange(
    nextTheme: "light" | "dark"
  ) {
    setTheme(nextTheme);

    localStorage.setItem(
      "voxera-theme",
      nextTheme
    );
  }

  // ==================================================
  // UI
  // ==================================================

  return (
    <div
      className={`dashboard-shell ${
        theme === "dark"
          ? "dashboard-dark"
          : "dashboard-light"
      }`}
    >
      {/* Sidebar */}

      <Sidebar
        theme={theme}
        onThemeChange={handleThemeChange}
      />

      {/* Page */}

      <div className="min-h-screen lg:pl-64">
        {children}
      </div>

      {/* ==================================================
          GLOBAL NEW REFERRAL POPUP
          ================================================== */}

      {showNotification && notification && (
        <div className="fixed inset-x-0 top-5 z-[9999] flex justify-center px-4">

          <div className="w-full max-w-lg rounded-2xl border-2 border-[#D4AF37] bg-white p-5 text-black shadow-[0_10px_40px_rgba(0,0,0,0.25)]">

            <div className="flex items-start gap-4">

              {/* Icon */}

              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border-2 border-[#D4AF37] bg-[#faf9f1] text-2xl">
                🔔
              </div>

              {/* Content */}

              <div className="min-w-0 flex-1">

                <div className="flex items-start justify-between gap-3">

                  <div>
                    <p className="text-xs font-bold uppercase tracking-widest text-[#9a7b13]">
                      Voxera AI
                    </p>

                    <h2 className="mt-1 text-xl font-bold">
                      New Patient Referral
                    </h2>
                  </div>

                  <button
                    type="button"
                    onClick={closeNotification}
                    className="rounded-lg px-2 py-1 text-xl font-bold text-gray-500 transition-colors hover:bg-gray-100 hover:text-black"
                    aria-label="Close notification"
                  >
                    ×
                  </button>
                </div>

                {/* Message */}

                <p className="mt-3 text-sm leading-6 text-gray-700">
                  {notification.message}
                </p>

                <p className="mt-2 text-xs text-gray-500">
                  A new referral has been sent to your
                  hospital by Voxera AI.
                </p>

                {/* Actions */}

                <div className="mt-4 flex flex-col gap-3 sm:flex-row">

                  {notification.referral_id && (
                    <button
                      type="button"
                      onClick={openReferral}
                      className="rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-2.5 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)]"
                    >
                      View Referral
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={closeNotification}
                    className="rounded-lg border-2 border-gray-300 px-5 py-2.5 text-sm font-semibold text-gray-700 transition-all hover:bg-gray-100"
                  >
                    Dismiss
                  </button>

                  <Link
                    href="/dashboard/notifications"
                    onClick={closeNotification}
                    className="rounded-lg border-2 border-gray-300 px-5 py-2.5 text-center text-sm font-semibold text-gray-700 transition-all hover:bg-gray-100"
                  >
                    Notifications
                  </Link>

                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}