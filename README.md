# SplytPayments

A multi-vendor payment split gateway built on FastAPI + PostgreSQL + Stitch.

---

## Railway Deployment

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (Railway provides this automatically) |
| `STITCH_CLIENT_ID` | Stitch OAuth client ID |
| `STITCH_CLIENT_SECRET` | Stitch OAuth client secret |
| `STITCH_REDIRECT_URI` | Stitch OAuth redirect URI |
| `STITCH_API_URL` | Stitch GraphQL endpoint (default: `https://api.stitch.money/graphql`) |
| `STITCH_WEBHOOK_SECRET` | Secret used to verify HMAC-SHA256 webhook signatures |
| `SMILE_IDENTITY_API_KEY` | Smile Identity API key for KYC |
| `SMILE_IDENTITY_PARTNER_ID` | Smile Identity partner ID |
| `SMILE_IDENTITY_BASE_URL` | Smile Identity base URL (default: `https://api.smileidentity.com`) |
| `ADMIN_API_KEY` | Secret for admin-only endpoints |
| `ENCRYPTION_KEY` | Fernet key for encrypting bank account numbers at rest |
| `ENVIRONMENT` | `production`, `staging`, or `development` |
| `DLQ_ALERT_WEBHOOK_URL` | Webhook URL to receive dead-letter queue alerts (optional) |
| `SENTRY_DSN` | Sentry DSN for error tracking (optional) |

### Running Migrations

After provisioning the Railway Postgres database, run migrations once:

```bash
DATABASE_URL=<your-railway-db-url> python scripts/run_migrations.py
```

Railway also supports a one-off command via the dashboard: **Settings → Deploy → Start Command** can be temporarily set to `python scripts/run_migrations.py` for the initial migration run, then restored to the default.

---

## Uptime Monitoring

Configure an external monitor (Better Stack or UptimeRobot) to `GET /health` every 60 seconds. The endpoint returns `200 OK` when all services are healthy, `503` when degraded. Alert on any non-200 response.

---

## Railway Postgres Backup Verification

1. **View backup schedule**: Railway Dashboard → your Postgres service → **Backups** tab. Backups run daily by default.
2. **Test restore**: In the Backups tab, select a backup and click **Restore**. Railway creates a new database instance from the backup — connect to it and verify table row counts match expectations before promoting.
3. Perform a test restore at least once before going live, and again after any schema migration.

---

## Health Check

`GET /health` returns the status of the database, Stitch reachability, and all background workers. Railway uses `/health` as the deployment healthcheck before routing traffic.
