import "server-only";
import { createClient } from "@supabase/supabase-js";

/**
 * Secret-key client: bypasses RLS. Server-only (the "server-only" import fails the build if a client component
 * pulls this in). Used solely to check the invite allowlist before sending a magic link.
 */
export function createAdminClient() {
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!key) throw new Error("SUPABASE_SECRET_KEY is not set");
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
