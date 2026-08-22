#!/usr/bin/env bash
# Fresh-clone smoke: the only environment that catches "works on our box" bugs.
# Clean venv, requirements.txt ONLY, app must start and render pages.
#
# Usage: bash scripts/fresh_clone_smoke.sh [branch]
# Exits non-zero on failure so it can gate a sweep.
set -u
BRANCH="${1:-main}"
WORK=$(mktemp -d)
PORT=8129
FAILED=0

note() { printf '  %s\n' "$1"; }
fail() { printf '  FAIL: %s\n' "$1"; FAILED=1; }

cleanup() {
  pkill -9 -f "uvicorn app.main.*--port $PORT" 2>/dev/null
  sleep 1
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "=== fresh-clone smoke ($BRANCH) ==="
git clone -q --branch "$BRANCH" https://github.com/jfilipATX/saronic-event-project.git "$WORK/repo" || {
  fail "clone failed"; exit 1; }
cd "$WORK/repo"
note "HEAD: $(git rev-parse --short HEAD)"

export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
  uv venv .venv --python 3.11 >/dev/null 2>&1
  VIRTUAL_ENV="$WORK/repo/.venv" uv pip install -q -r requirements.txt >/dev/null 2>&1
else
  python3 -m venv .venv >/dev/null 2>&1
  .venv/bin/pip install -q -r requirements.txt >/dev/null 2>&1
fi
[ -x .venv/bin/python ] || { fail "venv not created"; exit 1; }

# 1. every runtime import resolves from requirements.txt alone
MISSING=$(.venv/bin/python - <<'PY'
import importlib
missing = []
for mod in ("PIL", "qrcode", "fastapi", "jinja2", "uvicorn", "anthropic",
            "pptx", "multipart", "dotenv", "requests"):
    try:
        importlib.import_module(mod)
    except Exception:
        missing.append(mod)
print(",".join(missing))
PY
)
[ -z "$MISSING" ] && note "imports: all resolve" || fail "missing modules: $MISSING"

# 2. the app itself imports (module-level deps cascade into startup failure)
.venv/bin/python -c "import app.main" 2>/dev/null && note "app.main imports" \
  || fail "app.main does not import"

# 3. bundled assets survive a clone
for asset in assets/fonts/ArchivoExpanded-Bold.ttf \
             assets/press-kit/Images/Corsair/SAR_Corsair_Hero.png \
             app/ui/static/brand/logo-on-dark.png; do
  [ -f "$asset" ] || fail "missing tracked asset: $asset"
done
note "tracked assets present"

# 4. it actually serves pages
cp .env.example .env 2>/dev/null || true
DB_PATH="$WORK/smoke.db" .venv/bin/python -m uvicorn app.main:create_app --factory \
  --host 127.0.0.1 --port $PORT >"$WORK/server.log" 2>&1 &
for _ in $(seq 1 30); do
  sleep 1
  curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/" && break
done
B="http://127.0.0.1:$PORT"
HOME_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$B/")
if [ "$HOME_CODE" = "200" ]; then
  note "home: 200"
else
  fail "home: $HOME_CODE"
  tail -15 "$WORK/server.log" | sed 's/^/      /'
fi

if [ "$HOME_CODE" = "200" ]; then
  EID=$(curl -s -i -X POST -d "name=Smoke&city=Austin" "$B/events" \
        | grep -i '^location:' | grep -oE '[0-9]+' | head -1)
  for page in schedule visuals playbook checkin; do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 60 "$B/events/$EID/$page")
    [ "$CODE" = "200" ] && note "$page: 200" || fail "$page: $CODE"
  done
fi

# 5. the suite runs from the clone
.venv/bin/python -m pip install -q pytest httpx >/dev/null 2>&1 || true
RESULT=$(.venv/bin/python -m pytest tests/ -q 2>&1 | tail -1)
note "tests: $RESULT"

echo "=== $([ $FAILED -eq 0 ] && echo 'SMOKE PASSED' || echo 'SMOKE FAILED') ==="
exit $FAILED
