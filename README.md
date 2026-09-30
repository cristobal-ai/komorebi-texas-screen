# Komorebi Texas Screen

Ranks utility-scale PV plants in ERCOT as acquisition targets for conversion into AI compute campuses (Komorebi cooling-first model), and serves the ranked table, map and site dossiers to invited users through a web app.

- Plan and decisions: [`docs/build-plan-v2.md`](docs/build-plan-v2.md)
- Original specification: [`docs/brief.md`](docs/brief.md)
- Accounts, keys and where each secret goes: [`docs/accounts-and-keys.md`](docs/accounts-and-keys.md)
- Working agreement for Claude Code sessions: [`CLAUDE.md`](CLAUDE.md)

## Layout

```
pipeline/     Python 3.11 — phases 1–6, config.yaml, tests (runs on GitHub Actions monthly)
web/          Next.js 15 app deployed by Vercel (scaffolded in Phase W1)
supabase/     SQL migrations, seed data, row-level security policies
data/         Local raw cache (gitignored) + the committed EIA↔ERCOT crosswalk CSV
docs/         Brief, build plan, ADRs, methodology
.github/      Monthly refresh workflow
```

## Working from this repo with Claude Code

The repo is the single source of truth so work can move between computers. On any machine:

1. `git clone https://github.com/cristobal-ai/komorebi-texas-screen.git`
2. Open the folder in Claude Code (Claude Desktop → Code → choose this folder, or `claude` in a terminal inside it).
3. Copy `pipeline/.env.example` → `pipeline/.env` and fill in the keys from your password manager. Never commit `.env`.
4. Start each session with `git pull`; end each session with a commit and `git push`.

Claude Code reads `CLAUDE.md` automatically, so the first prompt in a fresh session can be as short as "Read CLAUDE.md and the build plan, then continue with the next phase in Status."

## Running cost target

Vercel Pro $20/mo · Supabase $0–25/mo · GitHub Actions $0 (within free minutes) · data APIs $0.
