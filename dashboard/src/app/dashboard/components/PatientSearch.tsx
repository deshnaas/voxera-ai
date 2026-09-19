"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { supabase } from "@/lib/supabase";
import { Avatar } from "./ui";
import Icon from "./Icon";

type Hit = { id: string; full_name: string; phone: string | null };

/** Global patient search: name or phone. Press "/" anywhere to focus. */
export default function PatientSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      const typing =
        el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    const onClick = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, []);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setHits([]);
      return;
    }
    setBusy(true);
    const t = setTimeout(async () => {
      const safe = term.replace(/[%,()]/g, " ");
      const { data } = await supabase
        .from("patients")
        .select("id, full_name, phone")
        .or(`full_name.ilike.%${safe}%,phone.ilike.%${safe}%`)
        .order("full_name")
        .limit(6);
      setHits((data ?? []) as Hit[]);
      setIdx(0);
      setBusy(false);
    }, 220);
    return () => clearTimeout(t);
  }, [q]);

  function go(h: Hit) {
    setOpen(false);
    setQ("");
    router.push(`/dashboard/patients/${h.id}`);
  }

  return (
    <div ref={boxRef} className="relative w-full max-w-md">
      <div className="text-faint pointer-events-none absolute left-3 top-1/2 -translate-y-1/2">
        <Icon name="search" size={16} />
      </div>
      <input
        ref={inputRef}
        value={q}
        type="search"
        role="combobox"
        aria-expanded={open}
        aria-label="Search patients"
        placeholder="Search patient by name or phone   ( / )"
        className="input"
        style={{ paddingLeft: 36, minHeight: 40 }}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setIdx((i) => Math.min(i + 1, hits.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setIdx((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter" && hits[idx]) {
            e.preventDefault();
            go(hits[idx]);
          } else if (e.key === "Escape") {
            setOpen(false);
            inputRef.current?.blur();
          }
        }}
      />
      {open && q.trim().length >= 2 && (
        <div
          className="card absolute left-0 right-0 top-full z-[60] mt-1.5 max-h-80 overflow-y-auto p-1"
          style={{ boxShadow: "var(--shadow-md)" }}
          role="listbox"
        >
          {busy && hits.length === 0 ? (
            <p className="text-muted px-3 py-3 text-sm">Searching…</p>
          ) : hits.length === 0 ? (
            <p className="text-muted px-3 py-3 text-sm">No patient found for “{q.trim()}”.</p>
          ) : (
            hits.map((h, i) => (
              <Link
                key={h.id}
                href={`/dashboard/patients/${h.id}`}
                role="option"
                aria-selected={i === idx}
                onClick={() => {
                  setOpen(false);
                  setQ("");
                }}
                className="flex items-center gap-3 rounded-lg px-3 py-2"
                style={{ background: i === idx ? "var(--dashboard-surface-muted)" : undefined }}
              >
                <Avatar name={h.full_name} size={32} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold">{h.full_name}</span>
                  <span className="text-muted block truncate text-xs">{h.phone ?? "no phone"}</span>
                </span>
              </Link>
            ))
          )}
        </div>
      )}
    </div>
  );
}
