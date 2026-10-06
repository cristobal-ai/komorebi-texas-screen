import { NextResponse, type NextRequest } from "next/server";
import { currentRole } from "@/lib/auth";
import { LAYER_TABLES, buildWorkbook, weightsFromQuery, type ExportData } from "@/lib/export";
import { createClient } from "@/lib/supabase/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Supa = Awaited<ReturnType<typeof createClient>>;

/** Every row of a table (PostgREST returns at most 1,000 per request). */
async function all(supabase: Supa, table: string, order: string): Promise<Record<string, unknown>[]> {
  const out: Record<string, unknown>[] = [];
  for (let from = 0; ; from += 1000) {
    const { data, error } = await supabase.from(table).select("*").order(order).range(from, from + 999);
    if (error) throw new Error(`${table}: ${error.message}`);
    out.push(...(data ?? []));
    if (!data || data.length < 1000) return out;
  }
}

/** GET /export[?a=30&b=15&c=20&d=20&e=9&f=15] → ercot_pv_conversion_screen.xlsx for allowlisted users. */
export async function GET(request: NextRequest) {
  if (!(await currentRole())) return new NextResponse("Not on the allowlist", { status: 403 });
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  try {
    const [plants, scores, monthly, ...layerRows] = await Promise.all([
      all(supabase, "plants", "eia_id"),
      all(supabase, "plant_scores", "eia_id"),
      all(supabase, "plant_metrics_monthly", "eia_id"),
      ...LAYER_TABLES.map((l) => all(supabase, l.name, "eia_id")),
    ]);
    const data: ExportData = { plants, scores, monthly, layers: LAYER_TABLES.map((l, i) => ({ ...l, rows: layerRows[i] })) };
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
