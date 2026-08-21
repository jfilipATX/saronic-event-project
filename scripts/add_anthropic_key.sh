#!/usr/bin/env bash
# add_anthropic_key.sh — interactive, safe entry of the Anthropic API key.
# Run from anywhere:  bash /home/hermes/saronic-event-tool/scripts/add_anthropic_key.sh
# The key is prompted with no echo, never appears on the command line,
# never lands in shell history, and is written only to the gitignored .env.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi

read -r -s -p "Paste Anthropic API key (input hidden), then press Enter: " KEY
echo
if [ -z "${KEY}" ]; then
  echo "ERROR: empty input — nothing written. Run the script again." >&2
  exit 1
fi
case "$KEY" in
  sk-ant-*) : ;;
  *) echo "WARNING: key does not start with 'sk-ant-' — writing anyway, but double-check it." ;;
esac

# Remove any existing ANTHROPIC_API_KEY lines, then write the new one.
sed -i '/^ANTHROPIC_API_KEY=/d' .env
printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" >> .env
unset KEY

# Flip real mode on (edit in place; no duplicate entries).
if grep -q '^USE_REAL_CLAUDE=' .env; then
  sed -i 's/^USE_REAL_CLAUDE=.*/USE_REAL_CLAUDE=1/' .env
else
  printf 'USE_REAL_CLAUDE=1\n' >> .env
fi

chmod 600 .env

# Verify without revealing anything.
LEN=$(awk -F= '/^ANTHROPIC_API_KEY=/{print length($2)}' .env)
echo "OK: ANTHROPIC_API_KEY written (length ${LEN}), USE_REAL_CLAUDE=1, .env is chmod 600."
echo "Now say 'done' in the chat."
