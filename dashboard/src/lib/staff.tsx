"use client";

// ============================================================
// StaffProvider
// ------------------------------------------------------------
// One place that:
//   * resolves the logged-in hospital user -> facility (once, not on every page)
//   * keeps live counts (active emergencies, pending referrals, unread
//     notifications, live calls) fresh via Supabase realtime + 20 s polling
//   * exposes `tick` — pages re-fetch silently whenever it changes
//   * plays an audible alert when a new emergency / referral arrives
// ============================================================

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { useRouter } from "next/navigation";
import type { User } from "@supabase/supabase-js";
import { supabase } from "@/lib/supabase";

export type Facility = {
  id: string;
  name: string;
  type: string | null;
  location: string | null;
  district: string | null;
};

export type Counts = {
  emergencies: number;
  pendingReferrals: number;
  unreadNotifications: number;
  liveCalls: number;
};

export type IncomingNotification = {
  id: string;
  referral_id: string | null;
  target_facility_id: string;
  type: string;
  message: string;
  is_read: boolean;
  created_at: string;
};

type StaffContext = {
  loading: boolean;
  error: string;
  user: User | null;
  role: string | null;
  facilityId: string | null;
  facility: Facility | null;
  counts: Counts;
  tick: number;
  realtime: boolean;
  lastUpdated: Date | null;
  refresh: () => void;
  incoming: IncomingNotification | null;
  dismissIncoming: () => void;
  soundOn: boolean;
  setSoundOn: (v: boolean) => void;
  testSound: () => void;
  signOut: () => Promise<void>;
};

const Ctx = createContext<StaffContext | null>(null);

export function useStaff(): StaffContext {
  const v = useContext(Ctx);
  if (!v) throw new Error("useStaff must be used inside <StaffProvider>");
  return v;
}

const POLL_MS = 20_000;
const EMPTY: Counts = { emergencies: 0, pendingReferrals: 0, unreadNotifications: 0, liveCalls: 0 };

export function StaffProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [facilityId, setFacilityId] = useState<string | null>(null);
  const [facility, setFacility] = useState<Facility | null>(null);
  const [counts, setCounts] = useState<Counts>(EMPTY);
  const [tick, setTick] = useState(0);
  const [realtime, setRealtime] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [incoming, setIncoming] = useState<IncomingNotification | null>(null);
  const [soundOn, setSoundOnState] = useState(true);

  const audioRef = useRef<AudioContext | null>(null);
  const lastBeepRef = useRef(0);
  const soundRef = useRef(true);

  // ---------- sound ----------
  useEffect(() => {
    try {
      const saved = localStorage.getItem("voxera-sound");
      const on = saved !== "off";
      setSoundOnState(on);
      soundRef.current = on;
    } catch { /* ignore */ }
  }, []);

  const setSoundOn = useCallback((v: boolean) => {
    setSoundOnState(v);
    soundRef.current = v;
    try { localStorage.setItem("voxera-sound", v ? "on" : "off"); } catch { /* ignore */ }
  }, []);

  const beep = useCallback((kind: "emergency" | "info", force = false) => {
    if (!force && !soundRef.current) return;
    const now = Date.now();
    if (!force && now - lastBeepRef.current < 3000) return; // de-dupe bursts
    lastBeepRef.current = now;
    try {
      const AC: typeof AudioContext | undefined =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AC) return;
      const ctx = audioRef.current ?? new AC();
      audioRef.current = ctx;
      if (ctx.state === "suspended") void ctx.resume();
      const tones = kind === "emergency" ? [880, 660, 880, 660] : [740];
      tones.forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        const t0 = ctx.currentTime + i * 0.22;
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(0.25, t0 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.2);
        osc.connect(gain).connect(ctx.destination);
        osc.start(t0);
        osc.stop(t0 + 0.22);
      });
    } catch { /* audio is best-effort */ }
  }, []);

  const testSound = useCallback(() => beep("emergency", true), [beep]);

  // ---------- identity ----------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data: { user: u }, error: uErr } = await supabase.auth.getUser();
      if (cancelled) return;
      if (uErr || !u) {
        router.replace("/");
        return;
      }
      setUser(u);

      const { data: hu, error: huErr } = await supabase
        .from("hospital_users")
        .select("facility_id, role")
        .eq("user_id", u.id)
        .single();
      if (cancelled) return;
      if (huErr || !hu) {
        setError("This login is not linked to a hospital. Ask an administrator to add you to hospital_users.");
        setLoading(false);
        return;
      }
      setRole(hu.role ?? null);
      setFacilityId(hu.facility_id);

      const { data: f } = await supabase
        .from("facilities")
        .select("id, name, type, location, district")
        .eq("id", hu.facility_id)
        .single();
      if (cancelled) return;
      setFacility((f as Facility) ?? null);
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [router]);

  // ---------- live counts ----------
  const refresh = useCallback(async () => {
    if (!facilityId) return;
    const since = new Date(Date.now() - 20 * 60 * 1000).toISOString();
    const head = { count: "exact" as const, head: true };
    const [em, rf, nt, lc] = await Promise.all([
      supabase.from("emergency_cases").select("id", head)
        .eq("facility_id", facilityId).eq("status", "active"),
      supabase.from("referrals").select("id", head)
        .eq("receiving_facility_id", facilityId).eq("status", "pending"),
      supabase.from("referral_notifications").select("id", head)
        .eq("target_facility_id", facilityId).eq("is_read", false),
      supabase.from("calls").select("id", head)
        .eq("status", "in_progress").gte("created_at", since)
        .or(`facility_id.eq.${facilityId},facility_id.is.null`),
    ]);
    setCounts({
      emergencies: em.count ?? 0,
      pendingReferrals: rf.count ?? 0,
      unreadNotifications: nt.count ?? 0,
      liveCalls: lc.count ?? 0,
    });
    setLastUpdated(new Date());
    setTick((t) => t + 1);
  }, [facilityId]);

  useEffect(() => {
    if (!facilityId) return;
    void refresh();
    const id = setInterval(() => { void refresh(); }, POLL_MS);
    return () => clearInterval(id);
  }, [facilityId, refresh]);

  // ---------- realtime ----------
  useEffect(() => {
    if (!facilityId) return;
    const channel = supabase.channel(`voxera-live-${facilityId}`);

    const onChange = () => { void refresh(); };

    channel
      .on("postgres_changes",
        { event: "INSERT", schema: "public", table: "emergency_cases", filter: `facility_id=eq.${facilityId}` },
        () => { beep("emergency"); onChange(); })
      .on("postgres_changes",
        { event: "UPDATE", schema: "public", table: "emergency_cases", filter: `facility_id=eq.${facilityId}` },
        onChange)
      .on("postgres_changes",
        { event: "*", schema: "public", table: "referrals", filter: `receiving_facility_id=eq.${facilityId}` },
        onChange)
      .on("postgres_changes",
        { event: "INSERT", schema: "public", table: "referral_notifications", filter: `target_facility_id=eq.${facilityId}` },
        (payload) => {
          setIncoming(payload.new as IncomingNotification);
          beep("emergency");
          onChange();
        })
      .on("postgres_changes", { event: "*", schema: "public", table: "calls" }, onChange)
      .on("postgres_changes", { event: "*", schema: "public", table: "appointments", filter: `facility_id=eq.${facilityId}` }, onChange)
      .subscribe((status) => setRealtime(status === "SUBSCRIBED"));

    return () => { void supabase.removeChannel(channel); };
  }, [facilityId, refresh, beep]);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    router.replace("/");
  }, [router]);

  const value = useMemo<StaffContext>(() => ({
    loading, error, user, role, facilityId, facility, counts, tick, realtime, lastUpdated,
    refresh: () => { void refresh(); },
    incoming, dismissIncoming: () => setIncoming(null),
    soundOn, setSoundOn, testSound, signOut,
  }), [loading, error, user, role, facilityId, facility, counts, tick, realtime,
       lastUpdated, refresh, incoming, soundOn, setSoundOn, testSound, signOut]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
