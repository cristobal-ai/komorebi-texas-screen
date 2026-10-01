"""Phase 0 check: which secrets are set in pipeline/.env (or the environment), optionally verified live.

    python -m pipeline.check_env          # set / missing only
    python -m pipeline.check_env --live   # also call EIA, ERCOT and Supabase with the keys

Never prints a secret value.
"""
from __future__ import annotations

import argparse
import os

import requests

from pipeline.common import PIPELINE_DIR, load_env

REQUIRED = [
    "EIA_API_KEY",
    "ERCOT_API_USERNAME",
    "ERCOT_API_PASSWORD",
    "ERCOT_API_SUBSCRIPTION_KEY",
    "NLR_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
]
# Old names still honoured so an existing .env keeps working: new name → legacy name
ALIASES = {"SUPABASE_SECRET_KEY": "SUPABASE_SERVICE_ROLE_KEY"}
OPTIONAL = ["SLACK_WEBHOOK_URL"]

ERCOT_TOKEN_URL = (
    "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)
ERCOT_CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"


def _eia(env) -> str:
    r = requests.get("https://api.eia.gov/v2/electricity/", params={"api_key": env["EIA_API_KEY"]}, timeout=30)
    return "ok" if r.ok else f"HTTP {r.status_code}"


def _ercot(env) -> str:
    r = requests.post(
        ERCOT_TOKEN_URL,
        data={
            "username": env["ERCOT_API_USERNAME"],
            "password": env["ERCOT_API_PASSWORD"],
            "grant_type": "password",
            "scope": f"openid {ERCOT_CLIENT_ID} offline_access",
            "client_id": ERCOT_CLIENT_ID,
            "response_type": "id_token",
        },
        timeout=30,
    )
    if not r.ok or "id_token" not in r.json():
        return f"token HTTP {r.status_code}"
    token = r.json()["id_token"]
    r = requests.get(
        "https://api.ercot.com/api/public-reports",
        headers={"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": env["ERCOT_API_SUBSCRIPTION_KEY"]},
        timeout=30,
    )
    return "ok" if r.ok else f"token ok, API HTTP {r.status_code} (check subscription key)"


def _supabase(env) -> str:
    from pipeline.load_supabase import headers as sb_headers

    key = env["SUPABASE_SECRET_KEY"]
    headers = sb_headers(key)
    r = requests.get(f"{env['SUPABASE_URL'].rstrip('/')}/rest/v1/", headers=headers, timeout=30)
    if r.ok:
        return "ok" if key.startswith("sb_secret_") or not key.startswith("sb_") else "ok (but this is not a sb_secret_ key)"
    return f"HTTP {r.status_code}"


LIVE = {
    "EIA_API_KEY": _eia,
    "ERCOT_API_SUBSCRIPTION_KEY": _ercot,
    "SUPABASE_SECRET_KEY": _supabase,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    env_file = PIPELINE_DIR / ".env"
    print(f"pipeline/.env: {'found' if env_file.exists() else 'NOT FOUND (using process environment only)'}")
    load_env()
    env = {k: os.environ.get(k, "").strip() for k in REQUIRED + OPTIONAL}
    via_alias = set()
    for new, old in ALIASES.items():
        if not env[new] and os.environ.get(old, "").strip():
            env[new] = os.environ[old].strip()
            via_alias.add(new)
    missing = 0
    for k in REQUIRED + OPTIONAL:
        state = "set" if env[k] else ("missing" if k in REQUIRED else "missing (optional)")
        if k in via_alias:
            state += f" (via legacy name {ALIASES[k]})"
        missing += k in REQUIRED and not env[k]
        live = ""
        if args.live and k in LIVE:
            deps = {"ERCOT_API_SUBSCRIPTION_KEY": ["ERCOT_API_USERNAME", "ERCOT_API_PASSWORD"],
                    "SUPABASE_SECRET_KEY": ["SUPABASE_URL"]}.get(k, [])
            if all(env[d] for d in deps + [k]):
                try:
                    live = f"  live: {LIVE[k](env)}"
                except requests.RequestException as e:
                    live = f"  live: {type(e).__name__}"
        print(f"  {k:<28} {state}{live}")
    print(f"{len(REQUIRED) - missing}/{len(REQUIRED)} required secrets set")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
