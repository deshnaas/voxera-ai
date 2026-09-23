"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Icon from "./Icon";
import PatientSearch from "./PatientSearch";
import { useStaff } from "@/lib/staff";

export default function Topbar({
  theme,
  onToggleTheme,
  onMenu,
}: {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onMenu: () => void;
}) {
  const { facility, counts, realtime, lastUpdated, refresh, soundOn, setSoundOn, testSound } = useStaff();
  const [, force] = useState(0);
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 15000);
    return () => clearInterval(t);
  }, []);

  const secs = lastUpdated ? Math.max(0, Math.floor((Date.now() - lastUpdated.getTime()) / 1000)) : null;

  return (
    <header
      className="no-print sticky top-0 z-20 flex items-center gap-3 border-b px-4 py-2.5 sm:px-6"
      style={{
        background: "color-mix(in srgb, var(--dashboard-surface) 92%, transparent)",
        backdropFilter: "blur(8px)",
        borderColor: "var(--dashboard-border)",
      }}
    >
      <div className="lg:hidden">   {/* the sidebar is always visible on desktop; this menu button is for phones/tablets */}
        <button className="btn btn-ghost" onClick={onMenu} aria-label="Open menu">
          <Icon name="menu" size={20} />
        </button>
      </div>

      <PatientSearch />

      <div className="ml-auto flex items-center gap-2">
        {facility && facility.verified === false && (
          <span
            className="hidden rounded-full px-2.5 py-1 text-xs font-semibold sm:inline-flex"
            style={{ background: "color-mix(in srgb, #f59e0b 18%, transparent)", color: "#b45309" }}
            title="A Voxera operator hasn't verified this hospital's registration yet. Everything works normally in the meantime."
          >
            Pending verification
          </span>
        )}
        {counts.emergencies > 0 && (
          <Link href="/dashboard/emergency" className="btn btn-danger btn-sm">
            <span className="dot dot-critical" style={{ background: "#fff" }} />
            {counts.emergencies} ACTIVE EMERGENC{counts.emergencies === 1 ? "Y" : "IES"}
          </Link>
        )}
        {counts.pendingReferrals > 0 && (
          <Link href="/dashboard/referrals" className="btn btn-sm hidden sm:inline-flex">
            {counts.pendingReferrals} pending referral{counts.pendingReferrals === 1 ? "" : "s"}
          </Link>
        )}

        <button
          className="btn btn-ghost btn-sm hidden md:inline-flex"
          onClick={refresh}
          title={
            realtime
              ? "Live: updates arrive instantly. Click to refresh now."
              : "Auto-refreshing every 20 s. Click to refresh now."
          }
        >
          <span className={`dot ${realtime ? "dot-live" : ""}`} />
          <span className="text-xs">
            {realtime ? "Live" : "Auto"}
            {secs !== null ? ` · ${secs < 5 ? "now" : secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m`}` : ""}
          </span>
        </button>

        <button
          className="btn btn-ghost btn-sm"
          aria-pressed={soundOn}
          onClick={() => {
            const next = !soundOn;
            setSoundOn(next);
            if (next) testSound();
          }}
          title={soundOn ? "Alert sound on (click to mute)" : "Alert sound muted (click to enable)"}
        >
          <Icon name={soundOn ? "volume" : "volumeOff"} />
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onToggleTheme} title="Toggle light / dark" aria-label="Toggle theme">
          <Icon name={theme === "dark" ? "sun" : "moon"} />
        </button>
      </div>
    </header>
  );
}
