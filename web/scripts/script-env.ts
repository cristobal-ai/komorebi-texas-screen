/**
 * Secret-key Supabase client for the scripts in this folder. Reads SUPABASE_URL / SUPABASE_SECRET_KEY from the
 * environment (CI), else from .env.local, else ../pipeline/.env (local runs from web/).
 */
import { existsSync, readFileSync } from "node:fs";
import { createClient } from "@supabase/supabase-js";

function fileEnv(): Record<string, string> {
  const file = [".env.local", "../pipeline/.env"].find((f) => existsSync(f));
  if (!file) return {};
  return Object.fromEntries(
    readFileSync(file, "utf8").split(/\r?\n/).filter((l) => l.includes("=") && !l.startsWith("#"))
      .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]),
  );
}

export function scriptClient() {
  const env = { ...fileEnv(), ...process.env };
  const url = env.SUPABASE_URL || env.NEXT_PUBLIC_SUPABASE_URL;
  const key = env.SUPABASE_SECRET_KEY;
  if (!url || !key) throw new Error("SUPABASE_URL and SUPABASE_SECRET_KEY must be set (environment, .env.local or ../pipeline/.env)");
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}
