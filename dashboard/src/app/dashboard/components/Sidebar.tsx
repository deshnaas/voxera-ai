"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

type SidebarProps = {
  theme: "light" | "dark";
  onThemeChange: (next: "light" | "dark") => void;
};

const menuItems = [
  {
    label: "Overview",
    href: "/dashboard",
    icon: "▦",
  },
  {
    label: "Referrals",
    href: "/dashboard/referrals",
    icon: "↗",
  },
  {
    label: "Patients",
    href: "/dashboard/patients",
    icon: "♙",
  },
  {
    label: "Voxera Calls",
    href: "/dashboard/calls",
    icon: "☎",
  },
  {
    label: "Emergency",
    href: "/dashboard/emergency",
    icon: "⚠",
  },
  {
    label: "Appointments",
    href: "/dashboard/appointments",
    icon: "▣",
  },
  {
    label: "Follow-ups",
    href: "/dashboard/follow-ups",
    icon: "↻",
  },
  {
    label: "Beds",
    href: "/dashboard/beds",
    icon: "▤",
  },
  {
    label: "Alerts",
    href: "/dashboard/alerts",
    icon: "!",
  },
  {
    label: "Notifications",
    href: "/dashboard/notifications",
    icon: "♢",
  },
];

export default function Sidebar({
  theme,
  onThemeChange,
}: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const darkMode = theme === "dark";
  const onToggleTheme = () =>
    onThemeChange(darkMode ? "light" : "dark");

  async function handleLogout() {
    await supabase.auth.signOut();
    router.push("/");
  }

  function isActive(href: string) {
    if (href === "/dashboard") {
      return pathname === "/dashboard";
    }

    return pathname.startsWith(href);
  }

  return (
    <aside className="fixed left-0 top-0 z-40 flex h-screen w-64 flex-col border-r border-[#D4AF37] bg-black text-white">
      {/* Brand */}
      <div className="border-b border-[#D4AF37] px-6 py-6">
        <p className="text-xs font-semibold uppercase tracking-[0.3em] text-[#D4AF37]">
          Hospital System
        </p>

        <h1 className="mt-2 text-2xl font-bold tracking-wide">
          VOXERA
        </h1>

        <p className="mt-1 text-xs text-gray-400">
          Referral & Coordination
        </p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-5">
        <p className="px-3 pb-3 text-xs font-semibold uppercase tracking-widest text-gray-500">
          Navigation
        </p>

        <div className="space-y-1">
          {menuItems.map((item) => {
            const active = isActive(item.href);

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 rounded-lg border px-3 py-3 text-sm font-semibold transition-all duration-200 ${
                  active
                    ? "border-[#D4AF37] bg-[#D4AF37] text-black shadow-[0_0_15px_rgba(212,175,55,0.35)]"
                    : "border-transparent text-gray-300 hover:border-[#D4AF37] hover:text-white"
                }`}
              >
                <span className="flex h-6 w-6 items-center justify-center text-base">
                  {item.icon}
                </span>

                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Bottom Controls */}
      <div className="space-y-2 border-t border-[#D4AF37] p-3">
        <button
          type="button"
          onClick={onToggleTheme}
          className="flex w-full items-center gap-3 rounded-lg border border-gray-700 px-3 py-3 text-left text-sm font-semibold text-gray-300 transition-all hover:border-[#D4AF37] hover:text-white"
        >
          <span className="flex h-6 w-6 items-center justify-center">
            {darkMode ? "☀️" : "🌙"}
          </span>

          <span>
            {darkMode ? "Light Mode" : "Dark Mode"}
          </span>
        </button>

        <button
          type="button"
          onClick={handleLogout}
          className="flex w-full items-center gap-3 rounded-lg border border-gray-700 px-3 py-3 text-left text-sm font-semibold text-gray-300 transition-all hover:border-red-400 hover:text-red-300"
        >
          <span className="flex h-6 w-6 items-center justify-center">
            ↪
          </span>

          <span>Logout</span>
        </button>
      </div>
    </aside>
  );
}