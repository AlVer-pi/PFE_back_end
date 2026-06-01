import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import AlertResponse

SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.alerts")

router = APIRouter(prefix="/admin/alerts", tags=["Alerts"])


def _parse_supabase_response(response: Any) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    if response is None:
        return None, None, None
    if hasattr(response, "data") or hasattr(response, "error") or hasattr(response, "status_code"):
        return getattr(response, "data", None), getattr(response, "error", None), getattr(response, "status_code", None)
    if isinstance(response, dict):
        return response.get("data"), response.get("error"), response.get("status_code")
    return None, None, None


async def get_current_admin_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Please provide a valid Bearer token in Authorization header.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        logger.warning("JWT decode failed: %s", e)
        raise credentials_exception

    email = payload.get("sub")
    role = payload.get("role")

    if not email or not isinstance(email, str):
        raise credentials_exception

    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )

    # Resolve id_user from DB so we can scope alerts to this admin only
    user_resp = supabase.table("users").select("id_user").eq("email", email).single().execute()
    user_data, user_error, _ = _parse_supabase_response(user_resp)
    if user_error or not user_data:
        raise HTTPException(status_code=500, detail="Failed to resolve user identity")

    id_user = user_data.get("id_user")
    logger.debug("Admin %s (id_user=%s) verified", email, id_user)
    return {"email": email, "role": role, "id_user": id_user}


@router.get("", response_model=List[AlertResponse])
async def get_active_alerts(
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Admin-only: Get all unread alerts belonging to this admin."""
    id_user = admin_user.get("id_user")
    logger.info("Admin %s (id=%s) fetching active alerts", admin_user.get("email"), id_user)

    response = (
        supabase.table("alerts")
        .select("*")
        .eq("id_user", id_user)        # ← scoped to this admin only
        .eq("is_read", False)
        .order("created_at", desc=True)
        .execute()
    )
    data, error, _ = _parse_supabase_response(response)
    if error:
        logger.error("Error fetching alerts: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch alerts")
    if not data:
        return []
    logger.info("Retrieved %d active alerts for user %s", len(data), id_user)
    return data


@router.patch("/{id_alert}/read", status_code=status.HTTP_200_OK)
async def mark_alert_as_read(
    id_alert: int,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Admin-only: Mark an alert as read. Only if it belongs to this admin."""
    id_user = admin_user.get("id_user")

    fetch_response = (
        supabase.table("alerts")
        .select("*")
        .eq("id_alert", id_alert)
        .eq("id_user", id_user)        # ← scoped to this admin only
        .single()
        .execute()
    )
    alert_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not alert_data:
        raise HTTPException(status_code=404, detail="Alert not found")

    update_response = (
        supabase.table("alerts")
        .update({"is_read": True})
        .eq("id_alert", id_alert)
        .execute()
    )
    _, update_error, _ = _parse_supabase_response(update_response)
    if update_error:
        raise HTTPException(status_code=500, detail="Failed to update alert")

    return {"id_alert": id_alert, "message": "Alert marked as read successfully"}


@router.delete("/{id_alert}", status_code=status.HTTP_200_OK)
async def delete_alert(
    id_alert: int,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Admin-only: Delete an alert. Only if it belongs to this admin."""
    id_user = admin_user.get("id_user")

    fetch_response = (
        supabase.table("alerts")
        .select("id_alert")
        .eq("id_alert", id_alert)
        .eq("id_user", id_user)        # ← scoped to this admin only
        .single()
        .execute()
    )
    alert_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not alert_data:
        raise HTTPException(status_code=404, detail="Alert not found")

    delete_response = (
        supabase.table("alerts").delete().eq("id_alert", id_alert).execute()
    )
    _, delete_error, _ = _parse_supabase_response(delete_response)
    if delete_error:
        raise HTTPException(status_code=500, detail="Failed to delete alert")

    return {"id_alert": id_alert, "message": "Alert deleted successfully"}