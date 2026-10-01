import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

/** The signed-in user's allowlist role, or null. Admins can read every row, so filter to the caller's email. */
export async function currentRole(): Promise<"admin" | "viewer" | null> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user?.email) return null;
  const { data } = await supabase
    .from("users_allowlist")
    .select("role")
    .eq("email", user.email.toLowerCase())
    .maybeSingle();
  return (data?.role as "admin" | "viewer" | undefined) ?? null;
}

export async function requireAdmin() {
  if ((await currentRole()) !== "admin") redirect("/");
}
