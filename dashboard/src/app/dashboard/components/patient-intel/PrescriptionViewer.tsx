"use client";

import { useEffect, useState } from "react";
import { getDocument, PatientApiError } from "@/lib/patientApi";
import type { DocumentView } from "@/lib/patientTypes";
import { fmtDateTime } from "@/lib/format";
import { Badge, Callout, LoadingRows, Modal } from "../ui";

export default function PrescriptionViewer({ patientRef, docId, onClose }: { patientRef: string; docId: string | null; onClose: () => void }) {
  const [view, setView] = useState<DocumentView | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!docId) return;
    let live = true;
    setView(null); setError("");
    getDocument(patientRef, docId)
      .then((v) => { if (live) setView(v); })
      .catch((e) => { if (live) setError(e instanceof PatientApiError ? e.message : "Could not load the document."); });
    return () => { live = false; };
  }, [patientRef, docId]);

  const isPdf = view?.document.filename?.toLowerCase().endsWith(".pdf");
  const meta = view?.document.ocr_metadata;

  return (
    <Modal open={!!docId} onClose={onClose} title="Prescription document" width={900}>
      {error && <Callout tone="critical">{error}</Callout>}
      {!view && !error && <LoadingRows rows={2} />}
      {view && (
        <div className="grid gap-5 lg:grid-cols-2">
          <div>
            <p className="eyebrow mb-2">Original document</p>
            {view.url ? (isPdf
              ? <iframe title="Original prescription" src={view.url} className="h-96 w-full rounded-lg border" />
              // eslint-disable-next-line @next/next/no-img-element
              : <img alt="Original prescription" src={view.url} className="max-h-96 w-full rounded-lg border object-contain" />)
              : <Callout tone="warning">The original file is not available.</Callout>}
            <p className="text-faint mt-2 text-xs">
              {view.document.filename} · uploaded {fmtDateTime(view.document.created_at)} · read by {meta?.method ?? "OCR"}
              {typeof meta?.confidence === "number" ? ` (${Math.round(meta.confidence * 100)}% avg confidence)` : ""}
            </p>
          </div>
          <div className="space-y-4">
            <div>
              <p className="eyebrow mb-2">Extracted medicines</p>
              {view.items.length === 0 ? <p className="text-muted text-sm">None extracted.</p> : (
                <ul className="space-y-2">
                  {view.items.map((i) => (
                    <li key={i.id} className="rounded-lg border p-3 text-sm" style={{ borderColor: "var(--dashboard-border)" }}>
                      <div className="flex flex-wrap items-center gap-2">
                        <b>{i.medicine_name ?? "Unreadable"}</b>
                        <Badge tone={i.verification_status === "confirmed" ? "success" : i.verification_status === "rejected" ? "critical" : "warning"}>
                          {i.verification_status.replace("_", " ")}
                        </Badge>
                        {i.confidence !== null && <span className="text-faint text-xs">{Math.round(i.confidence * 100)}%</span>}
                      </div>
                      <p className="text-muted">{[i.strength, i.route, i.frequency, i.duration].filter(Boolean).join(" · ") || "no dose details found"}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <p className="eyebrow mb-2">Extracted text</p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg p-3 text-xs" style={{ background: "var(--dashboard-surface-muted)" }}>
                {view.document.extracted_text ?? "No text could be read."}
              </pre>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
