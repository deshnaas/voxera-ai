"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useStaff } from "@/lib/staff";
import {
  getCall, getCallSummary, getConversation, getPatientsMap, humanDuration,
  type CallSummary, type ConversationTurn, type PatientLite, type VoxeraCall,
} from "@/lib/voxera";
import { fmtDateTime } from "@/lib/format";
import { Callout, InfoItem, LoadingRows, Page, PageHeader, SectionCard } from "../../components/ui";
import { CallStatusBadges, SummaryCard, Transcript } from "../../components/CallHistory";

export default function CallDetailPage() {
  const { callId } = useParams<{ callId: string }>();
  const { tick } = useStaff();
  const [call, setCall] = useState<VoxeraCall | null>(null);
  const [patient, setPatient] = useState<PatientLite | null>(null);
  const [turns, setTurns] = useState<ConversationTurn[]>([]);
  const [summary, setSummary] = useState<CallSummary | null>(null);
  const [summaryText, setSummaryText] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const c = await getCall(callId);
    setCall(c);
    if (c) {
      const t = await getConversation(callId);
      setTurns(t);
      const s = await getCallSummary(callId, t);
      setSummary(s.summary);
      setSummaryText(s.text);
      if (c.patient_id) setPatient((await getPatientsMap([c.patient_id]))[c.patient_id] ?? null);
    }
    setLoaded(true);
  }, [callId]);

  // re-fetch on every tick so a live call's transcript keeps filling in
  useEffect(() => { void load(); }, [load, tick]);

  if (!loaded) return <Page><LoadingRows rows={4} /></Page>;
  if (!call) return (
    <Page>
      <Callout tone="critical">Call not found.</Callout>
      <Link href="/dashboard/calls" className="btn mt-4">← Back to calls</Link>
    </Page>
  );

  return (
    <Page>
      <Link href="/dashboard/calls" className="text-muted text-sm font-semibold hover:underline">← Calls</Link>
      <div className="mt-3">
        <PageHeader
          eyebrow="Voxera call"
          title={patient?.full_name ?? "Unknown patient"}
          subtitle={fmtDateTime(call.created_at)}
          actions={<>
            <CallStatusBadges call={call} />
            {call.patient_id && <Link href={`/dashboard/patients/${call.patient_id}`} className="btn">Patient record</Link>}
            {patient?.phone && <a href={`tel:${patient.phone}`} className="btn">Call {patient.phone}</a>}
          </>}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          {summary && <SummaryCard summary={summary} text={summaryText} />}
          <SectionCard title="Conversation"><Transcript turns={turns} /></SectionCard>
        </div>
        <SectionCard title="Call details">
          <div className="grid gap-4">
            <InfoItem label="Outcome" value={call.outcome} />
            <InfoItem label="Status" value={call.status} />
            <InfoItem label="Duration" value={humanDuration(call.call_duration_seconds)} />
            <InfoItem label="Language" value={call.language} />
            <InfoItem label="AI replies" value={call.ai_response_count ?? 0} />
            <InfoItem label="Interruptions" value={call.interruption_count ?? 0} />
            <InfoItem label="Emergency checks" value={call.emergency_checks_count ?? 0} />
          </div>
        </SectionCard>
      </div>
    </Page>
  );
}
