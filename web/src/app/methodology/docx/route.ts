import { NextResponse } from "next/server";
import { METHODOLOGY } from "@/content/methodology";
import { currentRole } from "@/lib/auth";
import { methodologyDocx } from "@/lib/methodology-docx";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** GET /methodology/docx → the methodology note as Word, built from the same markdown as the page. */
export async function GET() {
  if (!(await currentRole())) return new NextResponse("Not on the allowlist", { status: 403 });
  const buf = await methodologyDocx(METHODOLOGY);
  return new NextResponse(new Uint8Array(buf), {
    headers: {
      "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "content-disposition": 'attachment; filename="komorebi_texas_screen_methodology.docx"',
      "cache-control": "no-store",
    },
  });
}
