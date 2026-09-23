"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export default function Home() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [darkMode, setDarkMode] = useState(false);

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setLoading(true);
    setMessage("");

    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (error) {
      setMessage(error.message);
      setLoading(false);
      return;
    }

    router.push("/dashboard");
  }

  return (
    <main
      className={`relative flex min-h-screen items-center justify-center px-4 transition-colors duration-300 ${
        darkMode ? "bg-black" : "bg-gray-100"
      }`}
    >
      {/* Theme Toggle */}
      <button
        type="button"
        onClick={() => setDarkMode((current) => !current)}
        className="absolute right-6 top-6 rounded-full border-2 border-[#D4AF37] bg-white px-4 py-2 text-sm font-semibold text-black shadow-[0_0_15px_rgba(212,175,55,0.45)] transition-all duration-300 hover:shadow-[0_0_25px_rgba(212,175,55,0.8)]"
      >
        {darkMode ? "☀️ Light" : "🌙 Dark"}
      </button>

      {/* Login Card */}
      <div className="w-full max-w-md rounded-2xl border-2 border-[#D4AF37] bg-white p-8 shadow-[0_0_20px_rgba(212,175,55,0.45),0_0_50px_rgba(212,175,55,0.2)]">
        {/* Header */}
        <div className="mb-8 text-center">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-gray-500">
            Hospital Coordination
          </p>

          <h1 className="mt-2 text-4xl font-bold tracking-wide text-black">
            VOXERA
          </h1>

          <p className="mt-3 text-black">
            Hospital Staff Login
          </p>
        </div>

        {/* Login Form */}
        <form onSubmit={handleLogin} className="space-y-5">
          {/* Email */}
          <div>
            <label
              htmlFor="email"
              className="mb-2 block text-sm font-semibold text-black"
            >
              Email
            </label>

            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="hospital.a@caresetu.demo"
              className="w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-black placeholder-gray-400 outline-none transition-all duration-300 focus:shadow-[0_0_15px_rgba(212,175,55,0.7)]"
            />
          </div>

          {/* Password */}
          <div>
            <label
              htmlFor="password"
              className="mb-2 block text-sm font-semibold text-black"
            >
              Password
            </label>

            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Enter your password"
              className="w-full rounded-lg border-2 border-[#D4AF37] bg-white px-4 py-3 text-black placeholder-gray-400 outline-none transition-all duration-300 focus:shadow-[0_0_15px_rgba(212,175,55,0.7)]"
            />
          </div>

          {/* Sign In */}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg border-2 border-[#D4AF37] bg-black px-4 py-3 font-bold text-white shadow-[0_0_15px_rgba(212,175,55,0.45)] transition-all duration-300 hover:shadow-[0_0_25px_rgba(212,175,55,0.8)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        {/* Login Message */}
        {message && (
          <div
            role="alert"
            className="mt-5 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-center text-sm font-medium text-red-700"
          >
            {message}
          </div>
        )}

        <p className="mt-6 text-center text-sm text-gray-600">
          New hospital?{" "}
          <a href="/signup" className="font-semibold text-black underline decoration-[#D4AF37] decoration-2 underline-offset-2">
            Create an account
          </a>
        </p>
      </div>
    </main>
  );
}