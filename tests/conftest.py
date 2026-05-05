import os

# Stub required env vars before any app module is imported.
# Settings() is instantiated at module level in app/config/env.py so these
# must be set before the first import of any app code.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("STITCH_CLIENT_ID", "test-client-id")
os.environ.setdefault("STITCH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("STITCH_REDIRECT_URI", "http://localhost/callback")
os.environ.setdefault("SMILE_IDENTITY_API_KEY", "test-smile-key")
os.environ.setdefault("SMILE_IDENTITY_PARTNER_ID", "test-partner-id")
os.environ.setdefault("SMILE_IDENTITY_BASE_URL", "https://testapi.smileidentity.com")
os.environ.setdefault("STITCH_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("ENCRYPTION_KEY", "OnboAscPWzVYooYIRHYORVi2OrzSfi1KnicJGYKomrY=")
