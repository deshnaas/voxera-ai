"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import Icon, { type IconName } from "./Icon";
import { useStaff } from "@/lib/staff";

type Item = {
  label: string;
  href: string;
  icon: IconName;
  badge?: "emergencies" | "pendingReferrals" | "unreadNotifications" | "liveCalls";
};

const GROUPS: Array<{ title: string; items: Item[] }> = [
  {
    title: "Critical care",
    items: [
      { label: "Command Center", href: "/dashboard", icon: "grid" },
      { label: "Emergency", href: "/dashboard/emergency", icon: "alert", badge: "emergencies" },
      { label: "Referrals", href: "/dashboard/referrals", icon: "refer", badge: "pendingReferrals" },
    ],
  },
  {
    title: "Patients",
    items: [
      { label: "Patients", href: "/dashboard/patients", icon: "users" },
      { label: "Voxera Calls", href: "/dashboard/calls", icon: "headset", badge: "liveCalls" },
    ],
  },
  {
    title: "Scheduling",
    items: [
      { label: "Appointments", href: "/dashboard/appointments", icon: "calendar" },
      { label: "Follow-ups", href: "/dashboard/follow-ups", icon: "repeat" },
    ],
  },
  {
    title: "Operations",
    items: [
      { label: "Beds", href: "/dashboard/beds", icon: "bed" },
      { label: "Alerts", href: "/dashboard/alerts", icon: "circleAlert" },
      { label: "Notifications", href: "/dashboard/notifications", icon: "bell", badge: "unreadNotifications" },
    ],
  },
];

export default function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const { counts, facility, user, role, signOut } = useStaff();

  const isActive = (href: string) =>
    href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(href);

  return (
    <>
      {open && <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={onClose} aria-hidden />}
      <aside
        className={`no-print fixed left-0 top-0 z-50 flex h-screen w-64 flex-col text-white transition-transform duration-200 lg:z-30 lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}
        style={{ background: "#0b1220", borderRight: "1px solid #1c2740" }}
        aria-label="Main navigation"
      >
        <div className="px-5 pb-4 pt-5">
          <div className="flex items-center gap-2.5">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-lg"
              style={{ background: "var(--voxera-gold)", color: "#111827" }}
            >
              <Icon name="pulse" size={20} />
            </span>
            <div className="leading-tight">
              <p className="text-lg font-extrabold tracking-wide">VOXERA</p>
              <p className="text-[11px] uppercase tracking-[0.14em] text-slate-400">Hospital Command</p>
            </div>
          </div>
          {facility && (
            <div className="mt-4 rounded-lg px-3 py-2" style={{ background: "rgba(255,255,255,0.05)" }}>
              <p className="truncate text-sm font-semibold">{facility.name}</p>
              <p className="truncate text-xs text-slate-400">
                {[facility.location, facility.district].filter(Boolean).join(" · ") || facility.type}
              </p>
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-5 overflow-y-auto px-3 pb-4">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <p className="px-3 pb-1.5 text-[11px] font-bold uppercase tracking-[0.12em] text-slate-500">{g.title}</p>
              <div className="space-y-0.5">
                {g.items.map((it) => {
                  const n = it.badge ? counts[it.badge] : 0;
                  const critical = it.badge === "emergencies" && n > 0;
                  return (
                    <Link
                      key={it.href}
                      href={it.href}
                      onClick={onClose}
                      className="nav-link"
                      aria-current={isActive(it.href) ? "page" : undefined}
                    >
                      <Icon name={it.icon} />
                      <span>{it.label}</span>
                      {n > 0 && (
                        <span
                          className={`nav-badge ${
                            critical ? "nav-badge-critical" : it.badge === "pendingReferrals" ? "nav-badge-warning" : ""
                          }`}
                        >
                          {n}
                        </span>
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t px-4 py-3" style={{ borderColor: "#1c2740" }}>
          <p className="truncate text-sm font-semibold">{user?.email ?? "—"}</p>
          <p className="text-xs capitalize text-slate-400">{role ?? "staff"}</p>
          <button onClick={() => void signOut()} className="nav-link mt-2 w-full" style={{ padding: "0.5rem 0.6rem" }}>
            <Icon name="logout" /> <span>Sign out</span>
          </button>
        </div>
      </aside>
    </>
  );
}
