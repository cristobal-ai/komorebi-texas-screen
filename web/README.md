# web/

Next.js 15 (App Router, TypeScript, Tailwind 4) — scaffolded with `create-next-app@15` on 1 Oct 2026. Deployed by
Vercel (Root Directory `web`).

```bash
cd web
cp .env.example .env.local     # fill in the three values
npm install
npm run dev                    # http://localhost:3000
npm run build && npm run lint  # what CI / Vercel run
```

## Pages (v0.1, Phase W1)

| Route | What |
|---|---|
| `/login` | Magic link. A link is sent only if the email is in `users_allowlist` (checked server-side with the secret key); the reply is identical either way so the form cannot probe the list. |
| `/auth/callback` | Exchanges the link's PKCE `code` (or `token_hash`) for a session. Open the link in the same browser that requested it. |
| `/` | Plants table (TanStack Table): tier filter, include-review toggle, search, multi-column sort. Unscored until Phase 5. |
| `/map` | MapLibre GL on the OpenFreeMap "liberty" basemap (no token); USPVDB polygons from the `plants_map` view, dots at state scale, tier colours validated for colour-vision deficiency. |
| `/plants/[id]` | Identity block: capacity, compute envelope, footprint, electrical, generation, provenance, caveat flags. |
| `/admin/users` | Admins add/remove allowlist entries (RLS enforces admin-only writes). |

`src/middleware.ts` refreshes the Supabase session and redirects signed-out visitors to `/login`. All data reads
use the signed-in user's session, so Postgres RLS is the access control; the secret key is used only for the
allowlist check at login (`src/lib/supabase/admin.ts`, `server-only`).

## Supabase Auth settings (dashboard → Authentication → URL Configuration)

- Site URL: the production URL.
- Redirect URLs: `http://localhost:3000/auth/callback`, `https://<vercel-project>-*.vercel.app/auth/callback`
  (previews), and the production `/auth/callback`.

## Deferred

- **shadcn/ui** — its component registry was unreachable from the cloud sandbox that built v0.1; run
  `npx shadcn@latest init` from a machine with access and migrate the hand-styled controls.
- `package.json` overrides `postcss` to ^8.5.28 (Next 15.5.27 bundles a version with open advisories) and `uuid` to
  ^11.1.1 (exceljs 4.4 pins uuid 8, GHSA-w5hq-g745-h8pq; exceljs only calls v4, and the export was re-checked on 11.1.1).

## Phase 6 routes

- `/export[?a=..&f=..]` — `ercot_pv_conversion_screen.xlsx` (`src/lib/export.ts`); the Export XLSX button passes custom
  weights. Check locally without a session: `npx tsx scripts/export-check.ts out.xlsx [e=0 a=40]`.
- `/methodology` — `docs/methodology.md`, copied into `src/content/methodology.ts` by `scripts/gen-methodology.mjs`
  before every build and dev start (edit the doc, never the generated file). `/methodology/docx` builds the Word copy
  from the same markdown (`src/lib/methodology-docx.ts`; local check: `npx tsx scripts/methodology-docx-check.ts out.docx`).
- `/plants/[id]` is the dossier: caveats section (brief §6 + tax flags), host parcel ids, Print / save as PDF (print
  styles in `globals.css`; `dark:` applies on screen only).
