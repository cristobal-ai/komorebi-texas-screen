import { NextResponse } from "next/server";
import { currentRole } from "@/lib/auth";
import { EXPORT_BUCKET, LATEST_EXPORT } from "@/lib/export-data";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/** GET /export/latest → the XLSX the last pipeline run wrote to Storage (default weights), via a short-lived signed URL. */
export async function GET() {
  if (!(await currentRole())) return new NextResponse("Not on the allowlist", { status: 403 });
  const supabase = await createClient();
  const { data, error } = await supabase.storage.from(EXPORT_BUCKET).createSignedUrl(LATEST_EXPORT, 60, { download: true });
  if (error || !data) return new NextResponse(`No pipeline snapshot yet (${error?.message ?? "not found"})`, { status: 404 });
  return NextResponse.redirect(data.signedUrl, { headers: { "cache-control": "no-store" } });
}
