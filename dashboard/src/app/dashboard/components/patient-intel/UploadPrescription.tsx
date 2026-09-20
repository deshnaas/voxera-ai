"use client";

import { useRef, useState } from "react";
import { PatientApiError, uploadPrescription } from "@/lib/patientApi";
import type { UploadResult } from "@/lib/patientTypes";
import { Callout } from "../ui";
import Icon from "../Icon";

const MAX = 12 * 1024 * 1024;

export default function UploadPrescription({ patientRef, onUploaded }: { patientRef: string; onUploaded: (r: UploadResult) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "success" | "warning" | "critical"; text: string } | null>(null);

  async function pick(file: File | undefined) {
    if (!file) return;
    setMsg(null);
    if (!/\.(pdf|jpe?g|png)$/i.test(file.name)) { setMsg({ tone: "critical", text: "Upload a PDF, JPG or PNG file." }); return; }
    if (file.size > MAX) { setMsg({ tone: "critical", text: "File is too large (max 12 MB)." }); return; }
    setBusy(true);
    try {
      const r = await uploadPrescription(patientRef, file);
      setMsg({
        tone: r.status === "staged" ? "success" : "warning",
        text: r.message + (r.warning ? ` ${r.warning}` : ""),
      });
      onUploaded(r);
    } catch (e) {
      setMsg({ tone: "critical", text: e instanceof PatientApiError ? (e.offline ? "The patient-records service is not running, so documents can't be read right now." : e.message) : "Upload failed." });
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-col items-start gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="font-bold">Upload prescription</p>
          <p className="text-muted text-sm">PDF, JPG or PNG. Medicines are read automatically, then wait for a doctor to confirm.</p>
        </div>
        <button className="btn btn-primary" disabled={busy} onClick={() => input.current?.click()}>
          <Icon name="plus" size={16} /> {busy ? "Reading document…" : "Choose file"}
        </button>
        <input ref={input} type="file" hidden accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/png,image/jpeg"
               onChange={(e) => void pick(e.target.files?.[0])} />
      </div>
      {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}
    </div>
  );
}
