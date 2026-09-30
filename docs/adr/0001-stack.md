# ADR-0001 — Stack: Supabase + Next.js on Vercel + Python pipeline on GitHub Actions

**Status:** Accepted, 1 Sep 2026
**Deciders:** Cristobal (Smart Investments)

## Context

The screener must (a) run a geospatial Python pipeline over multi-GB ERCOT/EIA/GIS sources about once a month, (b) present results to invited outside users (JV partners, lenders, brokers) in a professional UI, and (c) be maintainable by one person moving between several computers.

## Decision

- **Pipeline: Python 3.11 on GitHub Actions.** Monthly schedule plus manual dispatch. Free tier covers one run per month on a private repo (2,000 minutes). First SCED backfill runs once and is cached to Supabase Storage.
- **Backend: Supabase.** One project gives Postgres (+PostGIS), invite-only magic-link auth, and file storage, with a Claude connector for schema work. Free tier is sufficient for a few hundred plants × 36 months; Pro is $25/mo if limits bite.
- **Web: Next.js 15 on Vercel Pro.** Vercel is already in use by the owner. Pro ($20/mo) is required because Hobby is non-commercial by policy; Vercel's own password gate is a paid add-on with no per-user audit trail, so auth is in the app.
- **Source of truth: this Git repo**, including `CLAUDE.md`, the brief and the build plan, so any machine with a clone has full context.

## Alternatives considered

- *Pipeline on Vercel functions/cron* — rejected: geopandas + multi-GB inputs exceed function limits (300 s Hobby / 800 s Pro) and Hobby cron is once-a-day only.
- *Pipeline on the owner's Mac Mini* — viable fallback for heavy backfills; rejected as the scheduled path because it depends on one box being up.
- *Neon + Clerk* — two vendors for what Supabase does in one; no Claude connector for Clerk.
- *Static JSON in the repo, no database* — zero infra, but no accounts, allowlist, notes or on-demand export without adding a DB later.
- *Shared password gate* — fastest, but no per-user trail and a paid Vercel add-on.

## Consequences

- Secrets live in three places (GitHub Actions, Vercel env, per-machine `.env`); `docs/accounts-and-keys.md` maps them.
- The web app can re-rank client-side from stored sub-scores, so weight tuning does not require a pipeline run.
- Upgrading Vercel to Pro is a prerequisite for the first outside invite, not for development.
