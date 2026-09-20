// Types for the Patient Intelligence API (voxera_patientfetch/service.py).
// Mirrors the server's response shapes; the server decides which sections a user may receive.

export type { Patient, Prescription } from "./voxera";

export type Role = "admin" | "doctor" | "nurse" | "receptionist" | "operator";
export type Relationship = "home" | "external";
export type AccessReason = "patient_referred" | "emergency_care" | "follow_up" | "other";

export type Section =
  | "identity" | "allergies" | "medications" | "prescriptions" | "consultations"
  | "referrals" | "appointments" | "calls" | "transcripts" | "documents" | "audit" | "notes";

export type MedicationSource = "clinician" | "ocr" | "ocr_verified" | "patient_reported" | "imported";
export type MedicationStatus =
  | "active" | "stopped" | "completed" | "pending" | "needs_review" | "rejected" | "confirmed";

export type PatientIdentity = {
  id: string;
  patient_id: string;
  full_name: string;
  age?: number | null;
  gender?: string | null;
  phone?: string | null;
  preferred_language?: string | null;
  village_or_locality?: string | null;
  district?: string | null;
};

export type PatientMedication = {
  id: string;
  origin: "patient_medications" | "prescriptions" | "prescription_items";
  medicine_name: string | null;
  generic_name: string | null;
  strength: string | null;
  form: string | null;
  route: string | null;
  frequency: string | null;
  duration: string | null;
  instructions: string | null;
  source: MedicationSource;
  status: MedicationStatus;
  confidence?: number | null;
  verified_by: string | null;
  verified_at: string | null;
  date: string;
  document_id: string | null;
  is_clinician_confirmed: boolean;
  is_current: boolean;
  provenance_label: string;
};

export type PrescriptionItem = {
  id: string;
  document_id: string | null;
  patient_id: string;
  medicine_name: string | null;
  generic_name: string | null;
  strength: string | null;
  form: string | null;
  route: string | null;
  frequency: string | null;
  duration: string | null;
  instructions: string | null;
  confidence: number | null;
  raw_text: string | null;
  warnings: string[] | null;
  source: "ocr";
  verification_status: "pending" | "needs_review" | "confirmed" | "rejected";
  created_at: string;
};

export type MedicalDocument = {
  id: string;
  filename: string | null;
  document_type: string;
  extracted_text: string | null;
  ocr_metadata: { method?: string; confidence?: number; pages?: number; error?: string | null } | null;
  ocr_status: string;
  created_at: string;
  uploaded_by: string | null;
  facility_id: string | null;
};

export type VoiceSignal = {
  arousal_level: "low" | "moderate" | "high" | "unavailable";
  confidence: number;
  signal_quality?: string;
  turns_analyzed?: number;
  note?: string;
};

export type PatientClinicalContext = {
  chief_complaint?: string;
  symptoms?: string[];
  duration?: string;
  onset?: string;
  severity?: string;
  quality?: string;
  location?: string;
  radiation?: string;
  red_flags?: string[];
  negated_red_flags?: string[];
  relevant_history?: string[];
  conclusion?: string;
  next_action?: string;
};

export type RecordContext = {
  patient_verified?: boolean;
  identity_assurance?: string;
  previous_relevant_consultation?: { date: string | null; chief_complaint: string | null };
  relevant_medications?: string[];
  relevant_prescription?: { medicine: string | null; date: string | null };
};

export type Consultation = {
  id: string;
  kind: string;
  date: string;
  facility_id: string | null;
  chief_complaint: string | null;
  symptoms: string[];
  assessment: string | null;
  risk_level: string | null;
  guidance: string[];
  otc_guidance: string[];
  emergency: boolean;
  outcome: string | null;
  follow_up: Record<string, unknown> | null;
  voice_signal?: VoiceSignal | null;
  clinical_context?: PatientClinicalContext | null;
  record_context?: RecordContext | null;
};

export type PatientAccessLog = {
  id: string;
  patient_id: string;
  accessed_by_user_id: string | null;
  facility_id: string | null;
  facility_name?: string | null;
  role: string | null;
  access_reason: string | null;
  accessed_at: string;
  source: string;
  action: string | null;
  relationship: string | null;
  granted: boolean | null;
};

export type ClinicianPrescriptionRow = {
  id: string;
  medication_name: string;
  dosage: string | null;
  route: string | null;
  frequency: string | null;
  duration: string | null;
  instructions: string | null;
  prescribed_by: string | null;
  status: string;
  prescribed_at: string;
};

export type PatientRecord = {
  relationship: Relationship;
  label: string;
  sections: Section[];
  role: Role | null;
  patient: PatientIdentity;
  allergies?: { allergies: string[]; blood_group?: string | null };
  medications?: PatientMedication[];
  prescriptions?: ClinicianPrescriptionRow[];
  prescription_items?: PrescriptionItem[];
  consultations?: Consultation[];
  conditions?: string[];
  referrals?: Array<{ id: string; status: string; recommended_department: string | null; created_at: string }>;
  appointments?: Array<{ id: string; appointment_date: string; doctor_name: string | null; status: string }>;
  documents?: MedicalDocument[];
  audit?: PatientAccessLog[];
  partial?: boolean;
};

export type SearchResult = {
  id: string;
  patient_id: string;
  full_name: string;
  age: number | null;
  gender: string | null;
  relationship: Relationship;
  access: "direct" | "reason_required";
  last_consultation?: string | null;
  active_medications?: number | null;
  allergies?: number | null;
};

export type RecordAnswer = {
  answer: string;
  intent: string;
  found: boolean;
  conflict: boolean;
  confidence: number;
  used_llm: boolean;
  ai_generated: true;
  relationship: Relationship;
  sources: Array<{ type: string; id: string | null; date: string | null; source: string | null }>;
};

export type UploadResult = {
  document: MedicalDocument;
  items: PrescriptionItem[];
  status: "staged" | "unreadable" | "no_medications";
  message: string;
  warning?: string;
  error?: string;
};

export type DocumentView = {
  document: MedicalDocument;
  url: string | null;
  items: PrescriptionItem[];
};

export const ACCESS_REASONS: Array<{ value: AccessReason; label: string }> = [
  { value: "patient_referred", label: "Patient referred to us" },
  { value: "emergency_care", label: "Emergency care" },
  { value: "follow_up", label: "Follow-up care" },
  { value: "other", label: "Other (explain)" },
];
