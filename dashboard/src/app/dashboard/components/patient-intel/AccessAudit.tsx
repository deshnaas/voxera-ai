"use client";

import type { PatientAccessLog } from "@/lib/patientTypes";
import { fmtDateTime } from "@/lib/format";
import { Badge, EmptyState } from "../ui";

const ACTION: Record<string, string> = {
  open_record: "Opened record", search: "Appeared in search", ask_question: "Asked about record",
  upload: "Uploaded prescription", verify: "Verified medication", delete: "Deleted prescription / medicine", denied: "Access refused",
};

export default function AccessAudit({ logs }: { logs: PatientAccessLog[] }) {
  if (logs.length === 0) {
    return <div className="card"><EmptyState icon="🔒" title="No access recorded yet" hint="Every time this record is opened, by whom, from which facility and why, is listed here." /></div>;
  }
  return (
    <div className="card overflow-x-auto">
      <table className="table">
        <thead><tr><th>When</th><th>Who</th><th>Facility</th><th>Action</th><th>Reason</th></tr></thead>
        <tbody>
          {logs.map((l) => (
            <tr key={l.id}>
              <td className="whitespace-nowrap">{fmtDateTime(l.accessed_at)}</td>
              <td>{l.source === "call" ? "Patient (Voxera call)" : `${l.role ?? "staff"}${l.accessed_by_user_id ? ` · ${l.accessed_by_user_id.slice(0, 8)}` : ""}`}</td>
              <td>
                {l.facility_name ?? "—"}{" "}
                {l.relationship === "external" && <Badge tone="warning">External</Badge>}
              </td>
              <td>{ACTION[l.action ?? ""] ?? l.action ?? "—"}{l.granted === false && <> <Badge tone="critical">refused</Badge></>}</td>
              <td className="text-muted">{l.access_reason || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
