#!/usr/bin/env bash
# rotate_pexels_key.sh — interactive, safe replacement of the Pexels API key.
#
#   bash /home/hermes/saronic-event-tool/scripts/rotate_pexels_key.sh
#
# The key is prompted with no echo, never appears on the command line, never
# lands in shell history, and is written only to the gitignored .env.
#
# Rotation-specific behaviour: any existing PEXELS_API_KEY lines are REMOVED
# before the new one is written. Appending would leave the old (exposed) key in
# the file, and whichever line won would be a coin toss.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi

OLD_COUNT=$(grep -c '^PEXELS_API_KEY=' .env || true)

read -r -s -p "Paste the NEW Pexels API key (input hidden), then press Enter: " KEY
echo
if [ -z "${KEY}" ]; then
  echo "ERROR: empty input — nothing changed. Run the script again." >&2
  exit 1
fi

# Guard against pasting the old, exposed key back in.
if grep -qF "PEXELS_API_KEY=${KEY}" .env 2>/dev/null; then
  echo "ERROR: that is the key already in .env — regenerate a NEW one at" >&2
  echo "       https://www.pexels.com/api/ first, then re-run this script." >&2
  unset KEY
  exit 1
fi

# Remove every old entry, then write exactly one new line.
sed -i '/^PEXELS_API_KEY=/d' .env
printf 'PEXELS_API_KEY=%s\n' "$KEY" >> .env
unset KEY

chmod 600 .env

NEW_COUNT=$(grep -c '^PEXELS_API_KEY=' .env || true)
LEN=$(awk -F= '/^PEXELS_API_KEY=/{print length($2)}' .env)
echo "OK: replaced ${OLD_COUNT} old entry/entries with ${NEW_COUNT} (length ${LEN}); .env is chmod 600."
echo
echo "Verifying against the live Pexels API…"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from app.config import load_config
from app.providers.real.providers import RealImageProvider

cfg = load_config()
results = RealImageProvider(cfg).fetch("Austin", "city-stock", limit=2)
if results:
    print(f"OK: live call returned {len(results)} image(s). The new key works.")
    for a in results:
        print("   ", a.url[:80])
else:
    print("WARNING: the live call returned nothing. The key may be wrong or not yet")
    print("         active. Check https://www.pexels.com/api/ and re-run if needed.")
    sys.exit(1)
PYEOF
echo
echo "Rotation complete. Say 'done' in the chat."
