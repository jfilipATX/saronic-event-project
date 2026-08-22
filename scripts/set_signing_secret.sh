#!/usr/bin/env bash
# set_signing_secret.sh — set EVENT_SIGNING_SECRET in .env to a random value.
# Run:  bash scripts/set_signing_secret.sh
# Idempotent-safe: refuses to overwrite an existing non-empty secret unless --force.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi

CURRENT=$(grep '^EVENT_SIGNING_SECRET=' .env | head -1 | cut -d= -f2- || true)
if [ -n "$CURRENT" ] && [ "${1:-}" != "--force" ]; then
  echo "EVENT_SIGNING_SECRET is already set (${#CURRENT} chars). Re-run with --force to replace it."
  echo "WARNING: replacing it invalidates every QR invite already issued."
  exit 0
fi

SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
if grep -q '^EVENT_SIGNING_SECRET=' .env; then
  # portable in-place edit (BSD/macOS sed needs a suffix arg; use python for portability)
  python3 - "$SECRET" <<'EOF'
import re, sys, pathlib
secret = sys.argv[1]
p = pathlib.Path('.env')
t = p.read_text()
t = re.sub(r'^EVENT_SIGNING_SECRET=.*$', f'EVENT_SIGNING_SECRET={secret}', t, count=1, flags=re.M)
p.write_text(t)
EOF
else
  printf 'EVENT_SIGNING_SECRET=%s\n' "$SECRET" >> .env
fi
chmod 600 .env
LEN=$(grep '^EVENT_SIGNING_SECRET=' .env | head -1 | cut -d= -f2- | wc -c)
echo "OK: EVENT_SIGNING_SECRET set (64 hex chars). QR invites now survive server restarts."
