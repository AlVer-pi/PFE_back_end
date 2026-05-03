import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import AlertResponse

# Configuration
SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# Logger
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.alerts")

router = APIRouter(prefix="/admin/alerts", tags=["Alerts"])


# --- Supabase response helper ---
def _parse_supabase_response(
    response: Any,
) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    """
    Normalize supabase client response to (data, error, status_code).
    """
    if response is None:
        return None, None, None
    if (
        hasattr(response, "data")
        or hasattr(response, "error")
        or hasattr(response, "status_code")
    ):
        data = getattr(response, "data", None)
        error = getattr(response, "error", None)
        status_code = getattr(response, "status_code", None)
        return data, error, status_code
    if isinstance(response, dict):
        return response.get("data"), response.get("error"), response.get("status_code")
    return None, None, None


# --- Admin role check ---
async def get_current_admin_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    Verify that the current user has admin role.
    Raises 401 if token is invalid, 403 if user is not admin.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Please provide a valid Bearer token in Authorization header.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    admin_exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to access this resource. Admin role required.",
    )

    try:
        logger.debug("Decoding JWT token for admin check")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        logger.debug("JWT decoded successfully")
    except JWTError as e:
        logger.warning("JWT decode failed: %s", e)
        raise credentials_exception

    email = payload.get("sub")
    role = payload.get("role")
    logger.debug("Extracted email from token: %s, role: %s", email, role)

    if not email or not isinstance(email, str):
        logger.warning("Email not found in token or is not a string")
        raise credentials_exception

    # Check if role is admin
    if role != "admin":
        logger.warning(
            "User %s attempted to access admin endpoint without admin role (role: %s)",
            email,
            role,
        )
        raise admin_exception

    logger.debug("Admin user %s verified", email)
    return {"email": email, "role": role}


# --- Endpoints ---


@router.get("", response_model=List[AlertResponse])
async def get_active_alerts(
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Get all active (unread) alerts.
    Returns alerts about low stock, stock depletion, and other notifications.
    Sorted by most recent first.
    """
    logger.info("Admin user %s fetching active alerts", admin_user.get("email"))

    response = (
        supabase.table("alerts")
        .select("*")
        .eq("is_read", False)
        .order("created_at", desc=True)
        .execute()
    )
    data, error, status_code = _parse_supabase_response(response)
    if error:
        logger.error("Error fetching alerts: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch alerts")
    if not data:
        logger.info("No active alerts found")
        return []
    logger.info("Retrieved %d active alerts", len(data))
    return data


@router.patch("/{id_alert}/read", status_code=status.HTTP_200_OK)
async def mark_alert_as_read(
    id_alert: int,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Mark an alert as read.
    Updates the is_read status to true.
    """
    logger.info(
        "Admin user %s marking alert ID %d as read",
        admin_user.get("email"),
        id_alert,
    )

    # Fetch alert to verify it exists
    fetch_response = (
        supabase.table("alerts").select("*").eq("id_alert", id_alert).single().execute()
    )
    alert_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not alert_data:
        logger.error("Error fetching alert ID %d: %s", id_alert, fetch_error)
        raise HTTPException(status_code=404, detail="Alert not found")

    # Update alert to mark as read
    update_response = (
        supabase.table("alerts")
        .update({"is_read": True})
        .eq("id_alert", id_alert)
        .execute()
    )
    updated_data, update_error, _ = _parse_supabase_response(update_response)
    if update_error:
        logger.error("Error updating alert ID %d: %s", id_alert, update_error)
        raise HTTPException(status_code=500, detail="Failed to update alert")

    logger.info("Successfully marked alert ID %d as read", id_alert)
    return {
        "id_alert": id_alert,
        "message": "Alert marked as read successfully",
    }


@router.delete("/{id_alert}", status_code=status.HTTP_200_OK)
async def delete_alert(
    id_alert: int,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Delete an alert.
    Removes the alert from the system.
    """
    logger.info(
        "Admin user %s deleting alert ID %d",
        admin_user.get("email"),
        id_alert,
    )

    # Fetch alert to verify it exists
    fetch_response = (
        supabase.table("alerts")
        .select("id_alert")
        .eq("id_alert", id_alert)
        .single()
        .execute()
    )
    alert_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not alert_data:
        logger.error("Error fetching alert ID %d: %s", id_alert, fetch_error)
        raise HTTPException(status_code=404, detail="Alert not found")

    # Delete alert
    delete_response = (
        supabase.table("alerts").delete().eq("id_alert", id_alert).execute()
    )
    deleted_data, delete_error, _ = _parse_supabase_response(delete_response)
    if delete_error:
        logger.error("Error deleting alert ID %d: %s", id_alert, delete_error)
        raise HTTPException(status_code=500, detail="Failed to delete alert")

    logger.info("Successfully deleted alert ID %d", id_alert)
    return {
        "id_alert": id_alert,
        "message": "Alert deleted successfully",
    }
