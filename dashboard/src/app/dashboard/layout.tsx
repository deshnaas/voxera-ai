"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";
import Icon from "./components/Icon";
import { Skeleton } from "./components/ui";
import { StaffProvider, useStaff } from "@/lib/staff";

export default function DashboardLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <StaffProvider>
      <Shell>{children}</Shell>
    </StaffProvider>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { loading, error, incoming, dismissIncoming, signOut } = useStaff();

  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem("voxera-theme") === "dark") setTheme("dark");
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    document.documentElement.classList.remove("dashboard-light", "dashboard-dark");
    document.documentElement.classList.add(theme === "dark" ? "dashboard-dark" : "dashboard-light");
  }, [theme]);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem("voxera-theme", next);
    } catch {
      /* ignore */
    }
  }

  function openReferral() {
    const id = incoming?.referral_id;
    dismissIncoming();
    if (id) router.push(`/dashboard/referrals/${id}`);
  }

  const shellCls = `dashboard-shell ${theme === "dark" ? "dashboard-dark" : "dashboard-light"}`;

  if (error) {
    return (
      <div className={shellCls}>
        <div className="mx-auto max-w-lg px-6 py-24 text-center">
          <div className="callout callout-critical">{error}</div>
          <button className="btn mt-6" onClick={() => void signOut()}>Sign out</button>
        </div>
      </div>
    );
  }

  return (
    <div className={shellCls}>
      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />

      <div className="min-h-screen lg:pl-64">
        <Topbar theme={theme} onToggleTheme={toggleTheme} onMenu={() => setMenuOpen(true)} />
        {loading ? (
          <div className="mx-auto max-w-7xl space-y-4 px-6 py-8" aria-busy="true">
            <Skeleton className="h-10 w-72" />
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}
            </div>
            <Skeleton className="h-72" />
          </div>
        ) : (
          children
        )}
      </div>

      {/* New referral / emergency toast (realtime) */}
      {incoming && (
        <div className="fixed right-4 top-4 z-[9999] w-[calc(100%-2rem)] max-w-md" role="alert">
          <div className="card triage triage-critical p-4" style={{ boxShadow: "0 18px 50px rgba(0,0,0,0.35)" }}>
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full" style={{ background: "var(--critical)", color: "#fff" }}>
                <Icon name="alert" size={20} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="eyebrow" style={{ color: "var(--critical-ink)" }}>Voxera AI · new referral</p>
                <p className="mt-1 text-sm font-semibold leading-snug">{incoming.message}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {incoming.referral_id && (
                    <button className="btn btn-danger btn-sm" onClick={openReferral}>Open referral</button>
                  )}
                  <button className="btn btn-sm" onClick={() => { dismissIncoming(); router.push("/dashboard/emergency"); }}>
                    Emergency board
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={dismissIncoming}>Dismiss</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
