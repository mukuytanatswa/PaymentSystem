from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.config.database import AsyncSessionLocal

EXCLUDED_PATHS = {"/health", "/api/v1/webhooks/stitch"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        api_key = request.headers.get("x-api-key")
        if not api_key:
            return JSONResponse(status_code=401, content={"error": "Missing x-api-key header"})

        async with AsyncSessionLocal() as session:
            from sqlalchemy import text
            result = await session.execute(
                text("SELECT id, name, fee_percentage FROM platforms WHERE api_key = :key"),
                {"key": api_key},
            )
            platform = result.fetchone()

        if not platform:
            return JSONResponse(status_code=401, content={"error": "Invalid API key"})

        request.state.platform_id = platform.id
        request.state.platform_name = platform.name
        request.state.platform_fee_percentage = platform.fee_percentage

        return await call_next(request)
