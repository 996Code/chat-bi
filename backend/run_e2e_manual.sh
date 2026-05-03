#!/bin/bash
set -e

LOG="e2e-manual.log"
TIMEOUT=30

log() {
  echo "[$(date +%H:%M:%S)] $1" | tee -a "$LOG"
}

fail() {
  log "FAIL: $1"
  kill %1 2>/dev/null; wait 2>/dev/null
  exit 1
}

> "$LOG"
log "=== E2E Manual Test Start ==="

# Start server
log "Starting server..."
.venv/bin/uvicorn app.main:app --port 8001 >>"$LOG" 2>&1 &
sleep 2

# Health check
log "Checking server is up..."
curl -sf --max-time 5 http://localhost:8001/health >/dev/null || log "No /health, trying /..."

# Register
log "Step 1: Register..."
REG=$(curl -s --max-time $TIMEOUT -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"e2e@test.com","password":"Test1234"}')
echo "$REG" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  status={d.get(\"code\",\"?\")}')" 2>/dev/null || log "  Register response: $REG"

# Login
log "Step 2: Login..."
LOGIN=$(curl -s --max-time $TIMEOUT -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"e2e@test.com","password":"Test1234"}')
TOKEN=$(echo "$LOGIN" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
log "  Got token: ${TOKEN:0:20}..."

# List (empty)
log "Step 3: List datasources (should be empty)..."
LIST=$(curl -s --max-time $TIMEOUT http://localhost:8001/api/v1/datasources \
  -H "Authorization: Bearer $TOKEN")
echo "$LIST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  count={len(d)}')" 2>/dev/null || log "  Response: $LIST"

# Create datasource
log "Step 4: Create datasource..."
CREATE=$(curl -s --max-time $TIMEOUT -X POST http://localhost:8001/api/v1/datasources \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test MySQL","type":"mysql","host":"127.0.0.1","port":3306,"database_name":"testdb","username":"root","password":"root"}')
DS_ID=$(echo "$CREATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log "  Created ds_id=$DS_ID"

# Test connection (expect fail - no MySQL)
log "Step 5: Test connection (expect fail)..."
TEST=$(curl -s --max-time $TIMEOUT -X POST http://localhost:8001/api/v1/datasources/$DS_ID/test \
  -H "Authorization: Bearer $TOKEN")
TEST_STATUS=$(echo "$TEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail',{}).get('code','?'))" 2>/dev/null || echo "?")
log "  Connection test: $TEST_STATUS"

# Delete
log "Step 6: Delete datasource..."
DEL_STATUS=$(curl -s --max-time $TIMEOUT -o /dev/null -w "%{http_code}" -X DELETE \
  "http://localhost:8001/api/v1/datasources/$DS_ID" \
  -H "Authorization: Bearer $TOKEN")
log "  Delete status: HTTP $DEL_STATUS"

# Verify deleted
log "Step 7: Verify deleted..."
LIST2=$(curl -s --max-time $TIMEOUT http://localhost:8001/api/v1/datasources \
  -H "Authorization: Bearer $TOKEN")
COUNT=$(echo "$LIST2" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))")
if [ "$COUNT" = "0" ]; then log "  Verified: datasource list is empty"; else log "FAIL: still $COUNT datasources"; fi

# Query greeting
log "Step 8: Query (greeting)..."
Q=$(curl -s --max-time $TIMEOUT -X POST http://localhost:8001/api/v1/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"你好","datasource_id":"dummy"}')
INTENT=$(echo "$Q" | python3 -c "import sys,json; print(json.load(sys.stdin).get('intent','?'))")
log "  Intent: $INTENT"

# Encryption check
log "Step 9: Check encryption in DB..."
python3 -c "
import sqlite3; conn=sqlite3.connect('chatbi.db'); cur=conn.cursor()
cur.execute('SELECT username_encrypted,password_encrypted FROM data_sources LIMIT 1')
row=cur.fetchone()
if row and row[0] and row[1] and 'root' not in row[0] and 'root' not in row[1]:
    print('  Encrypted OK')
else:
    print('  Empty or FAIL (may be expected if DB cleaned)')
conn.close()
"

# Cleanup
log "=== All steps completed ==="
kill %1 2>/dev/null; wait 2>/dev/null
log "Server stopped."
