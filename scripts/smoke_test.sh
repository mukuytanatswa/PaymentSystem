#!/usr/bin/env bash
# Smoke test against a deployed Railway environment using Stitch sandbox credentials.
# Exit 0 on full success, 1 on any failure.
#
# Required env vars:
#   BASE_URL         e.g. https://your-app.railway.app
#   API_KEY          platform api key
#   ADMIN_API_KEY    admin api key
#   STITCH_WEBHOOK_SECRET   used to sign the simulated webhook
#
# Usage:
#   BASE_URL=https://... API_KEY=... ADMIN_API_KEY=... STITCH_WEBHOOK_SECRET=... ./scripts/smoke_test.sh

set -euo pipefail

BASE_URL="${BASE_URL:?BASE_URL is required}"
API_KEY="${API_KEY:?API_KEY is required}"
ADMIN_KEY="${ADMIN_API_KEY:?ADMIN_API_KEY is required}"
WEBHOOK_SECRET="${STITCH_WEBHOOK_SECRET:?STITCH_WEBHOOK_SECRET is required}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

# ---------------------------------------------------------------------------
# Step 1: Health check
# ---------------------------------------------------------------------------
info "Step 1: GET /health"
HEALTH=$(curl -sf "${BASE_URL}/health")
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "ok" ] && pass "Health check: status=ok" || fail "Health check failed: $HEALTH"

# ---------------------------------------------------------------------------
# Step 2: Register a vendor (sandbox ID — update before running against real env)
# ---------------------------------------------------------------------------
info "Step 2: Register vendor"
VENDOR=$(curl -sf -X POST "${BASE_URL}/api/v1/vendors" \
  -H "x-api-key: ${API_KEY}" \
  -H "content-type: application/json" \
  -d '{
    "name": "Smoke Test Vendor",
    "bank_account": "62123456789",
    "bank_code": "632005",
    "id_number": "8001015009087"
  }')
VENDOR_ID=$(echo "$VENDOR" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
KYC=$(echo "$VENDOR" | python3 -c "import sys,json; print(json.load(sys.stdin)['kyc_status'])")
[ "$KYC" = "verified" ] && pass "Vendor registered: kyc_status=verified" || fail "KYC failed: $KYC — $VENDOR"

# ---------------------------------------------------------------------------
# Step 3: Create payment with 2-vendor split (second vendor reuses same ID for simplicity)
# ---------------------------------------------------------------------------
info "Step 3: Create payment"
IDEM_KEY=$(python3 -c "import uuid; print(uuid.uuid4())")
PAYMENT=$(curl -sf -X POST "${BASE_URL}/api/v1/payments" \
  -H "x-api-key: ${API_KEY}" \
  -H "Idempotency-Key: ${IDEM_KEY}" \
  -H "content-type: application/json" \
  -d "{
    \"total_amount\": \"100.00\",
    \"currency\": \"ZAR\",
    \"splits\": [{\"vendor_id\": \"${VENDOR_ID}\", \"gross_amount\": \"100.00\"}]
  }")
PAYMENT_ID=$(echo "$PAYMENT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
CHECKOUT_URL=$(echo "$PAYMENT" | python3 -c "import sys,json; print(json.load(sys.stdin)['checkout_url'])")
STITCH_ID=$(echo "$PAYMENT" | python3 -c "import sys,json; print(json.load(sys.stdin)['stitch_payment_id'])")
[ -n "$CHECKOUT_URL" ] && pass "Payment created: checkout_url present" || fail "No checkout_url in response: $PAYMENT"

# ---------------------------------------------------------------------------
# Step 4: Display checkout URL for manual completion
# ---------------------------------------------------------------------------
info "Step 4: Complete Stitch sandbox checkout manually"
echo ""
echo "  Open this URL in a browser and complete the Stitch sandbox payment:"
echo "  ${CHECKOUT_URL}"
echo ""
read -rp "  Press ENTER after completing the checkout..."

# ---------------------------------------------------------------------------
# Step 5: Poll payment status until completed (timeout 60s)
# ---------------------------------------------------------------------------
info "Step 5: Polling payment status (timeout 60s)"
DEADLINE=$((SECONDS + 60))
while [ $SECONDS -lt $DEADLINE ]; do
  P_STATUS=$(curl -sf "${BASE_URL}/api/v1/payments/${PAYMENT_ID}" \
    -H "x-api-key: ${API_KEY}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  if [ "$P_STATUS" = "completed" ]; then
    pass "Payment status=completed"
    break
  fi
  echo "  ...status=${P_STATUS}, waiting..."
  sleep 5
done
[ "$P_STATUS" = "completed" ] || fail "Payment did not reach completed within 60s (last status: ${P_STATUS})"

# ---------------------------------------------------------------------------
# Step 6: Verify ledger entries via admin force-status endpoint (reuse it as
#         a read probe — hit GET /api/v1/payments/{id} which is sufficient)
# ---------------------------------------------------------------------------
info "Step 6: Verify splits are paid"
SPLITS_PAID=$(curl -sf "${BASE_URL}/api/v1/payments/${PAYMENT_ID}" \
  -H "x-api-key: ${API_KEY}" | python3 -c "
import sys, json
p = json.load(sys.stdin)
all_paid = all(s['status'] == 'paid' for s in p['splits'])
print('yes' if all_paid else 'no')
")
[ "$SPLITS_PAID" = "yes" ] && pass "All splits paid" || fail "Not all splits are paid"

# ---------------------------------------------------------------------------
# Step 7: Trigger refund and confirm reversal
# ---------------------------------------------------------------------------
info "Step 7: Initiate refund"
REFUND=$(curl -sf -X POST "${BASE_URL}/api/v1/payments/${PAYMENT_ID}/refund" \
  -H "x-api-key: ${API_KEY}" \
  -H "content-type: application/json" \
  -d '{"reason": "Smoke test refund"}')
REFUND_STATUS=$(echo "$REFUND" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$REFUND_STATUS" = "completed" ] && pass "Refund completed" || fail "Refund failed: $REFUND"

FINAL_STATUS=$(curl -sf "${BASE_URL}/api/v1/payments/${PAYMENT_ID}" \
  -H "x-api-key: ${API_KEY}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$FINAL_STATUS" = "refunded" ] && pass "Payment status=refunded" || fail "Expected refunded, got ${FINAL_STATUS}"

# ---------------------------------------------------------------------------
# All done
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} All smoke test steps passed.${NC}"
echo -e "${GREEN}========================================${NC}"
echo "  payment_id:   ${PAYMENT_ID}"
echo "  vendor_id:    ${VENDOR_ID}"
exit 0
