"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAdminAuth } from "@/hooks/use-admin-auth";
import { Eye, EyeOff, Lock, User, ArrowRight } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAdminAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      await login(username, password);
      router.push("/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Invalid credentials");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg-base">
      {/* Background gradient effects */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-[200px] -top-[200px] h-[600px] w-[600px] rounded-full bg-accent/[0.04] blur-[120px]" />
        <div className="absolute -bottom-[200px] -right-[200px] h-[600px] w-[600px] rounded-full bg-accent/[0.03] blur-[120px]" />
        <div className="absolute left-1/2 top-1/2 h-[400px] w-[400px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/[0.02] blur-[80px]" />
      </div>

      {/* Grid pattern */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.015]"
        style={{
          backgroundImage: `linear-gradient(hsl(var(--fg-default)) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--fg-default)) 1px, transparent 1px)`,
          backgroundSize: "64px 64px",
        }}
      />

      <div className="relative z-10 w-full max-w-[420px] px-6">
        {/* Logo & branding */}
        <div className="mb-10 text-center">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-accent to-accent-hover shadow-lg shadow-accent/20">
            <span className="text-xl font-bold text-fg-on-accent tracking-tight">O</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em] text-fg">
            OPRAI Admin
          </h1>
          <p className="mt-2 text-sm text-fg-muted">
            Secure access to your administration console
          </p>
        </div>

        {/* Login card */}
        <div className="rounded-2xl border border-line-muted bg-bg-surface/80 p-8 shadow-lg backdrop-blur-xl">
          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div className="flex items-center gap-3 rounded-xl border border-semantic-danger/20 bg-semantic-danger/[0.06] px-4 py-3">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-semantic-danger/20">
                  <span className="text-[10px] font-bold text-semantic-danger">!</span>
                </div>
                <p className="text-[13px] text-semantic-danger">{error}</p>
              </div>
            )}

            <div className="space-y-2">
              <label className="text-[13px] font-medium text-fg-muted">Username</label>
              <div className="relative">
                <User className="absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-fg-subtle" />
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Enter your username"
                  autoComplete="username"
                  required
                  className="flex h-11 w-full rounded-xl border border-line bg-bg-elevated/50 pl-11 pr-4 text-sm text-fg placeholder:text-fg-subtle transition-all duration-200 focus:border-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/15 hover:border-line/80"
                />
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-[13px] font-medium text-fg-muted">Password</label>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-fg-subtle" />
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  autoComplete="current-password"
                  required
                  className="flex h-11 w-full rounded-xl border border-line bg-bg-elevated/50 pl-11 pr-11 text-sm text-fg placeholder:text-fg-subtle transition-all duration-200 focus:border-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/15 hover:border-line/80"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-fg-subtle transition-colors hover:text-fg-muted"
                >
                  {showPassword ? <EyeOff className="h-[18px] w-[18px]" /> : <Eye className="h-[18px] w-[18px]" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="group flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-accent to-accent-hover text-sm font-semibold text-fg-on-accent shadow-md shadow-accent/20 transition-all duration-200 hover:shadow-lg hover:shadow-accent/30 hover:brightness-110 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
            >
              {loading ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-fg-on-accent/30 border-t-fg-on-accent" />
              ) : (
                <>
                  Sign In
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                </>
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <p className="mt-6 text-center text-xs text-fg-subtle">
          Protected by OPRAI Security
        </p>
      </div>
    </div>
  );
}
