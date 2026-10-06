import { NextResponse, type NextRequest } from "next/server";
import { currentRole } from "@/lib/auth";
import { buildWorkbook, weightsFromQuery } from "@/lib/export";
import { loadExportData } from "@/lib/export-data";
import { createClient } from "@/lib/supabase/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** GET /export[?a=30&b=15&c=20&d=20&e=9&f=15] → ercot_pv_conversion_screen.xlsx for allowlisted users. */
export async function GET(request: NextRequest) {
  if (!(await currentRole())) return new NextResponse("Not on the allowlist", { status: 403 });
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  try {
    const data = await loadExportData(supabase);
    const now = new Date();
    const wb = buildWorkbook(data, { generatedAt: now, user: user?.email ?? null, weights: weightsFromQuery(request.nextUrl.searchParams) });
    const buf = await wb.xlsx.writeBuffer();
    return new NextResponse(buf as ArrayBuffer, {
      headers: {
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-disposition": `attachment; filename="ercot_pv_conversion_screen_${now.toISOString().slice(0, 10)}.xlsx"`,
        "cache-control": "no-store",
      },
    });
  } catch (e) {
    return new NextResponse(`Export failed: ${(e as Error).message}`, { status: 500 });
  }
}
