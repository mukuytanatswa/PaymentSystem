# SplytPayments Webhook Reference

SplytPayments receives webhooks from Stitch at `POST /api/v1/webhooks/stitch`.

---

## Signature Verification

Every request from Stitch includes an `x-stitch-signature` header. Verify it before processing:

```
x-stitch-signature: sha256=<hex-digest>
```

The digest is computed as HMAC-SHA256 of the raw request body, keyed with your `STITCH_WEBHOOK_SECRET`.

**Python**
```python
import hashlib
import hmac

def verify_stitch_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)
```

**JavaScript**
```javascript
const crypto = require("crypto");

function verifyStitchSignature(rawBody, signatureHeader, secret) {
  if (!signatureHeader.startsWith("sha256=")) return false;
  const expected = crypto
    .createHmac("sha256", secret)
    .update(rawBody)
    .digest("hex");
  const provided = signatureHeader.replace("sha256=", "");
  return crypto.timingSafeEqual(
    Buffer.from(expected, "hex"),
    Buffer.from(provided, "hex")
  );
}
```

Invalid signatures return **401 Unauthorized**.

---

## Webhook Types

### `payment_initiation_request.completed`

Fired when a customer completes payment via the Stitch checkout page.

SplytPayments automatically:
1. Marks the payment as `processing`
2. Disburses to each vendor
3. Marks the payment as `completed` (or `failed` if any payout fails)

**Payload**
```json
{
  "type": "payment_initiation_request.completed",
  "data": {
    "id": "stitch-payment-uuid",
    "amount": {
      "quantity": "500.00",
      "currency": "ZAR"
    },
    "status": "Completed",
    "payerReference": "platform-name-abc12345",
    "beneficiaryReference": "payment-uuid",
    "externalReference": "payment-uuid",
    "created": "2026-05-05T10:05:00.000Z",
    "updated": "2026-05-05T10:05:30.000Z"
  }
}
```

**Response from SplytPayments**
```json
{ "status": "completed" }
```

---

### `payment_initiation_request.failed`

Fired when the customer abandons or the payment fails.

SplytPayments marks the payment as `failed`. No payouts are made.

**Payload**
```json
{
  "type": "payment_initiation_request.failed",
  "data": {
    "id": "stitch-payment-uuid",
    "status": "Failed",
    "failureReason": "CustomerAbandoned"
  }
}
```

**Response from SplytPayments**
```json
{ "status": "marked_failed" }
```

---

### Unrecognised Types

Any other webhook type is acknowledged and ignored:

```json
{ "status": "ignored" }
```

---

## Idempotency

SplytPayments handles duplicate webhook delivery correctly:
- If the same `payment_initiation_request.completed` webhook is received twice, the second call returns `{ "status": "already_processed_or_not_found" }` without re-processing.
- Stitch retries webhooks with exponential backoff on non-2xx responses. SplytPayments always returns 2xx after signature verification to prevent retry loops.

---

## Replay Attack Protection

The `payment_initiation_request.completed` handler transitions the payment from `pending → processing` atomically. A second webhook for the same payment finds no row in `pending` state and returns `already_processed_or_not_found`.

---

## DLQ Alerts

If a payout fails after all retries (split reaches `dead` status), SplytPayments posts to `DLQ_ALERT_WEBHOOK_URL` with:

```json
{
  "event": "split.dead",
  "timestamp": "2026-05-05T23:00:00Z",
  "split_id": "...",
  "vendor_id": "...",
  "amount": "292.50"
}
```

When a refund is initiated or fails:

```json
{
  "event": "refund.initiated",
  "timestamp": "2026-05-05T12:00:00Z",
  "payment_id": "...",
  "refund_id": "...",
  "amount": "500.00",
  "initiated_by": "Platform Name"
}
```
