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
- **exceljs** — added with the XLSX export (Phase 6); its current release pulls a `uuid` with a moderate advisory.
- `package.json` overrides `postcss` to ^8.5.28 (Next 15.5.27 bundles a version with open advisories).
