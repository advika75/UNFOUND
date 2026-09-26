import { useState } from "react";

export default function AuthPanel({
  user,
  authLoading,
  authMessage,
  authError,
  onSignIn,
  onSignUp,
  onSignOut,
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function handleSubmit(action) {
    if (!email || !password) {
      return;
    }

    if (action === "signin") {
      await onSignIn({ email, password });
      return;
    }

    await onSignUp({ email, password });
  }

  if (user) {
    return (
      <section className="glass-cyber rounded-[2rem] p-6">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.34em] text-[#00F2FF]">
              Synced identity
            </p>
            <h2 className="mt-3 text-2xl font-semibold text-white">{user.email}</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-white/56">
              Your likes and comparison flow are connected to Supabase and ready to persist.
            </p>
          </div>

          <button
            type="button"
            onClick={onSignOut}
            disabled={authLoading}
            className="rounded-full border border-white/10 bg-white/6 px-5 py-3 text-sm font-semibold text-white transition hover:border-[#00F2FF]/35 hover:text-[#00F2FF] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {authLoading ? "Signing out..." : "Sign out"}
          </button>
        </div>

        {authMessage ? (
          <p className="mt-4 rounded-2xl border border-emerald-300/20 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-100">
            {authMessage}
          </p>
        ) : null}
      </section>
    );
  }

  return (
    <section className="glass-cyber rounded-[2rem] p-6">
      <div className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr] lg:items-end">
        <div>
          <p className="text-xs uppercase tracking-[0.34em] text-[#00F2FF]">Account Layer</p>
          <h2 className="mt-3 text-3xl font-semibold text-white">
            Sign in to save what you discover.
          </h2>
          <p className="mt-3 max-w-xl text-sm leading-6 text-white/56">
            Keep liked products persistent, revisit brand comparison sessions, and turn
            this discovery page into a real workflow.
          </p>
        </div>

        <div className="rounded-[1.75rem] border border-white/10 bg-white/6 p-4 backdrop-blur-xl">
          <div className="space-y-3">
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="Email address"
              className="w-full rounded-2xl border border-white/10 bg-[#0F1318] px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/28 focus:border-[#00F2FF]/45"
            />
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              className="w-full rounded-2xl border border-white/10 bg-[#0F1318] px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/28 focus:border-[#00F2FF]/45"
            />

            <div className="flex flex-col gap-3 sm:flex-row">
              <button
                type="button"
                onClick={() => handleSubmit("signin")}
                disabled={authLoading || !email || !password}
                className="flex-1 rounded-full bg-[#00F2FF] px-4 py-3 text-sm font-semibold text-[#071018] transition hover:shadow-[0_0_26px_rgba(0,242,255,0.42)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {authLoading ? "Working..." : "Sign in"}
              </button>
              <button
                type="button"
                onClick={() => handleSubmit("signup")}
                disabled={authLoading || !email || !password}
                className="flex-1 rounded-full border border-white/12 bg-white/5 px-4 py-3 text-sm font-semibold text-white transition hover:border-[#B794F4]/35 hover:text-[#E5D7FF] disabled:cursor-not-allowed disabled:opacity-60"
              >
                Create account
              </button>
            </div>
          </div>
        </div>
      </div>

      {authError ? (
        <p className="mt-4 rounded-2xl border border-rose-300/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
          {authError}
        </p>
      ) : null}

      {authMessage ? (
        <p className="mt-4 rounded-2xl border border-emerald-300/20 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-100">
          {authMessage}
        </p>
      ) : null}
    </section>
  );
}
