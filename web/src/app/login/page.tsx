"use client";

import { useActionState } from "react";
import { requestMagicLink, type LoginState } from "./actions";

export default function LoginPage() {
  const [state, action, pending] = useActionState<LoginState, FormData>(requestMagicLink, null);
  return (
    <main className="mx-auto flex min-h-[70vh] max-w-sm flex-col justify-center px-4">
      <h1 className="text-xl font-semibold">Komorebi Texas Screen</h1>
      <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
        Invite-only. Enter your email and we will send a one-time sign-in link.
      </p>
      <form action={action} className="mt-6 space-y-3">
        <label className="block text-sm font-medium" htmlFor="email">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        />
        <button
          type="submit"
          disabled={pending}
          className="w-full rounded bg-neutral-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
        >
          {pending ? "Sending…" : "Send sign-in link"}
        </button>
      </form>
      {state && (
        <p role="status" className={`mt-4 text-sm ${state.error ? "text-red-600" : "text-neutral-700 dark:text-neutral-300"}`}>
          {state.message}
        </p>
      )}
    </main>
  );
}
