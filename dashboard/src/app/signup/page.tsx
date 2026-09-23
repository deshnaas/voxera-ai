"use client";

// ============================================================
// Hospital sign-up
// ------------------------------------------------------------
// Creates the Supabase Auth account only. The hospital record itself (facility +
// the admin's hospital_users row) is created on /onboarding, right after this, via
// the register_hospital() RPC — that function reads WHO is calling from auth.uid(),
// so it can only ever create a facility owned by the account that is actually
// logged in. See sql/2026_hospital_signup.sql for why that split matters here.
//
// The hospital details entered below are only a convenience: they're kept in
// localStorage so /onboarding can prefill them, never as the source of truth.
// ============================================================

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export const PENDING_HOSPITAL_KEY = "voxera-pending-hospital";

export default function SignUp() {
  const router = useRouter();

  const [hospitalName, setHospitalName] = useState("");
  const [hospitalType, setHospitalType] = useState("General Hospital");
  const [location, setLocation] = useState("");
  const [district, setDistrict] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSignUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setNotice("");

    if (!hospitalName.trim()) {
      setMessage("Enter the hospital's name.");
      return;
    }
    if (password.length < 8) {
      setMessage("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setMessage("Passwords don't match.");
      return;
    }

    setLoading(true);
    try {
      localStorage.setItem(
        PENDING_HOSPITAL_KEY,
        JSON.stringify({
          name: hospitalName.trim(),
          type: hospitalType,
          location: location.trim(),
          district: district.trim(),
          phone: phone.trim(),
          email: email.trim(),
        })
      );
    } catch {
      /* localStorage unavailable (private browsing): onboarding just starts blank */
    }

    const { data, error } = await supabase.auth.signUp({
      email: email.trim(),
      password,
    });

    if (error) {
      setMessage(error.message);
      setLoading(false);
      return;
    }

    if (data.session) {
      // email confirmation is off for this project: already signed in, go straight to onboarding
      router.push("/onboarding");
      return;
    }

    // email confirmation is required: they'll land on /onboarding automatically after
    // confirming and logging in (StaffProvider sends anyone with no hospital there).
    setNotice(
      "Account created. Check your email to confirm it, then sign in — we'll pick up right where you left off."
    );
    setLoading(false);
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-gray-100 px-4 py-12 transition-colors duration-300">
      <div className="w-full max-w-lg rounded-2xl border-2 border-[#D4AF37] bg-white p-8 shadow-[0_0_20px_rgba(212,175,55,0.45),0_0_50px_rgba(212,175,55,0.2)]">
        <div className="mb-8 text-center">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-gray-500">
            Hospital Coordination
          </p>
          <h1 className="mt-2 text-4xl font-bold tracking-wide text-black">VOXERA</h1>
          <p className="mt-3 text-black">Register your hospital</p>
        </div>

        {notice ? (
          <div className="space-y-5 text-center">
            <div
              role="status"
              className="rounded-lg border border-green-300 bg-green-50 px-4 py-3 text-sm font-medium text-green-800"
            >
              {notice}
            </div>
            <a
              href="/"
              className="inline-block rounded-lg border-2 border-[#D4AF37] bg-black px-6 py-3 font-bold text-white shadow-[0_0_15px_rgba(212,175,55,0.45)] transition-all duration-300 hover:shadow-[0_0_25px_rgba(212,175,55,0.8)]"
            >
              Go to sign in
            </a>
          </div>
        ) : (
          <form onSubmit={handleSignUp} className="space-y-5">
            <fieldset className="space-y-4">
              <legend className="mb-1 text-xs font-bold uppercase tracking-wider text-gray-500">
                Hospital details
              </legend>

              <Field label="Hospital name" htmlFor="hospitalName">
                <input
                  id="hospitalName"
                  required
                  value={hospitalName}
                  onChange={(e) => setHospitalName(e.target.value)}
                  placeholder="CareSetu General Hospital"
                  className={inputCls}
                />
              </Field>

              <div className="grid grid-cols-2 gap-4">
                <Field label="Type" htmlFor="hospitalType">
                  <select
                    id="hospitalType"
                    value={hospitalType}
                    onChange={(e) => setHospitalType(e.target.value)}
                    className={inputCls}
                  >
                    {["General Hospital", "Multi-specialty Hospital", "Clinic", "Primary Health Centre", "Diagnostic Centre"].map(
                      (t) => (
                        <option key={t} value={t}>{t}</option>
                      )
                    )}
                  </select>
                </Field>
                <Field label="District" htmlFor="district">
                  <input
                    id="district"
                    value={district}
                    onChange={(e) => setDistrict(e.target.value)}
                    placeholder="Chennai"
                    className={inputCls}
                  />
                </Field>
              </div>

              <Field label="Address / location" htmlFor="location">
                <input
                  id="location"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="12 Anna Salai, Chennai"
                  className={inputCls}
                />
              </Field>

              <Field label="Hospital phone (optional)" htmlFor="phone">
                <input
                  id="phone"
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="044 1234 5678"
                  className={inputCls}
                />
              </Field>
            </fieldset>

            <fieldset className="space-y-4 border-t border-gray-200 pt-4">
              <legend className="mb-1 text-xs font-bold uppercase tracking-wider text-gray-500">
                Your admin login
              </legend>

              <Field label="Email" htmlFor="email">
                <input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@yourhospital.com"
                  className={inputCls}
                />
              </Field>

              <div className="grid grid-cols-2 gap-4">
                <Field label="Password" htmlFor="password">
                  <input
                    id="password"
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={8}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="At least 8 characters"
                    className={inputCls}
                  />
                </Field>
                <Field label="Confirm password" htmlFor="confirm">
                  <input
                    id="confirm"
                    type="password"
                    autoComplete="new-password"
                    required
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    placeholder="Repeat password"
                    className={inputCls}
                  />
                </Field>
              </div>
            </fieldset>

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg border-2 border-[#D4AF37] bg-black px-4 py-3 font-bold text-white shadow-[0_0_15px_rgba(212,175,55,0.45)] transition-all duration-300 hover:shadow-[0_0_25px_rgba(212,175,55,0.8)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? "Creating account..." : "Create hospital account"}
            </button>

            {message && (
              <div
                role="alert"
                className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-center text-sm font-medium text-red-700"
              >
                {message}
              </div>
            )}

            <p className="text-center text-sm text-gray-600">
              Already have an account?{" "}
              <a href="/" className="font-semibold text-black underline decoration-[#D4AF37] decoration-2 underline-offset-2">
                Sign in
              </a>
            </p>
          </form>
        )}
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
