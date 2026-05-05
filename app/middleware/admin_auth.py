from fastapi import Header, HTTPException

from app.config.env import settings


async def require_admin_key(x_admin_api_key: str = Header(..., alias="x-admin-api-key")) -> None:
    if x_admin_api_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin API key")
