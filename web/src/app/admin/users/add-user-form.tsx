"use client";

import { useActionState } from "react";
import { addUser, type FormState } from "./actions";

export default function AddUserForm() {
  const [state, action, pending] = useActionState<FormState, FormData>(addUser, null);
  return (
    <form action={action} className="flex flex-wrap items-end gap-2 text-sm">
      <label className="flex flex-col">
        Email
        <input name="email" type="email" required className="mt-1 w-72 rounded border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900" />
      </label>
      <label className="flex flex-col">
        Role
        <select name="role" className="mt-1 rounded border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900">
          <option value="viewer">viewer</option>
          <option value="admin">admin</option>
        </select>
      </label>
      <button disabled={pending} className="rounded bg-neutral-900 px-3 py-1.5 font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900">
        {pending ? "Adding…" : "Add to allowlist"}
      </button>
      {state && <p role="status" className={`w-full ${state.error ? "text-red-600" : "text-neutral-600 dark:text-neutral-400"}`}>{state.message}</p>}
    </form>
  );
}
