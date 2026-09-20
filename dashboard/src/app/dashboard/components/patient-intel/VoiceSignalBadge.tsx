"use client";

import type { VoiceSignal } from "@/lib/patientTypes";
import { Badge } from "../ui";

// Deliberately NOT a diagnosis: it never says panic, anxiety or instability, and it never drives an alert.
const LABEL: Record<VoiceSignal["arousal_level"], { text: string; tone: "success" | "info" | "warning" | "neutral" }> = {
  low: { text: "Normal", tone: "success" },
  moderate: { text: "Elevated", tone: "info" },
  high: { text: "High arousal", tone: "warning" },
  unavailable: { text: "Uncertain", tone: "neutral" },
};

export default function VoiceSignalBadge({ signal, detailed = false }: { signal?: VoiceSignal | null; detailed?: boolean }) {
  if (!signal) return null;
  const l = LABEL[signal.arousal_level] ?? LABEL.unavailable;
  const pct = Math.round((signal.confidence ?? 0) * 100);
  return (
    <span className="inline-flex flex-wrap items-center gap-2"
          title="A conversational signal from the caller's voice. Not a diagnostic indicator.">
      <span className="text-muted text-xs font-semibold">Voice signal</span>
      <Badge tone={l.tone}>{l.text}</Badge>
      {detailed && signal.arousal_level !== "unavailable" && (
        <span className="text-faint text-xs">
          {signal.arousal_level !== "low" && "Elevated vocal arousal signal · "}confidence {pct}% · not a diagnostic indicator
        </span>
      )}
    </span>
  );
}
