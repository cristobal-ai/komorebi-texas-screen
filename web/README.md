# web/

Next.js 15 app deployed by Vercel. **Not scaffolded yet** — Phase W1 in `docs/build-plan-v2.md` §5.

To scaffold (run from the repo root, in Claude Code):

```bash
npx create-next-app@latest web --typescript --tailwind --eslint --app --src-dir --import-alias "@/*" --use-npm
cd web && npx shadcn@latest init
npm install @supabase/supabase-js @supabase/ssr @tanstack/react-table maplibre-gl recharts exceljs
```

Vercel project settings: Root Directory = `web`, Framework = Next.js. Environment variables per `web/.env.example`.

Pages planned for v0.1: `/login` (magic link, allowlist-gated) · `/` ranked table with tier filter · `/map` · `/plants/[id]` dossier · `/methodology` · `/admin/crosswalk` · `/admin/users`.
