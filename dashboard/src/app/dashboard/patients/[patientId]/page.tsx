"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useStaff } from "@/lib/staff";
import {
  getCallsForPatient, getPrescriptions, getReportedProfile,
  type Patient, type Prescription, type VoxeraCall,
} from "@/lib/voxera";
import { ageFromDob, fmtDate, fmtDateTime, fmtTime, statusBadge, urgencyBadge } from "@/lib/format";
import { Avatar, Badge, Callout, EmptyState, InfoItem, LoadingRows, Page, SectionCard, Tabs } from "../../components/ui";
import CallHistory from "../../components/CallHistory";
import PrescriptionsPanel from "../../components/PrescriptionsPanel";
import EditPatientModal from "../../components/EditPatientModal";
import Icon from "../../components/Icon";

type Referral = {
  id: string; reason: string; urgency: string; status: string; required_service: string | null;
  ai_summary: string | null; ai_recommendation: string | null; recommended_department: string | null; created_at: string;
};
type Appointment = {
  id: string; appointment_date: string; appointment_time: string | null; department: string | null;
  doctor_name: string | null; reason: string | null; status: string;
};
type Reported = Awaited<ReturnType<typeof getReportedProfile>>;
type Tab = "overview" | "calls" | "rx" | "referrals" | "appointments";

const splitList = (s: string | null | undefined) =>
  (s ?? "").split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);

export default function PatientPage() {
  const { patientId } = useParams<{ patientId: string }>();
  const { facilityId, tick } = useStaff();

  const [patient, setPatient] = useState<Patient | null>(null);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [calls, setCalls] = useState<VoxeraCall[]>([]);
  const [rx, setRx] = useState<{ rows: Prescription[]; tableMissing: boolean }>({ rows: [], tableMissing: false });
  const [reported, setReported] = useState<Reported | null>(null);
  const [activeEmergency, setActiveEmergency] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    if (!facilityId || !patientId) return;
    const { data: p, error: pErr } = await supabase.from("patients").select("*").eq("id", patientId).maybeSingle();
    if (pErr || !p) { setError(pErr?.message ?? "Patient could not be found."); setLoading(false); return; }
    setError("");
    setPatient(p as Patient);

    const [rf, ap, cl, rxr, em] = await Promise.all([
      supabase.from("referrals")
        .select("id, reason, urgency, status, required_service, ai_summary, ai_recommendation, recommended_department, created_at")
        .eq("patient_id", patientId).eq("receiving_facility_id", facilityId).order("created_at", { ascending: false }),
      supabase.from("appointments")
        .select("id, appointment_date, appointment_time, department, doctor_name, reason, status")
        .eq("patient_id", patientId).eq("facility_id", facilityId).order("appointment_date", { ascending: false }),
      getCallsForPatient(patientId),
      getPrescriptions(patientId),
      supabase.from("emergency_cases").select("id", { count: "exact", head: true })
        .eq("patient_id", patientId).eq("facility_id", facilityId).eq("status", "active"),
    ]);
    setReferrals((rf.data ?? []) as Referral[]);
    setAppointments((ap.data ?? []) as Appointment[]);
    setCalls(cl);
    setRx(rxr);
    setActiveEmergency((em.count ?? 0) > 0);
    setLoading(false);
    setReported(await getReportedProfile(cl.map((c) => c.id)));
  }, [facilityId, patientId]);

  useEffect(() => { void load(); }, [load, tick]);

  if (loading) return <Page><LoadingRows rows={4} /></Page>;
  if (error || !patient) {
    return (
      <Page>
        <Callout tone="critical">{error || "Patient could not be found."}</Callout>
        <Link href="/dashboard/patients" className="btn mt-4">← Back to patients</Link>
      </Page>
    );
  }

  const age = ageFromDob(patient.date_of_birth) ?? reported?.age ?? null;
  const clinicalKnown = "allergies" in patient;
  const allergyList = [...new Set([...splitList(patient.allergies), ...(reported?.allergies ?? [])])];
  const conditionList = [...new Set([...splitList(patient.chronic_conditions), ...(reported?.conditions ?? [])])];
  const activeRx = rx.rows.filter((r) => r.status === "active");

  return (
    <Page>
      <Link href="/dashboard/patients" className="text-muted inline-flex items-center gap-1 text-sm font-semibold hover:underline">← Patients</Link>

      {/* Header ---------------------------------------------------- */}
      <section className={`card mt-3 p-5 ${activeEmergency ? "triage triage-critical" : ""}`}>
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex min-w-0 items-center gap-4">
            <Avatar name={patient.full_name} size={64} tone={activeEmergency ? "critical" : undefined} />
            <div className="min-w-0">
              <p className="eyebrow">Patient record</p>
              <h1 className="truncate text-2xl font-extrabold sm:text-3xl">{patient.full_name}</h1>
              <p className="text-muted mt-1 text-sm">
                {[age !== null && `${age} yrs`, patient.gender, patient.blood_group && `Blood ${patient.blood_group}`, patient.village_or_locality, patient.district].filter(Boolean).join(" · ") || "No demographic details yet"}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {patient.phone && <a className="btn btn-lg" href={`tel:${patient.phone}`}><Icon name="phone" size={16} /> {patient.phone}</a>}
            <button className="btn btn-lg" onClick={() => setEditing(true)}><Icon name="edit" size={16} /> Edit details</button>
            <button className="btn btn-primary btn-lg" onClick={() => setTab("rx")}><Icon name="pill" size={16} /> Prescribe</button>
          </div>
        </div>

        {(activeEmergency || allergyList.length > 0 || reported?.pregnant) && (
          <div className="mt-4 space-y-2">
            {activeEmergency && <Callout tone="critical"><b>Active emergency</b> for this patient. <Link href="/dashboard/emergency" className="font-bold underline">Open emergency board</Link></Callout>}
            {allergyList.length > 0 && <Callout tone="warning"><b>Allergies:</b> {allergyList.join(", ")}</Callout>}
            {reported?.pregnant && <Callout tone="warning"><b>Patient reported being pregnant</b> (told Voxera, unverified)</Callout>}
          </div>
        )}
      </section>

      <div className="mt-5">
        <Tabs<Tab> value={tab} onChange={setTab} tabs={[
          { id: "overview", label: "Overview" },
          { id: "calls", label: `Calls & conversations (${calls.length})` },
          { id: "rx", label: `Prescriptions (${activeRx.length})` },
          { id: "referrals", label: `Referrals (${referrals.length})` },
          { id: "appointments", label: `Appointments (${appointments.length})` },
        ]} />
      </div>

      <div className="mt-5">
        {tab === "overview" && (
          <div className="grid gap-5 lg:grid-cols-3">
            <div className="space-y-5 lg:col-span-2">
              <SectionCard title="Patient details" actions={<button className="btn btn-sm" onClick={() => setEditing(true)}><Icon name="edit" size={14} /> Edit</button>}>
                <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                  <InfoItem label="Full name" value={patient.full_name} />
                  <InfoItem label="Phone" value={patient.phone} />
                  <InfoItem label="Date of birth" value={patient.date_of_birth ? `${fmtDate(patient.date_of_birth)}${age !== null ? ` (${age})` : ""}` : "—"} />
                  <InfoItem label="Gender" value={patient.gender} />
                  <InfoItem label="Language" value={patient.preferred_language} />
                  <InfoItem label="Locality" value={patient.village_or_locality} />
                  <InfoItem label="District" value={patient.district} />
                  {clinicalKnown && <>
                    <InfoItem label="Blood group" value={patient.blood_group} />
                    <InfoItem label="Emergency contact" value={patient.emergency_contact_name ? `${patient.emergency_contact_name}${patient.emergency_contact_phone ? ` · ${patient.emergency_contact_phone}` : ""}` : "—"} />
                    <InfoItem label="Allergies (verified)" value={patient.allergies} />
                    <InfoItem label="Chronic conditions (verified)" value={patient.chronic_conditions} />
                    <InfoItem label="Clinical notes" value={patient.clinical_notes} />
                  </>}
                </div>
                {!clinicalKnown && (
                  <Callout tone="info" className="mt-4">Clinical fields (allergies, blood group, emergency contact) need <code>sql/2026_dashboard_v2.sql</code> to be run once.</Callout>
                )}
              </SectionCard>

              <SectionCard title="Latest Voxera call" subtitle="Most recent conversation and structured summary">
                {calls.length === 0 ? <p className="text-muted text-sm">No Voxera calls yet.</p> : (
                  <div>
                    <p className="text-sm">{reported?.latest?.chief_concern ?? "Summary not available for the latest call."}</p>
                    <button className="btn btn-sm mt-3" onClick={() => setTab("calls")}>View conversation</button>
                  </div>
                )}
              </SectionCard>
            </div>

            <div className="space-y-5">
              <SectionCard title="Told Voxera" subtitle="Patient-reported across all calls — unverified">
                {!reported ? <LoadingRows rows={1} /> : (
                  <div className="space-y-3 text-sm">
                    <ListRow label="Allergies" items={reported.allergies} />
                    <ListRow label="Conditions" items={reported.conditions} />
                    <ListRow label="Medicines mentioned" items={reported.medications} />
                  </div>
                )}
              </SectionCard>
              <SectionCard title="Active prescriptions" actions={<button className="btn btn-sm" onClick={() => setTab("rx")}>Manage</button>}>
                {activeRx.length === 0 ? <p className="text-muted text-sm">None.</p> : (
                  <ul className="space-y-2 text-sm">
                    {activeRx.slice(0, 5).map((p) => (
                      <li key={p.id}><b>{p.medication_name}</b> <span className="text-muted">{[p.dosage, p.frequency].filter(Boolean).join(" · ")}</span></li>
                    ))}
                  </ul>
                )}
              </SectionCard>
            </div>
          </div>
        )}

        {tab === "calls" && <CallHistory patientId={patient.id} />}

        {tab === "rx" && (
          <PrescriptionsPanel
            patientId={patient.id} rows={rx.rows} tableMissing={rx.tableMissing} onChanged={() => void load()}
            allergies={allergyList} conditions={conditionList} pregnant={reported?.pregnant}
            referralId={referrals.find((r) => r.status === "accepted" || r.status === "pending")?.id ?? null}
          />
        )}

        {tab === "referrals" && (
          referrals.length === 0 ? <div className="card"><EmptyState title="No referrals to this hospital" /></div> : (
            <div className="space-y-4">
              {referrals.map((r) => (
                <article key={r.id} className="card p-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={statusBadge(r.status)}>{r.status}</span>
                    <span className={urgencyBadge(r.urgency)}>{r.urgency}</span>
                    <span className="text-muted ml-auto text-sm">{fmtDateTime(r.created_at)}</span>
                  </div>
                  <p className="mt-3 font-semibold">{r.reason}</p>
                  <div className="mt-3 grid gap-4 sm:grid-cols-2">
                    <InfoItem label="Required service" value={r.required_service} />
                    <InfoItem label="Recommended department" value={r.recommended_department} />
                  </div>
                  {r.ai_summary && <p className="text-muted mt-3 text-sm"><b>AI summary:</b> {r.ai_summary}</p>}
                  <Link href={`/dashboard/referrals/${r.id}`} className="btn btn-sm mt-4">Open referral</Link>
                </article>
              ))}
            </div>
          )
        )}

        {tab === "appointments" && (
          appointments.length === 0 ? <div className="card"><EmptyState title="No appointments" hint="Appointments scheduled for this patient appear here." /></div> : (
            <div className="card overflow-x-auto">
              <table className="table">
                <thead><tr><th>Date</th><th>Time</th><th>Department</th><th>Doctor</th><th>Status</th></tr></thead>
                <tbody>
                  {appointments.map((a) => (
                    <tr key={a.id}>
                      <td>{fmtDate(a.appointment_date)}</td>
                      <td>{fmtTime(a.appointment_time)}</td>
                      <td>{a.department ?? "—"}</td>
                      <td>{a.doctor_name ?? "—"}</td>
                      <td><span className={statusBadge(a.status)}>{a.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </div>

      {editing && (
        <EditPatientModal
          open={editing} onClose={() => setEditing(false)} patient={patient}
          reported={{ allergies: reported?.allergies ?? [], conditions: reported?.conditions ?? [] }}
          onSaved={() => void load()}
        />
      )}
    </Page>
  );
}

function ListRow({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      {items.length === 0 ? <p className="text-muted mt-1">None reported</p> : (
        <div className="mt-1 flex flex-wrap gap-1.5">{items.map((i) => <Badge key={i}>{i}</Badge>)}</div>
      )}
    </div>
  );
}
