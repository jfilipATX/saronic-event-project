#!/usr/bin/env bash
# Seeds an event + roster and leaves a server running for design QA.
# Usage: bash scripts/seed_qa_desk.sh [PORT]
# Stop it with: bash scripts/stop_qa.sh [PORT]
set -u
PORT="${1:-8737}"
cd /home/hermes/saronic-event-tool
DB="/tmp/qa-desk-$PORT.db"
rm -f "$DB"
export EVENT_SIGNING_SECRET="qa-desk-secret"
DB_PATH="$DB" .venv/bin/python -m uvicorn app.main:create_app \
    --factory --host 127.0.0.1 --port "$PORT" > "/tmp/qa-desk-$PORT.log" 2>&1 &
for _ in $(seq 1 20); do
  sleep 1
  curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/" && break
done
B="http://127.0.0.1:$PORT"
EID=$(curl -s -i -X POST -d "name=Fleet Week 2026&city=Austin" "$B/events" \
      | grep -i '^location:' | grep -oE '[0-9]+' | head -1)
curl -s -X POST -d "step=event_type&key=convention" "$B/events/$EID/decide" -o /dev/null
curl -s -X POST -d "step=audience&key=baseline" "$B/events/$EID/decide" -o /dev/null
curl -s -X POST -d "step=venue&key=austin-convention-center" "$B/events/$EID/decide" -o /dev/null
CSV="/tmp/qa-roster-$PORT.csv"
printf 'Full Name,Email Address,Job Title,Company,VIP\n' > "$CSV"
printf 'Dana Reyes,dana@example.com,Program Director,Saronic,yes\n' >> "$CSV"
printf 'Sam Okoye,sam@example.com,Naval Architect,Damen,no\n' >> "$CSV"
printf 'Ada Fournier,ada@example.com,Fleet Ops Lead,Navantia,\n' >> "$CSV"
curl -s -X POST "$B/events/$EID/roster/import" --data-urlencode "csv_text@$CSV" \
  -d "map_Full Name=full_name" -d "map_Email Address=email" \
  -d "map_Job Title=title" -d "map_Company=company" -d "map_VIP=vip" -o /dev/null
echo "QA server ready on $B"
echo
echo "  event:        $B/events/$EID/checkin"
echo "  roster:       $B/events/$EID/roster"
echo
echo "To see each desk state, POST then screenshot the response:"
echo "  VIP banner:      curl -s -X POST -d 'email=dana@example.com' $B/events/$EID/checkin/email"
echo "  unknown email:   curl -s -X POST -d 'email=nobody@example.com' $B/events/$EID/checkin/email"
echo "  walk-in note:    curl -s -X POST -d 'full_name=Half Person' $B/events/$EID/checkin/walkin"
echo
echo "Stop with: bash scripts/stop_qa.sh $PORT"
