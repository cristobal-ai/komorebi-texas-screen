import { requireAdmin } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { removeUser } from "./actions";
import AddUserForm from "./add-user-form";

export const dynamic = "force-dynamic";

export default async function UsersPage() {
  await requireAdmin();
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const { data: rows, error } = await supabase
    .from("users_allowlist")
    .select("email,role,added_at,added_by")
    .order("added_at");

  return (
    <main className="mx-auto max-w-4xl px-4 py-6">
      <h1 className="text-xl font-semibold">Users</h1>
      <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
        Only addresses on this list can receive a sign-in link. Adding someone does not email them — send them the
        site address; they request their own link at /login.
      </p>
      <div className="mt-4">
        <AddUserForm />
      </div>
      {error ? (
        <p className="mt-6 text-sm text-red-600">{error.message}</p>
      ) : (
        <table className="mt-6 w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-left dark:border-neutral-800">
              <th className="py-2 font-medium">Email</th>
              <th className="py-2 font-medium">Role</th>
              <th className="py-2 font-medium">Added</th>
              <th className="py-2 font-medium">By</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((r) => (
              <tr key={r.email} className="border-b border-neutral-100 dark:border-neutral-900">
                <td className="py-1.5">{r.email}</td>
                <td>{r.role}</td>
                <td className="tabular-nums">{String(r.added_at).slice(0, 10)}</td>
                <td className="text-neutral-500">{r.added_by ?? "—"}</td>
                <td className="text-right">
                  {r.email !== user?.email?.toLowerCase() && (
                    <form action={removeUser}>
                      <input type="hidden" name="email" value={r.email} />
                      <button className="text-red-600 hover:underline">Remove</button>
                    </form>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
