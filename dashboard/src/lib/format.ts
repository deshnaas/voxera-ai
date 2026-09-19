// Small formatting helpers shared by every page.

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

/** "12 min" / "1 h 05 min" — how long something has been waiting. */
export function waiting(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  if (m < 1) return "< 1 min";
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${String(m % 60).padStart(2, "0")} min`;
  return `${Math.floor(h / 24)} d`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso));
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value);
  if (isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(d);
}

export function fmtTime(value: string | null | undefined): string {
  if (!value) return "—";
  const [h, m] = value.split(":");
  const d = new Date();
  d.setHours(Number(h), Number(m ?? 0), 0, 0);
  return new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit" }).format(d);
}

export function initials(name: string | null | undefined): string {
  const parts = (name ?? "?").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export function ageFromDob(dob: string | null | undefined): number | null {
  if (!dob) return null;
  const d = new Date(dob);
  if (isNaN(d.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - d.getFullYear();
  const m = now.getMonth() - d.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < d.getDate())) age--;
  return age >= 0 && age < 130 ? age : null;
}

export function humanDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** lower = more urgent. */
export function urgencyRank(u: string | null | undefined): number {
  switch ((u ?? "").toLowerCase()) {
    case "emergency": case "critical": return 0;
    case "high": case "urgent": return 1;
    case "medium": return 2;
    default: return 3;
  }
}

export function urgencyBadge(u: string | null | undefined): string {
  switch ((u ?? "").toLowerCase()) {
    case "emergency": case "critical": return "badge badge-critical";
    case "high": case "urgent": return "badge badge-warning";
    case "medium": return "badge badge-info";
    default: return "badge";
  }
}

export function statusBadge(s: string | null | undefined): string {
  switch ((s ?? "").toLowerCase()) {
    case "accepted": case "completed": case "confirmed": case "active": return "badge badge-success";
    case "pending": case "scheduled": case "rescheduled": return "badge badge-warning";
    case "rejected": case "cancelled": case "missed": case "stopped": return "badge badge-critical";
    case "in_progress": return "badge badge-info";
    default: return "badge";
  }
}

export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
