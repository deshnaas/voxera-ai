"use client";

import { useState } from "react";
import { ACCESS_REASONS, type AccessReason } from "@/lib/patientTypes";
import { Callout, Modal } from "../ui";

/** Shown before opening a record from another facility. The reason is stored in the audit log. */
export default function ReasonDialog({
  open, onClose, onConfirm, patientLabel,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (reason: AccessReason, text: string) => void;
  patientLabel: string;
}) {
  const [reason, setReason] = useState<AccessReason | "">("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  function submit() {
    if (!reason) { setError("Choose a reason."); return; }
    if (reason === "other" && !text.trim()) { setError("Please explain the reason."); return; }
    setError("");
    onConfirm(reason, text.trim());
  }

  return (
    <Modal open={open} onClose={onClose} title="Accessing records from another facility" width={520}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={submit}>Open record</button>
      </>}>
      <div className="space-y-4">
        <Callout tone="warning">
          <b>{patientLabel}</b> is not a patient of your facility. You will see essential information only, and this
          access is recorded with your name, facility, role and reason.
        </Callout>
        {error && <Callout tone="critical">{error}</Callout>}
        <fieldset className="space-y-2">
          <legend className="field-label">Reason for access</legend>
          {ACCESS_REASONS.map((r) => (
            <label key={r.value} className="flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5"
                   style={{ borderColor: reason === r.value ? "var(--dashboard-gold-border)" : "var(--dashboard-border)" }}>
              <input type="radio" name="reason" checked={reason === r.value} onChange={() => setReason(r.value)} />
              <span className="text-sm font-semibold">{r.label}</span>
            </label>
          ))}
        </fieldset>
        {reason === "other" && (
          <textarea className="textarea" rows={2} value={text} onChange={(e) => setText(e.target.value)}
                    placeholder="Briefly explain why you need this record" maxLength={200} />
        )}
      </div>
    </Modal>
  );
}
