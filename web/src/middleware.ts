import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PATHS = ["/login", "/auth"];

/** Refreshes the Supabase session cookie on every request and sends signed-out visitors to /login. */
export async function middleware(request: NextRequest) {
  // NEXT_PUBLIC_* values are inlined at build time; if they were absent when Vercel built, say so plainly
  // instead of failing with an opaque MIDDLEWARE_INVOCATION_FAILED.
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) {
    const missing = [!url && "NEXT_PUBLIC_SUPABASE_URL", !key && "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"].filter(Boolean);
    console.error("missing env at build time:", missing.join(", "));
    return new NextResponse(
      `Configuration error: ${missing.join(", ")} not set for this deployment. Add it in Vercel → Settings → ` +
        "Environment Variables (Production and Preview), then redeploy.",
      { status: 500, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  let response = NextResponse.next({ request });
  const supabase = createServerClient(
    url,
    key,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        },
      },
    },
  );

  // getUser() validates the token with Supabase Auth (getSession() would trust the cookie blindly).
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  if (!user && !PUBLIC_PATHS.some((p) => path === p || path.startsWith(`${p}/`))) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|maplibre/|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
