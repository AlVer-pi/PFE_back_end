import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import OrderCreateRequest, OrderItemBase, OrderResponse

SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.orders")

router = APIRouter(prefix="/orders", tags=["Orders"])

# Statuses that trigger ingredient deduction (transition FROM pending)
DEDUCT_ON_TRANSITION_TO = {"preparing", "out_for_delivery", "delivered"}

# Statuses that never deduct
NO_DEDUCT_STATUSES = {"pending", "refused", "cancelled"}


# --- Helpers ---
def _parse_supabase_response(response: Any) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    if response is None:
        return None, None, None
    if hasattr(response, "data") or hasattr(response, "error") or hasattr(response, "status_code"):
        return getattr(response, "data", None), getattr(response, "error", None), getattr(response, "status_code", None)
    if isinstance(response, dict):
        return response.get("data"), response.get("error"), response.get("status_code")
    return None, None, None


def _get_id_user(email: str) -> int:
    resp = supabase.table("users").select("id_user").eq("email", email).single().execute()
    data, error, _ = _parse_supabase_response(resp)
    if error or not data:
        raise HTTPException(status_code=500, detail="Failed to resolve user identity")
    return data.get("id_user")


# --- Auth guards ---
async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated.",
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

    id_user = _get_id_user(email)
    return {"email": email, "role": role, "id_user": id_user}


async def get_current_admin_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    user = await get_current_user(token)
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")
    return user


# --- Alert helper ---
async def _create_alert(id_user: int, alert_type: str, message: str) -> bool:
    try:
        response = (
            supabase.table("alerts")
            .insert({
                "id_user": id_user,
                "type": alert_type,
                "message": message,
                "is_read": False,
                "created_at": datetime.utcnow().isoformat(),
            })
            .execute()
        )
        data, error, _ = _parse_supabase_response(response)
        if error:
            logger.error("Error creating alert: %s", error)
            return False
        return True
    except Exception as e:
        logger.error("Exception while creating alert: %s", e)
        return False


# --- Deduction logic ---
async def _deduct_ingredients_for_order(id_order: int, id_user: int) -> None:
    """
    Deducts ingredients from stock based on the order's items and their recipes.
    Fires a low-stock alert if any ingredient drops at or below its threshold after deduction.
    Called only once: when status transitions from 'pending' to an active status.
    """
    logger.info("Deducting ingredients for order #%d", id_order)

    # Get all items in this order
    items_resp = (
        supabase.table("order_items")
        .select("id_cake, quantity")
        .eq("id_order", id_order)
        .execute()
    )
    items_data, items_error, _ = _parse_supabase_response(items_resp)
    if items_error or not items_data:
        logger.error("Could not fetch order items for order #%d", id_order)
        return

    for item in items_data:
        id_cake = item.get("id_cake")
        quantity_ordered = item.get("quantity", 1)

        # Get the recipe for this cake
        recipe_resp = (
            supabase.table("cake_ingredients")
            .select("id_ingredient, required_quantity")
            .eq("id_cake", id_cake)
            .execute()
        )
        recipe_data, recipe_error, _ = _parse_supabase_response(recipe_resp)
        if recipe_error or not recipe_data:
            logger.warning("No recipe found for cake #%d — skipping deduction", id_cake)
            continue

        for recipe_item in recipe_data:
            id_ingredient = recipe_item.get("id_ingredient")
            required_per_unit = recipe_item.get("required_quantity", 0)
            total_to_deduct = required_per_unit * quantity_ordered

            # Fetch current stock
            ing_resp = (
                supabase.table("ingredients")
                .select("name, current_stock, min_stock_threshold, unit")
                .eq("id_ingredient", id_ingredient)
                .single()
                .execute()
            )
            ing_data, ing_error, _ = _parse_supabase_response(ing_resp)
            if ing_error or not ing_data:
                logger.warning("Ingredient #%d not found — skipping", id_ingredient)
                continue

            current_stock = ing_data.get("current_stock", 0)
            min_threshold = ing_data.get("min_stock_threshold", 0)
            ing_name = ing_data.get("name", f"Ingredient #{id_ingredient}")
            unit = ing_data.get("unit", "unit")

            new_stock = max(0, current_stock - total_to_deduct)

            # Update stock
            supabase.table("ingredients").update(
                {"current_stock": new_stock}
            ).eq("id_ingredient", id_ingredient).execute()

            logger.info(
                "Deducted %.2f %s of '%s': %.2f → %.2f",
                total_to_deduct, unit, ing_name, current_stock, new_stock,
            )

            # Fire low-stock alert if new stock is at or below threshold
            if new_stock <= min_threshold:
                await _create_alert(
                    id_user,
                    "low_stock",
                    f"⚠️ '{ing_name}' is running low: {new_stock} {unit} remaining (threshold: {min_threshold} {unit})",
                )
                logger.info("Low-stock alert fired for '%s'", ing_name)


# --- Endpoints ---

async def _check_order_stock(items: list, id_admin: int) -> None:
    """
    Check whether the order requires more ingredients than are currently in stock.
    If any ingredient is insufficient, fire an alert that includes the total needed
    and how much more is required to fulfill the order.
    """
    if not items:
        return

    cake_quantities: dict[int, int] = {}
    for item in items:
        id_cake = item.id_cake if hasattr(item, 'id_cake') else item.get('id_cake')
        quantity_ordered = item.quantity if hasattr(item, 'quantity') else item.get('quantity', 1)
        if id_cake is None or quantity_ordered <= 0:
            continue
        cake_quantities[id_cake] = cake_quantities.get(id_cake, 0) + quantity_ordered

    if not cake_quantities:
        return

    recipe_resp = (
        supabase.table("cake_ingredients")
        .select("id_cake, id_ingredient, required_quantity")
        .in_("id_cake", list(cake_quantities.keys()))
        .execute()
    )
    recipe_data, recipe_error, _ = _parse_supabase_response(recipe_resp)
    if recipe_error or not recipe_data:
        logger.warning("Failed to fetch cake recipes for order stock check: %s", recipe_error)
        return

    needed_by_ingredient: dict[int, float] = {}
    for recipe_item in recipe_data:
        id_cake = recipe_item.get("id_cake")
        id_ingredient = recipe_item.get("id_ingredient")
        required_per_unit = float(recipe_item.get("required_quantity", 0) or 0)
        quantity_ordered = cake_quantities.get(id_cake, 0)
        if id_ingredient is None or quantity_ordered <= 0:
            continue
        needed_by_ingredient[id_ingredient] = (
            needed_by_ingredient.get(id_ingredient, 0.0) + required_per_unit * quantity_ordered
        )

    if not needed_by_ingredient:
        logger.warning("No ingredient totals computed for order stock check")
        return

    ing_resp = (
        supabase.table("ingredients")
        .select("id_ingredient, name, current_stock, min_stock_threshold, unit")
        .in_("id_ingredient", list(needed_by_ingredient.keys()))
        .execute()
    )
    ing_data, ing_error, _ = _parse_supabase_response(ing_resp)
    if ing_error:
        logger.warning("Failed to fetch ingredient stock data: %s", ing_error)
        return

    ingredients_map = {ing["id_ingredient"]: ing for ing in (ing_data or [])}

    for id_ingredient, total_needed in needed_by_ingredient.items():
        ing = ingredients_map.get(id_ingredient)
        if not ing:
            logger.warning("Ingredient #%s not found, skipping stock check", id_ingredient)
            continue

        current_stock = float(ing.get("current_stock", 0) or 0)
        min_threshold = float(ing.get("min_stock_threshold", 0) or 0)
        ing_name = ing.get("name", f"Ingredient #{id_ingredient}")
        unit = ing.get("unit", "unit")

        if current_stock < total_needed:
            shortage = total_needed - current_stock
            msg = (
                f"⚠️ Insufficient stock alert: '{ing_name}' requires {total_needed:.2f} {unit} "
                f"for this order, but only {current_stock:.2f} {unit} is available. "
                f"Need an additional {shortage:.2f} {unit} to fulfill the order."
            )
            await _create_alert(id_admin, "insufficient_stock", msg)
            logger.warning(
                "Insufficient stock alert fired for '%s': need %.2f, have %.2f",
                ing_name,
                total_needed,
                current_stock,
            )

        elif current_stock <= min_threshold:
            warn_msg = (
                f"⚠️ Low stock warning: '{ing_name}' is at {current_stock:.2f} {unit} "
                f"(threshold: {min_threshold:.2f} {unit}). Consider restocking soon."
            )
            await _create_alert(id_admin, "low_stock", warn_msg)
            logger.info("Low-stock warning fired for '%s'", ing_name)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Dict[str, Any])
async def create_order(
    order_data: OrderCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Create a new order in 'pending' status.
    Checks ingredient stock before accepting — fires alerts for insufficient or low stock.
    Deduction happens later when admin moves status out of 'pending'.
    """
    id_client = current_user.get("id_user")
    print(f"!!! create_order called by id_client={id_client}, items={[(i.id_cake, i.quantity) for i in order_data.items]}")

    # Resolve the admin who owns these cakes to send alerts to them
    id_admin = id_client  # fallback
    if order_data.items:
        first_cake_resp = (
            supabase.table("cakes")
            .select("id_user")
            .eq("id_cake", order_data.items[0].id_cake)
            .single()
            .execute()
        )
        first_cake_data, _, _ = _parse_supabase_response(first_cake_resp)
        if first_cake_data:
            id_admin = first_cake_data.get("id_user", id_client)
    print(f"!!! id_admin resolved to {id_admin}")

    # Check stock before accepting
    await _check_order_stock(order_data.items, id_admin)
    print(f"!!! _check_order_stock completed")

    insert_response = (
        supabase.table("orders")
        .insert({
            "id_client": id_client,
            "status": "pending",
            "total_price": str(order_data.total_price),
            "delivery_address": order_data.delivery_address,
            "delivery_lat_lng": order_data.delivery_lat_lng,
        })
        .execute()
    )
    order_insert_data, order_insert_error, _ = _parse_supabase_response(insert_response)
    if order_insert_error or not order_insert_data:
        raise HTTPException(status_code=500, detail="Failed to create order")

    id_order = order_insert_data[0].get("id_order")

    order_items_to_insert = [
        {"id_order": id_order, "id_cake": item.id_cake, "quantity": item.quantity}
        for item in order_data.items
    ]
    items_response = supabase.table("order_items").insert(order_items_to_insert).execute()
    _, items_error, _ = _parse_supabase_response(items_response)
    if items_error:
        supabase.table("orders").delete().eq("id_order", id_order).execute()
        raise HTTPException(status_code=500, detail="Failed to create order items")

    await _create_alert(
        id_admin, "new_order",
        f"New order #{id_order} received. Total: {order_data.total_price} DZD. Items: {len(order_data.items)}.",
    )

    return {
        "id_order": id_order,
        "status": "pending",
        "items": len(order_data.items),
        "total_price": order_data.total_price,
        "message": "Order created successfully",
    }


@router.get("/my-history", response_model=List[OrderResponse])
async def get_user_orders(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Client: get own order history."""
    id_client = current_user.get("id_user")
    response = (
        supabase.table("orders")
        .select("*")
        .eq("id_client", id_client)
        .order("created_at", desc=True)
        .execute()
    )
    data, error, _ = _parse_supabase_response(response)
    if error:
        raise HTTPException(status_code=500, detail="Failed to fetch order history")
    return data or []


@router.get("/admin/orders", response_model=List[Dict[str, Any]])
async def list_all_orders(
    status_filter: Optional[str] = None,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Admin-only: orders that contain at least one cake belonging to this admin."""
    id_user = admin_user.get("id_user")

    cakes_resp = supabase.table("cakes").select("id_cake").eq("id_user", id_user).execute()
    cakes_data, cakes_error, _ = _parse_supabase_response(cakes_resp)
    if cakes_error or not cakes_data:
        return []

    owned_cake_ids = [c["id_cake"] for c in cakes_data]

    items_resp = (
        supabase.table("order_items")
        .select("id_order")
        .in_("id_cake", owned_cake_ids)
        .execute()
    )
    items_data, items_error, _ = _parse_supabase_response(items_resp)
    if items_error or not items_data:
        return []

    order_ids = list({row["id_order"] for row in items_data})

    query = supabase.table("orders").select("*").in_("id_order", order_ids)
    if status_filter:
        query = query.eq("status", status_filter)

    response = query.order("created_at", desc=True).execute()
    data, error, _ = _parse_supabase_response(response)
    if error:
        raise HTTPException(status_code=500, detail="Failed to fetch orders")
    return data or []


@router.get("/admin/orders/{id_order}", response_model=Dict[str, Any])
async def get_order_detail(
    id_order: int,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Admin-only: full order detail including items. Only if this admin owns a cake in the order."""
    id_user = admin_user.get("id_user")

    cakes_resp = supabase.table("cakes").select("id_cake").eq("id_user", id_user).execute()
    cakes_data, _, _ = _parse_supabase_response(cakes_resp)
    owned_cake_ids = [c["id_cake"] for c in (cakes_data or [])]

    items_resp = (
        supabase.table("order_items")
        .select("id_order, id_cake, quantity, cakes(name, price, photo_url)")
        .eq("id_order", id_order)
        .execute()
    )
    items_data, items_error, _ = _parse_supabase_response(items_resp)
    if items_error or not items_data:
        raise HTTPException(status_code=404, detail="Order not found")

    order_cake_ids = [row["id_cake"] for row in items_data]
    if not any(cid in owned_cake_ids for cid in order_cake_ids):
        raise HTTPException(status_code=403, detail="This order does not belong to your cakes")

    order_resp = supabase.table("orders").select("*").eq("id_order", id_order).single().execute()
    order_data, order_error, _ = _parse_supabase_response(order_resp)
    if order_error or not order_data:
        raise HTTPException(status_code=404, detail="Order not found")

    order_data["items"] = items_data
    return order_data


@router.patch("/admin/orders/{id_order}/status", status_code=status.HTTP_200_OK)
async def update_order_status(
    id_order: int,
    new_status: str,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Update order status.
    Ingredient deduction fires ONCE when transitioning FROM 'pending'
    to any active status (preparing / out_for_delivery / delivered).
    No deduction for refused or cancelled.
    """
    allowed_statuses = ["pending", "preparing", "out_for_delivery", "delivered", "refused", "cancelled"]
    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed: {', '.join(allowed_statuses)}",
        )

    id_user = admin_user.get("id_user")

    # Verify ownership
    cakes_resp = supabase.table("cakes").select("id_cake").eq("id_user", id_user).execute()
    cakes_data, _, _ = _parse_supabase_response(cakes_resp)
    owned_cake_ids = [c["id_cake"] for c in (cakes_data or [])]

    items_resp = supabase.table("order_items").select("id_cake").eq("id_order", id_order).execute()
    items_data, _, _ = _parse_supabase_response(items_resp)
    order_cake_ids = [row["id_cake"] for row in (items_data or [])]

    if not any(cid in owned_cake_ids for cid in order_cake_ids):
        raise HTTPException(status_code=403, detail="This order does not belong to your cakes")

    # Fetch current order
    fetch_response = supabase.table("orders").select("*").eq("id_order", id_order).single().execute()
    order_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not order_data:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = order_data.get("status")
    id_client = order_data.get("id_client")

    # Guard: don't allow going backwards to pending once active
    if current_status in DEDUCT_ON_TRANSITION_TO and new_status == "pending":
        raise HTTPException(
            status_code=400,
            detail="Cannot revert an active order back to pending.",
        )

    # Update status
    update_response = (
        supabase.table("orders")
        .update({"status": new_status})
        .eq("id_order", id_order)
        .execute()
    )
    _, update_error, _ = _parse_supabase_response(update_response)
    if update_error:
        raise HTTPException(status_code=500, detail="Failed to update order status")

    # ── Deduction logic ──────────────────────────────────────────
    # Trigger ONLY when transitioning FROM pending TO an active status
    # This ensures ingredients are deducted exactly once per order
    should_deduct = (
        current_status == "pending"
        and new_status in DEDUCT_ON_TRANSITION_TO
    )
    if should_deduct:
        logger.info(
            "Order #%d moved from '%s' → '%s': triggering ingredient deduction",
            id_order, current_status, new_status,
        )
        await _deduct_ingredients_for_order(id_order, id_user)
    # ─────────────────────────────────────────────────────────────

    # Only alert on 'cancelled' — that's a client-initiated action the admin needs to know about.
    # All other status changes are admin-initiated, so no alert needed.
    if new_status == "cancelled":
        await _create_alert(
            id_client,
            "order_cancelled",
            f"Order #{id_order} has been cancelled by the client.",
        )

    return {
        "id_order": id_order,
        "previous_status": current_status,
        "new_status": new_status,
        "deduction_triggered": should_deduct,
        "message": "Order status updated successfully",
    }