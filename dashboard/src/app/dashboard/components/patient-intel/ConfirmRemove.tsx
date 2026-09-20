"use client";

import { useState } from "react";
import { PatientApiError } from "@/lib/patientApi";
import { Callout, Modal } from "../ui";

/** "Are you sure?" dialog for removing a wrongly entered prescription or medicine. */
export default function ConfirmRemove({
  open, title, children, confirmLabel = "Delete", onClose, onConfirm,
}: {
  open: boolean;
  title: string;
  children: React.ReactNode;
  confirmLabel?: string;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function go() {
    setBusy(true); setError("");
    try {
      await onConfirm();
      onClose();
    } catch (e) {
      setError(e instanceof PatientApiError ? (e.offline ? "The patient-records service isn't running." : e.message) : "Could not delete.");
    } finally { setBusy(false); }
  }

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} title={title} width={480}
      footer={<>
        <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button className="btn btn-danger" disabled={busy} onClick={() => void go()}>{busy ? "Deleting…" : confirmLabel}</button>
      </>}>
      <div className="space-y-3 text-sm">
        {children}
        {error && <Callout tone="critical">{error}</Callout>}
      </div>
    </Modal>
  );
}
