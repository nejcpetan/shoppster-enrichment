"use client";

import { useState, useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, Zap, ArrowRight } from "lucide-react";

export default function LoginPage() {
  const { login, user, isLoading } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && user) {
      router.replace("/");
    }
  }, [user, isLoading, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await login(email, password);
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-5 h-5 animate-spin text-zinc-600" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-zinc-100 flex flex-col items-center justify-center p-4 relative overflow-hidden">

      {/* Background glow */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-indigo-600/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-violet-600/8 rounded-full blur-[100px]" />
      </div>

      {/* Subtle dot grid */}
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.15]"
        style={{
          backgroundImage: `radial-gradient(circle, #3f3f46 1px, transparent 1px)`,
          backgroundSize: "28px 28px",
          maskImage: "radial-gradient(ellipse 80% 80% at 50% 50%, black 40%, transparent 100%)",
          WebkitMaskImage: "radial-gradient(ellipse 80% 80% at 50% 50%, black 40%, transparent 100%)",
        }}
      />

      {/* Card */}
      <div className="relative w-[380px] max-w-full space-y-8 mx-auto">

        {/* Logo */}
        <div className="flex flex-col items-center gap-4 text-center">
          <div className="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center shadow-2xl shadow-indigo-600/40 ring-1 ring-indigo-500/50">
            <Zap className="w-6 h-6 text-white fill-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">Enrichment Engine</h1>
            <p className="text-sm text-zinc-500 mt-1">Sign in to your workspace</p>
          </div>
        </div>

        {/* Form card */}
        <div className="bg-zinc-900/70 border border-zinc-800 rounded-2xl p-8 shadow-2xl shadow-black/60 backdrop-blur-sm space-y-5">

          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-widest">
                Email
              </label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
                autoFocus
                className="h-11 bg-zinc-950 border-zinc-800 text-zinc-100 placeholder:text-zinc-700
                  focus-visible:ring-1 focus-visible:ring-indigo-500 focus-visible:ring-offset-0
                  focus-visible:border-indigo-500 hover:border-zinc-700 transition-colors rounded-lg text-sm"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-widest">
                Password
              </label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                className="h-11 bg-zinc-950 border-zinc-800 text-zinc-100 placeholder:text-zinc-700
                  focus-visible:ring-1 focus-visible:ring-indigo-500 focus-visible:ring-offset-0
                  focus-visible:border-indigo-500 hover:border-zinc-700 transition-colors rounded-lg text-sm"
              />
            </div>

            {error && (
              <div className="flex items-center gap-2.5 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3.5 py-2.5">
                <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-red-400" />
                {error}
              </div>
            )}

            <Button
              type="submit"
              disabled={submitting}
              className="w-full h-11 bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700
                text-white font-semibold rounded-lg shadow-lg shadow-indigo-600/25
                transition-all duration-150 group mt-1"
            >
              {submitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <span className="flex items-center justify-center gap-2">
                  Sign in
                  <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
                </span>
              )}
            </Button>
          </form>
        </div>

        <p className="text-center text-xs text-zinc-700">
          Built by Webfast &middot; Powered by Claude &amp; Gemini
        </p>
      </div>
    </div>
  );
}
