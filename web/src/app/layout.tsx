import type { Metadata } from "next";
import Link from "next/link";
import { currentRole } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import "./globals.css";

export const metadata: Metadata = {
  title: "Komorebi Texas Screen",
  description: "ERCOT utility-scale PV plants screened as AI-compute conversion targets.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const isAdmin = user ? (await currentRole()) === "admin" : false;

  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-neutral-900 antialiased dark:bg-neutral-950 dark:text-neutral-100">
        {user && (
          <header className="border-b border-neutral-200 print:hidden dark:border-neutral-800">
            <nav className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-sm">
              <Link href="/" className="font-semibold">
                Komorebi Texas Screen
              </Link>
              <Link href="/" className="hover:underline">
                Plants
              </Link>
              <Link href="/map" className="hover:underline">
                Map
              </Link>
              <Link href="/methodology" className="hover:underline">
                Methodology
              </Link>
              {isAdmin && (
                <Link href="/admin/users" className="hover:underline">
                  Users
                </Link>
              )}
              <span className="ml-auto text-neutral-500">{user.email}</span>
              <form action="/auth/signout" method="post">
                <button className="rounded border border-neutral-300 px-2 py-1 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900">
                  Sign out
                </button>
              </form>
            </nav>
          </header>
        )}
        {children}
      </body>
    </html>
  );
}
