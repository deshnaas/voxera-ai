"use client";

// ============================================================
// Finish setting up a new hospital
// ------------------------------------------------------------
// Reached right after /signup (or automatically: StaffProvider sends any logged-in
// user with no hospital_users row here). Calls register_hospital() — the ONLY
// insert path into facilities / hospital_users from the browser — which reads the
// caller's identity from auth.uid() server-side, so this can never create an
// account under someone else's hospital. See sql/2026_hospital_signup.sql.
// ============================================================

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { PENDING_HOSPITAL_KEY } from "@/app/signup/page";

type Pending = { name: string; type: string; location: string; district: string; phone: string; email: string };

export default function Onboarding() {
  const router = useRouter();

  const [checking, setChecking] = useState(true);
  const [name, setName] = useState("");
  const [type, setType] = useState("General Hospital");
  const [location, setLocation] = useState("");
  const [district, setDistrict] = useState("");
  const [phone, setPhone] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [registrationNo, setRegistrationNo] = useState("");

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (cancelled) return;
      if (!user) {
        router.replace("/");
        return;
      }
      // already has a hospital? nothing to do here
      const { data: hu } = await supabase
        .from("hospital_users")
        .select("facility_id")
        .eq("user_id", user.id)
        .maybeSingle();
      if (cancelled) return;
      if (hu?.facility_id) {
        router.replace("/dashboard");
        return;
      }
      setContactEmail(user.email ?? "");
      try {
        const raw = localStorage.getItem(PENDING_HOSPITAL_KEY);
        if (raw) {
          const p = JSON.parse(raw) as Partial<Pending>;
          if (p.name) setName(p.name);
          if (p.type) setType(p.type);
          if (p.location) setLocation(p.location);
          if (p.district) setDistrict(p.district);
          if (p.phone) setPhone(p.phone);
          if (p.email) setContactEmail(p.email);
        }
      } catch { /* ignore */ }
      setChecking(false);
    })();
    return () => { cancelled = true; };
  }, [router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    if (!name.trim()) {
      setMessage("Enter the hospital's name.");
      return;
    }
    setLoading(true);

    const { error } = await supabase.rpc("register_hospital", {
      p_name: name.trim(),
      p_type: type,
      p_location: location.trim(),
      p_district: district.trim(),
      p_phone: phone.trim() || null,
      p_email: contactEmail.trim() || null,
      p_registration_no: registrationNo.trim() || null,
    });

    if (error) {
      setMessage(error.message);
      setLoading(false);
      return;
    }

    try { localStorage.removeItem(PENDING_HOSPITAL_KEY); } catch { /* ignore */ }
    router.push("/dashboard");
  }

  if (checking) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-100 text-black">
        Loading...
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-100 px-4 py-12">
      <div className="w-full max-w-lg rounded-2xl border-2 border-[#D4AF37] bg-white p-8 shadow-[0_0_20px_rgba(212,175,55,0.45),0_0_50px_rgba(212,175,55,0.2)]">
        <div className="mb-6 text-center">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-gray-500">One last step</p>
          <h1 className="mt-2 text-3xl font-bold tracking-wide text-black">Set up your hospital</h1>
          <p className="mt-3 text-sm text-black">
            You&apos;re signed in. This creates your hospital&apos;s record and makes you its first admin.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Hospital name" htmlFor="name">
            <input id="name" required value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Type" htmlFor="type">
              <select id="type" value={type} onChange={(e) => setType(e.target.value)} className={inputCls}>
                {["General Hospital", "Multi-specialty Hospital", "Clinic", "Primary Health Centre", "Diagnostic Centre"].map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="District" htmlFor="district">
              <input id="district" value={district} onChange={(e) => setDistrict(e.target.value)} className={inputCls} />
            </Field>
          </div>

          <Field label="Address / location" htmlFor="location">
            <input id="location" value={location} onChange={(e) => setLocation(e.target.value)} className={inputCls} />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Phone (optional)" htmlFor="phone">
              <input id="phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Contact email" htmlFor="contactEmail">
              <input id="contactEmail" type="email" value={contactEmail} onChange={(e) => setContactEmail(e.target.value)} className={inputCls} />
            </Field>
          </div>

          <Field label="Registration / license number (optional)" htmlFor="registrationNo">
            <input id="registrationNo" value={registrationNo} onChange={(e) => setRegistrationNo(e.target.value)} className={inputCls} />
          </Field>

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg border-2 border-[#D4AF37] bg-black px-4 py-3 font-bold text-white shadow-[0_0_15px_rgba(212,175,55,0.45)] transition-all duration-300 hover:shadow-[0_0_25px_rgba(212,175,55,0.8)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Setting up..." : "Finish setup"}
          </button>

          {message && (
            <div role="alert" className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-center text-sm font-medium text-red-700">
              {message}
            </div>
          )}
        </form>
      </div>
    </main>
  );
}

const inputCls =
  "w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-black placeholder-gray-400 outline-none transition-all duration-300 focus:shadow-[0_0_15px_rgba(212,175,55,0.7)]";

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-2 block text-sm font-semibold text-black">
        {label}
      </label>
      {children}
    </div>
  );
}
