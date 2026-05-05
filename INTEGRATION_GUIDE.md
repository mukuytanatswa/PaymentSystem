# SplytPayments Integration Guide

Interactive API docs (OpenAPI/Swagger UI) are available at `GET /docs` on any deployed instance.

---

## Authentication

All requests except `/health` and `/api/v1/webhooks/stitch` require an `x-api-key` header:

```
x-api-key: <your-platform-api-key>
```

Platform records are created manually in the database (see below). Admin endpoints additionally require `x-admin-api-key`.

---

## Step 0: Register Your Platform

This is a one-time manual database operation. Contact the SplytPayments team, or run directly against the database:

```sql
INSERT INTO platforms (name, api_key, fee_percentage)
VALUES ('Your Platform Name', 'your-secret-api-key', 2.50)
RETURNING id;
```

Store the `id` — it links all your vendors and payments.

---

## Step 1: Register a Vendor

Before accepting payments, each vendor must be registered and pass KYC.

**Python**
```python
import httpx

resp = httpx.post(
    "https://your-railway-url.railway.app/api/v1/vendors",
    headers={"x-api-key": "your-platform-api-key"},
    json={
        "name": "Vendor Trading CC",
        "bank_account": "62123456789",
        "bank_code": "632005",          # FNB
        "id_number": "8001015009087",   # South African ID
        "fee_percentage": 1.5           # Optional: overrides platform default
    },
)
vendor = resp.json()
print(vendor["id"], vendor["kyc_status"])  # "verified" or "rejected"
```

**JavaScript**
```javascript
const resp = await fetch("https://your-railway-url.railway.app/api/v1/vendors", {
  method: "POST",
  headers: {
    "x-api-key": "your-platform-api-key",
    "content-type": "application/json",
  },
  body: JSON.stringify({
    name: "Vendor Trading CC",
    bank_account: "62123456789",
    bank_code: "632005",
    id_number: "8001015009087",
    fee_percentage: 1.5,
  }),
});
const vendor = await resp.json();
console.log(vendor.id, vendor.kyc_status);
```

**Response**
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "platform_id": "...",
  "name": "Vendor Trading CC",
  "bank_account": "62123456789",
  "bank_code": "632005",
  "kyc_status": "verified",
  "balance": "0.00",
  "fee_percentage": "1.50",
  "created_at": "2026-05-05T10:00:00Z"
}
```

> Payment creation is blocked for vendors with `kyc_status != "verified"`.

---

## Step 2: Initiate a Payment with Splits

Use `Idempotency-Key` to safely retry without creating duplicate payments.

**Python**
```python
import uuid, httpx

resp = httpx.post(
    "https://your-railway-url.railway.app/api/v1/payments",
    headers={
        "x-api-key": "your-platform-api-key",
        "Idempotency-Key": str(uuid.uuid4()),  # unique per payment attempt
    },
    json={
        "total_amount": "500.00",
        "currency": "ZAR",
        "splits": [
            {"vendor_id": "3fa85f64-...", "gross_amount": "300.00"},
            {"vendor_id": "7cb92e11-...", "gross_amount": "200.00"},
        ],
    },
)
payment = resp.json()
print(payment["checkout_url"])  # redirect your customer here
```

**JavaScript**
```javascript
const resp = await fetch("https://your-railway-url.railway.app/api/v1/payments", {
  method: "POST",
  headers: {
    "x-api-key": "your-platform-api-key",
    "Idempotency-Key": crypto.randomUUID(),
    "content-type": "application/json",
  },
  body: JSON.stringify({
    total_amount: "500.00",
    currency: "ZAR",
    splits: [
      { vendor_id: "3fa85f64-...", gross_amount: "300.00" },
      { vendor_id: "7cb92e11-...", gross_amount: "200.00" },
    ],
  }),
});
const payment = await resp.json();
window.location.href = payment.checkout_url;
```

**Response**
```json
{
  "id": "a1b2c3d4-...",
  "platform_id": "...",
  "amount": "500.00",
  "currency": "ZAR",
  "status": "pending",
  "checkout_url": "https://checkout.stitch.money/...",
  "stitch_payment_id": "...",
  "splits": [
    {
      "id": "...",
      "vendor_id": "3fa85f64-...",
      "gross_amount": "300.00",
      "platform_fee": "7.50",
      "net_amount": "292.50",
      "status": "pending",
      "payout_id": null,
      "created_at": "2026-05-05T10:05:00Z"
    }
  ],
  "created_at": "2026-05-05T10:05:00Z"
}
```

Redirect or link the customer to `checkout_url` to complete payment via Stitch.

---

## Step 3: Handle Webhooks

Register your webhook endpoint at `POST https://your-railway-url.railway.app/api/v1/webhooks/stitch` on Stitch's dashboard. See [WEBHOOK_REFERENCE.md](WEBHOOK_REFERENCE.md) for full payload specs and signature verification.

After a successful customer payment, SplytPayments:
1. Receives the Stitch webhook
2. Disburses to each vendor automatically
3. Updates payment and split statuses

---

## Step 4: Query Payment Status

**Python**
```python
resp = httpx.get(
    "https://your-railway-url.railway.app/api/v1/payments/a1b2c3d4-...",
    headers={"x-api-key": "your-platform-api-key"},
)
payment = resp.json()
# payment["status"]: "pending" | "processing" | "completed" | "failed" | "refunded"
# payment["splits"][0]["status"]: "pending" | "paid" | "failed" | "dead"
```

**JavaScript**
```javascript
const resp = await fetch("https://your-railway-url.railway.app/api/v1/payments/a1b2c3d4-...", {
  headers: { "x-api-key": "your-platform-api-key" },
});
const payment = await resp.json();
```

### Payment Status Lifecycle

```
pending → processing → completed
                    ↘ failed
completed → refunded
```

---

## Step 5: Issue a Refund

Full refunds only. Reverses all vendor payouts via Stitch.

**Python**
```python
resp = httpx.post(
    "https://your-railway-url.railway.app/api/v1/payments/a1b2c3d4-.../refund",
    headers={"x-api-key": "your-platform-api-key"},
    json={"reason": "Customer requested cancellation"},
)
refund = resp.json()
# refund["status"]: "completed" | "failed"
```

**JavaScript**
```javascript
const resp = await fetch("https://your-railway-url.railway.app/api/v1/payments/a1b2c3d4-.../refund", {
  method: "POST",
  headers: {
    "x-api-key": "your-platform-api-key",
    "content-type": "application/json",
  },
  body: JSON.stringify({ reason: "Customer requested cancellation" }),
});
const refund = await resp.json();
```

> Refunds are only possible on payments with `status = "completed"`. Partial refunds are not supported.

---

## Idempotency-Key Usage

Supply a unique `Idempotency-Key` header on every `POST /api/v1/payments` request. If a request fails mid-flight and you retry with the same key, the original response is returned without creating a duplicate payment or calling Stitch again.

- Keys are scoped per platform — the same key on two different platforms creates two payments.
- Keys do not expire.
- Use a UUID (v4) generated fresh for each new payment attempt.

---

## Common Error Codes

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid `x-api-key` |
| 403 | Invalid `x-admin-api-key` |
| 404 | Vendor or payment not found for your platform |
| 422 | Validation error (e.g. vendor KYC not verified, payment not completed for refund) |
| 429 | Rate limit exceeded (100 req / 15 min per IP) |
| 500 | Unexpected server error (check Sentry) |
