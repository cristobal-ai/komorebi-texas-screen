# Accounts, keys and where each secret lives

No secret values in this file — only where to get them and where to put them. Verified 1 Sep 2026.

| Secret | Get it at | Notes | Put it in |
|---|---|---|---|
| `EIA_API_KEY` | https://www.eia.gov/opendata/register.php | Emailed within minutes. Passed in the URL (`?api_key=`). 5,000 rows/request (JSON); throttle or the key is auto-suspended. | `pipeline/.env`, GitHub Actions secret |
| `ERCOT_API_USERNAME` / `ERCOT_API_PASSWORD` | https://apiexplorer.ercot.com → Sign In / Sign Up → "Sign up now" → email verification code → password, display name, first/last name → Create | These are your API Explorer login. | `pipeline/.env`, GitHub Actions secret |
| `ERCOT_API_SUBSCRIPTION_KEY` | API Explorer → Products → "Public API" → subscription name "Public API" → Subscribe → Profile → "Show" next to Primary key | ID tokens (Azure B2C, client_id `fec253ea-0d06-4272-a5e6-b478baeecd70`) last 1 hour with no refresh; `gridstatus.ErcotAPI` handles this. Headers: `Authorization: Bearer <id_token>` + `Ocp-Apim-Subscription-Key`. History floor 2023-12-11. If sign-in silently fails, clear cache / new browser session, then open a support case (accounts have been left blocked after ERCOT-side incidents). | `pipeline/.env`, GitHub Actions secret |
| `NLR_API_KEY` | https://developer.nlr.gov/signup/ | NREL is now the National Laboratory of the Rockies; `developer.nrel.gov` retired 29 May 2026. 1,000 req/hour standard. NSRDB PSM v4 download: 2,000 req/day multi-site (emailed zip), 10,000/day single-point CSV. | `pipeline/.env`, GitHub Actions secret |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | https://supabase.com/dashboard → New project `komorebi-texas-screen`, region US East (N. Virginia) → Project Settings → API | Service-role key is server-only. | URL + service role: `pipeline/.env`, GitHub Actions secrets. URL + anon (+ service role): Vercel environment variables, `web/.env.local` |
| Vercel | https://vercel.com/dashboard → team "cristobal-ai's projects" → Settings → Billing → Upgrade to Pro ($20/mo) | Hobby is non-commercial by policy. No extra developer seats needed; outside users log in through the app. Link the GitHub repo; Root Directory `web`. | — |
| GitHub | https://github.com/cristobal-ai/komorebi-texas-screen → Settings → Secrets and variables → Actions | Add every `pipeline/.env` name as a repository secret. | — |

Per-machine: keep the filled `pipeline/.env` and `web/.env.local` in a password manager and recreate them on each computer. They are gitignored.
