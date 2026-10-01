"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

export type FormState = { message: string; error?: boolean } | null;

// Writes go through the admin's own session; RLS (admins insert/update/delete) is the real guard.
export async function addUser(_prev: FormState, form: FormData): Promise<FormState> {
  await requireAdmin();
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  const role = form.get("role") === "admin" ? "admin" : "viewer";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return { message: "Enter a valid email address.", error: true };
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const { error } = await supabase
    .from("users_allowlist")
    .upsert({ email, role, added_by: user?.email ?? null }, { onConflict: "email" });
  if (error) return { message: `Could not add: ${error.message}`, error: true };
  revalidatePath("/admin/users");
  return { message: `${email} can now request a sign-in link at /login (role: ${role}).` };
}

export async function removeUser(form: FormData) {
  await requireAdmin();
  const email = String(form.get("email") ?? "");
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (email === user?.email?.toLowerCase()) return; // never lock yourself out
  await supabase.from("users_allowlist").delete().eq("email", email);
  revalidatePath("/admin/users");
}
