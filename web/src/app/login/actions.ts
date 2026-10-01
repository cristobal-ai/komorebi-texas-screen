"use server";

import { headers } from "next/headers";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

export type LoginState = { message: string; error?: boolean } | null;

// Same reply whether or not the address is invited, so the form cannot be used to probe the allowlist.
const SENT = "If this address is on the invite list, a sign-in link is on its way. It expires in 1 hour.";

export async function requestMagicLink(_prev: LoginState, form: FormData): Promise<LoginState> {
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return { message: "Enter a valid email address.", error: true };

  const admin = createAdminClient();
  const { data, error: lookupError } = await admin
    .from("users_allowlist")
    .select("email")
    .eq("email", email)
    .maybeSingle();
  if (lookupError) {
    console.error("allowlist lookup failed", lookupError.message);
    return { message: "Sign-in is temporarily unavailable. Try again shortly.", error: true };
  }
  if (!data) return { message: SENT };

  const h = await headers();
  const origin = h.get("origin") ?? `https://${h.get("host")}`;
  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: `${origin}/auth/callback`, shouldCreateUser: true },
  });
  if (error) {
    console.error("signInWithOtp failed", error.message);
    return { message: "Could not send the link. Try again in a minute.", error: true };
  }
  return { message: SENT };
}
