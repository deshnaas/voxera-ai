"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { supabase } from "@/lib/supabase";
import { PatientApiError, searchPatients } from "@/lib/patientApi";
import type { AccessReason } from "@/lib/patientTypes";
import { Avatar, Badge } from "./ui";
import Icon from "./Icon";
import ReasonDialog from "./patient-intel/ReasonDialog";

type Hit = {
  id: string; patient_id: string | null; full_name: string; phone?: string | null;
  external: boolean; sub: string;
};

/** Global patient search (name, phone or patient ID). Press "/" anywhere to focus.
 *  Uses the authorised API (cross-hospital, audited); falls back to this hospital's patients if it's offline. */
export default function PatientSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [idx, setIdx] = useState(0);
  const [offline, setOffline] = useState(false);
  const [pending, setPending] = useState<Hit | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
      if (e.key === "/" && !typing) { e.preventDefault(); inputRef.current?.focus(); }
    };
    const onClick = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("mousedown", onClick); };
  }, []);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits([]); return; }
    setBusy(true);
    const t = setTimeout(async () => {                      // debounce 300 ms
      try {
        const { results } = await searchPatients(term);
        setOffline(false);
        setHits(results.map((r) => ({
          id: r.id, patient_id: r.patient_id, full_name: r.full_name, external: r.relationship === "external",
          sub: [r.patient_id, r.age !== null && r.age !== undefined ? `${r.age} yrs` : null, r.gender].filter(Boolean).join(" · "),
        })));
      } catch (e) {
        if (!(e instanceof PatientApiError) || !e.offline) { setHits([]); setBusy(false); return; }
        setOffline(true);                                    // fall back: RLS-scoped, this hospital only
        const safe = term.replace(/[%,()]/g, " ");
        const { data } = await supabase.from("patients").select("id, full_name, phone")
          .or(`full_name.ilike.%${safe}%,phone.ilike.%${safe}%`).order("full_name").limit(6);
        setHits((data ?? []).map((p) => ({ id: p.id as string, patient_id: null, full_name: p.full_name as string,
          phone: p.phone as string | null, external: false, sub: (p.phone as string | null) ?? "no phone" })));
      }
      setIdx(0);
      setBusy(false);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  function reset() { setOpen(false); setQ(""); }

  function go(h: Hit) {
    if (h.external) { setOpen(false); setPending(h); return; }
    reset();
    router.push(`/dashboard/patients/${h.id}`);
  }

  function confirmReason(reason: AccessReason, text: string) {
    if (!pending?.patient_id) return;
    try { sessionStorage.setItem(`voxera-access-reason:${pending.patient_id}`, JSON.stringify({ reason, text })); } catch { /* ignore */ }
    const target = `/dashboard/patients/${encodeURIComponent(pending.patient_id)}/external`;
    setPending(null); setQ("");
    router.push(target);
  }

  return (
    <div ref={boxRef} className="relative w-full max-w-md">
      <div className="text-faint pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"><Icon name="search" size={16} /></div>
      <input
        ref={inputRef} value={q} type="search" role="combobox" aria-expanded={open} aria-label="Search patients"
        placeholder="Search by Patient ID, name or phone   ( / )" className="input"
        style={{ paddingLeft: 36, minHeight: 40 }}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
          else if (e.key === "Enter" && hits[idx]) { e.preventDefault(); go(hits[idx]); }
          else if (e.key === "Escape") { setOpen(false); inputRef.current?.blur(); }
        }}
      />
      {open && q.trim().length >= 2 && (
        <div className="card absolute left-0 right-0 top-full z-[60] mt-1.5 max-h-80 overflow-y-auto p-1" style={{ boxShadow: "var(--shadow-md)" }} role="listbox">
          {busy && hits.length === 0 ? <p className="text-muted px-3 py-3 text-sm">Searching…</p>
            : hits.length === 0 ? <p className="text-muted px-3 py-3 text-sm">No patient found for “{q.trim()}”.</p>
            : hits.map((h, i) => (
              <button key={h.id} type="button" role="option" aria-selected={i === idx} onClick={() => go(h)}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left"
                style={{ background: i === idx ? "var(--dashboard-surface-muted)" : undefined }}>
                <Avatar name={h.full_name} size={32} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{h.full_name}</span>
                  <span className="text-muted block truncate text-xs">{h.sub}</span>
                </span>
                {h.external && <Badge tone="warning">Other facility</Badge>}
              </button>
            ))}
          {offline && <p className="text-faint border-t px-3 py-2 text-xs" style={{ borderColor: "var(--dashboard-border)" }}>Records service offline — showing this hospital only.</p>}
          <button type="button" className="text-muted w-full px-3 py-2 text-left text-xs font-semibold hover:underline"
                  onClick={() => { const t = q.trim(); reset(); router.push(`/dashboard/patients/search?q=${encodeURIComponent(t)}`); }}>
            Open full search →
          </button>
        </div>
      )}
      <ReasonDialog open={!!pending} onClose={() => setPending(null)} onConfirm={confirmReason}
                    patientLabel={pending ? `${pending.full_name} (${pending.patient_id})` : ""} />
    </div>
  );
}
