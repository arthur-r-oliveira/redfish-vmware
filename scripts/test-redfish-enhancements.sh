#!/usr/bin/env bash
#
# Curl tests for Redfish-VMware (IPI/Metal3 compatibility):
#   - Public GETs (Systems, trailing slash, query string)
#   - Session create + DELETE with/without auth
#   - Protected endpoints (401)
#   - Power status GET, Power off, Power on
#   - Boot from Cd (PATCH)
#   - VirtualMedia InsertMedia / EjectMedia (optional if REDFISH_ISO_URL set)
#
# Usage:
#   ./scripts/test-redfish-enhancements.sh [BASE_URL] [VM_NAME]
#   Default: BASE_URL=http://localhost:8440  VM_NAME=arolivei-ocp-tnf-a
#
# Optional env:
#   REDFISH_ISO_URL           - Full HTTP(S) URL to pull the ISO from (server then pushes to VMware datastore)
#   REDFISH_USER              - Basic auth user (default: admin)
#   REDFISH_PASSWORD          - Basic auth password (default: password)
#   REDFISH_VIRTUAL_MEDIA_DS  - VMware datastore for upload (display only; server uses config.json)
#   REDFISH_VIRTUAL_MEDIA_DIR - Folder on datastore (display only; server uses config.json)
#
# For InsertMedia to push the ISO to [isos] arolivei/, the server config.json must have for this VM:
#   "virtual_media_datastore": "isos", "virtual_media_folder": "arolivei"
#
# Example (from repo root):
#   REDFISH_ISO_URL=http://10.10.74.222/rhel-9.6-x86_64-boot.iso REDFISH_USER=admin REDFISH_PASSWORD=password \
#     ./scripts/test-redfish-enhancements.sh https://10.10.74.222:8440 arolivei-ocp-tnf-a
#

set -e

BASE_URL="${1:-http://localhost:8440}"
VM_NAME="${2:-arolivei-ocp-tnf-a}"
USER="${REDFISH_USER:-admin}"
PASS="${REDFISH_PASSWORD:-password}"

# Use -k for HTTPS when REDFISH_INSECURE=1 or when base URL is https (e.g. lab with self-signed cert)
CURL_INSECURE=""
[[ "$REDFISH_INSECURE" == "1" || "$BASE_URL" == https* ]] && CURL_INSECURE="-k"

# Strip trailing slash from base URL
BASE_URL="${BASE_URL%/}"
REDFISH_BASE="${BASE_URL}/redfish/v1"
MANAGER_ID="${VM_NAME}-bmc"

# Curl with Basic auth for protected endpoints
curl_auth() { curl -s $CURL_INSECURE -u "$USER:$PASS" "$@"; }

echo "=============================================="
echo "Redfish enhancement tests"
echo "  BASE_URL: $BASE_URL"
echo "  VM_NAME:  $VM_NAME"
echo "  User:     $USER"
if [[ -n "${REDFISH_ISO_URL:-}" ]]; then
  echo "  ISO source (pull): $REDFISH_ISO_URL"
fi
if [[ -n "${REDFISH_VIRTUAL_MEDIA_DS:-}" || -n "${REDFISH_VIRTUAL_MEDIA_DIR:-}" ]]; then
  echo "  VMware datastore (push): datastore=${REDFISH_VIRTUAL_MEDIA_DS:-<default>}, folder=${REDFISH_VIRTUAL_MEDIA_DIR:-<default>}"
fi
echo "=============================================="

pass_count=0
fail_count=0

assert_status() {
  local name="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    echo "  ✅ $name (HTTP $actual)"
    ((pass_count++)) || true
    return 0
  else
    echo "  ❌ $name (expected $expected, got $actual)"
    ((fail_count++)) || true
    return 1
  fi
}

# ----- Public GETs (no auth) -----
echo ""
echo "--- 1. Public GETs (no auth) ---"

code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' "$REDFISH_BASE/")
assert_status "GET /redfish/v1/ (no auth)" "200" "$code"

code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' "$REDFISH_BASE/Systems")
assert_status "GET /redfish/v1/Systems (no auth)" "200" "$code"

code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' "$REDFISH_BASE/Systems/")
assert_status "GET /redfish/v1/Systems/ (no auth, trailing slash)" "200" "$code"

code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' "$REDFISH_BASE/Systems/$VM_NAME")
assert_status "GET /redfish/v1/Systems/$VM_NAME (no auth)" "200" "$code"

code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' "$REDFISH_BASE/Systems/$VM_NAME?foo=bar")
assert_status "GET /redfish/v1/Systems/$VM_NAME?foo=bar (no auth, query string)" "200" "$code"

# ----- Session: create then DELETE without auth -----
echo ""
echo "--- 2. Session create + DELETE without auth ---"

create_resp=$(curl -s $CURL_INSECURE -w '\n%{http_code}' -X POST "$REDFISH_BASE/SessionService/Sessions" \
  -H "Content-Type: application/json" \
  -d "{\"UserName\":\"$USER\",\"Password\":\"$PASS\"}")

body=$(echo "$create_resp" | head -n -1)
code=$(echo "$create_resp" | tail -n 1)
assert_status "POST SessionService/Sessions (create session)" "201" "$code"

session_id=""
if command -v python3 &>/dev/null; then
  session_id=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Id',''))" 2>/dev/null || true)
fi
if [[ -z "$session_id" ]]; then
  # Fallback: Location header would have it; from body we need Id
  session_id=$(echo "$body" | grep -o '"Id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)".*/\1/')
fi

if [[ -z "$session_id" ]]; then
  echo "  ⚠️  Could not extract session Id from response; skipping DELETE test"
else
  # DELETE without any auth header (enhancement: session ID in URL is sufficient)
  code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' -X DELETE "$REDFISH_BASE/SessionService/Sessions/$session_id")
  assert_status "DELETE SessionService/Sessions/<id> (no auth)" "204" "$code"
fi

# ----- Session: create then DELETE with X-Auth-Token (optional check) -----
echo ""
echo "--- 3. Session create + DELETE with X-Auth-Token ---"

tmp_headers=$(mktemp)
tmp_body=$(mktemp)
trap "rm -f $tmp_headers $tmp_body" EXIT

curl -s $CURL_INSECURE -D "$tmp_headers" -o "$tmp_body" -X POST "$REDFISH_BASE/SessionService/Sessions" \
  -H "Content-Type: application/json" \
  -d "{\"UserName\":\"$USER\",\"Password\":\"$PASS\"}"

token=$(grep -i "^X-Auth-Token:" "$tmp_headers" 2>/dev/null | sed 's/^X-Auth-Token:[[:space:]]*//' | tr -d '\r')
session_id=""
if [[ -s "$tmp_body" ]]; then
  session_id=$(python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Id',''))" < "$tmp_body" 2>/dev/null || true)
fi
[[ -z "$session_id" ]] && session_id=$(grep -o '"Id"[[:space:]]*:[[:space:]]*"[^"]*"' "$tmp_body" 2>/dev/null | head -1 | sed 's/.*"\([^"]*\)".*/\1/')

if [[ -n "$token" && -n "$session_id" ]]; then
  code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' -X DELETE "$REDFISH_BASE/SessionService/Sessions/$session_id" \
    -H "X-Auth-Token: $token")
  assert_status "DELETE SessionService/Sessions/<id> (with X-Auth-Token)" "204" "$code"
else
  echo "  ⚠️  No token or Id; skipping"
fi

# ----- Protected endpoint still requires auth -----
echo ""
echo "--- 4. Protected GETs (expect 401 without auth) ---"

code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' "$REDFISH_BASE/Managers")
assert_status "GET /redfish/v1/Managers (no auth → 401)" "401" "$code"

code=$(curl -s $CURL_INSECURE -o /dev/null -w '%{http_code}' "$REDFISH_BASE/UpdateService")
assert_status "GET /redfish/v1/UpdateService (no auth → 401)" "401" "$code"

# ----- 5. Power status GET -----
echo ""
echo "--- 5. Power status (GET Systems) ---"

sys_body=$(mktemp)
trap "rm -f $tmp_headers $tmp_body $sys_body" EXIT
code=$(curl -s $CURL_INSECURE -o "$sys_body" -w '%{http_code}' "$REDFISH_BASE/Systems/$VM_NAME")
assert_status "GET /redfish/v1/Systems/$VM_NAME (power state)" "200" "$code"
power_state=""
if [[ -s "$sys_body" ]]; then
  power_state=$(python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('PowerState',''))" < "$sys_body" 2>/dev/null || true)
fi
if [[ -n "$power_state" ]]; then
  echo "  ✅ PowerState = $power_state"
else
  echo "  ⚠️  PowerState not found in response (GET still 200)"
fi

# ----- 6. Boot from Cd (PATCH) - set before insert + power on -----
echo ""
echo "--- 6. Boot from Cd (PATCH Boot) ---"
code=$(curl_auth -o /dev/null -w '%{http_code}' -X PATCH "$REDFISH_BASE/Systems/$VM_NAME" \
  -H "Content-Type: application/json" \
  -d '{"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}}')
assert_status "PATCH Systems Boot BootSourceOverrideTarget=Cd" "200" "$code"

# ----- 7. VirtualMedia InsertMedia (optional; must run before power on) -----
echo ""
echo "--- 7. VirtualMedia InsertMedia (optional, before power on) ---"
insert_ok=false
if [[ -n "${REDFISH_ISO_URL:-}" ]]; then
  code=$(curl_auth -o /dev/null -w '%{http_code}' -X POST "$REDFISH_BASE/Managers/$MANAGER_ID/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia" \
    -H "Content-Type: application/json" \
    -d "{\"Image\": \"$REDFISH_ISO_URL\", \"WriteProtected\": true}")
  if [[ "$code" == "204" ]]; then
    echo "  ✅ POST VirtualMedia.InsertMedia (HTTP 204)"
    ((pass_count++)) || true
    insert_ok=true
  else
    echo "  ❌ POST VirtualMedia.InsertMedia (expected 204, got $code) - continuing anyway"
    ((fail_count++)) || true
  fi
else
  echo "  ⏭️  Skipped (set REDFISH_ISO_URL to test InsertMedia)"
fi

# ----- 8. Wait after insert (so media is ready before power on) -----
if [[ "$insert_ok" == true ]]; then
  echo ""
  echo "--- 8. Wait 10s after InsertMedia ---"
  sleep 10
  echo "  ✅ Done"
fi

# ----- 9. Power on -----
echo ""
echo "--- 9. Power on (On) ---"
code=$(curl_auth -o /dev/null -w '%{http_code}' -X POST "$REDFISH_BASE/Systems/$VM_NAME/Actions/ComputerSystem.Reset" \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "On"}')
assert_status "POST ComputerSystem.Reset On" "204" "$code"

# ----- 10. Wait 15s then power off -----
echo ""
echo "--- 10. Wait 15s then Power off (ForceOff) ---"
sleep 15
code=$(curl_auth -o /dev/null -w '%{http_code}' -X POST "$REDFISH_BASE/Systems/$VM_NAME/Actions/ComputerSystem.Reset" \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "ForceOff"}')
assert_status "POST ComputerSystem.Reset ForceOff" "204" "$code"

# ----- 11. VirtualMedia EjectMedia -----
echo ""
echo "--- 11. VirtualMedia EjectMedia ---"
code=$(curl_auth -o /dev/null -w '%{http_code}' -X POST "$REDFISH_BASE/Managers/$MANAGER_ID/VirtualMedia/CD/Actions/VirtualMedia.EjectMedia" \
  -H "Content-Type: application/json" \
  -d '{}')
assert_status "POST VirtualMedia.EjectMedia" "204" "$code"

# ----- Summary -----
echo ""
echo "=============================================="
echo "Results: $pass_count passed, $fail_count failed"
echo "=============================================="
[[ $fail_count -eq 0 ]]
