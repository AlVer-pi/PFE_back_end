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


# --- Supabase response helper ---
def _parse_supabase_response(
    response: Any,
) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    if response is None:
        return None, None, None
    if hasattr(response, "data") or hasattr(response, "error") or hasattr(response, "status_code"):
        return getattr(response, "data", None), getattr(response, "error", None), getattr(response, "status_code", None)
    if isinstance(response, dict):
        return response.get("data"), response.get("error"), response.get("status_code")
    return None, None, None


# --- Shared helper: resolve id_user from email ---
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


# --- Stock verification helper ---
async def _verify_order_stock(
    order_items: List[OrderItemBase], id_user: int
) -> Tuple[bool, Optional[str]]:
    for item in order_items:
        id_cake = item.id_cake
        quantity = item.quantity

        recipe_response = (
            supabase.table("cake_ingredients")
            .select("id_ingredient, required_quantity")
            .eq("id_cake", id_cake)
            .execute()
        )
        recipe_data, recipe_error, _ = _parse_supabase_response(recipe_response)
        if recipe_error or not recipe_data:
            error_msg = f"Recipe not found for cake ID {id_cake}"
            await _create_alert(id_user, "insufficient_ingredients", error_msg)
            return False, error_msg

        for recipe_item in recipe_data:
            id_ingredient = recipe_item.get("id_ingredient")
            required_quantity = recipe_item.get("required_quantity")
            needed_quantity = required_quantity * quantity

            ingredient_response = (
                supabase.table("ingredients")
                .select("name, current_stock, min_stock_threshold, unit")
                .eq("id_ingredient", id_ingredient)
                .single()
                .execute()
            )
            ingredient_data, ingredient_error, _ = _parse_supabase_response(ingredient_response)
            if ingredient_error or not ingredient_data:
                error_msg = f"Ingredient ID {id_ingredient} not found"
                await _create_alert(id_user, "insufficient_ingredients", error_msg)
                return False, error_msg

            current_stock = ingredient_data.get("current_stock", 0)
            min_threshold = ingredient_data.get("min_stock_threshold", 0)
            ingredient_name = ingredient_data.get("name", f"Ingredient {id_ingredient}")
            unit = ingredient_data.get("unit", "unit")

            if current_stock <= min_threshold:
                warning_msg = f"Warning: '{ingredient_name}' is at or below minimum threshold ({current_stock} {unit} <= {min_threshold} {unit})"
                await _create_alert(id_user, "low_stock", warning_msg)

            if current_stock < needed_quantity:
                error_msg = f"Insufficient stock for '{ingredient_name}'. Required: {needed_quantity} {unit}, Available: {current_stock} {unit}"
                await _create_alert(id_user, "insufficient_ingredients", error_msg)
                return False, error_msg

    return True, None


# --- Endpoints ---


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Dict[str, Any])
async def create_order(
    order_data: OrderCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Create a new order. Verifies stock, deducts ingredients, fires alerts."""
    id_client = current_user.get("id_user")
    email = current_user.get("email")

    if not id_client:
        raise HTTPException(status_code=400, detail="User ID not found in token.")

    is_valid, error_message = await _verify_order_stock(order_data.items, id_client)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message or "Stock verification failed")

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
    items_data, items_error, _ = _parse_supabase_response(items_response)
    if items_error:
        supabase.table("orders").delete().eq("id_order", id_order).execute()
        raise HTTPException(status_code=500, detail="Failed to create order items")

    # Reduce ingredient stock
    for item in order_data.items:
        recipe_response = (
            supabase.table("cake_ingredients")
            .select("id_ingredient, required_quantity")
            .eq("id_cake", item.id_cake)
            .execute()
        )
        recipe_data, recipe_error, _ = _parse_supabase_response(recipe_response)
        if recipe_error or not recipe_data:
            continue

        for recipe_item in recipe_data:
            id_ingredient = recipe_item.get("id_ingredient")
            reduction_amount = recipe_item.get("required_quantity") * item.quantity

            ing_response = (
                supabase.table("ingredients")
                .select("current_stock")
                .eq("id_ingredient", id_ingredient)
                .single()
                .execute()
            )
            ing_data, ing_error, _ = _parse_supabase_response(ing_response)
            if ing_error or not ing_data:
                continue

            new_stock = max(0, ing_data.get("current_stock", 0) - reduction_amount)
            supabase.table("ingredients").update({"current_stock": new_stock}).eq("id_ingredient", id_ingredient).execute()

    await _create_alert(
        id_client, "new_order",
        f"New order #{id_order} created. Total: {order_data.total_price}. Items: {len(order_data.items)}.",
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
    """
    Admin-only: Get orders that contain at least one cake belonging to this admin.
    Uses order_items → cakes join to filter by ownership.
    """
    id_user = admin_user.get("id_user")
    logger.info("Admin %s fetching their orders", admin_user.get("email"))

    # Step 1: get all cake IDs owned by this admin
    cakes_resp = (
        supabase.table("cakes")
        .select("id_cake")
        .eq("id_user", id_user)
        .execute()
    )
    cakes_data, cakes_error, _ = _parse_supabase_response(cakes_resp)
    if cakes_error:
        raise HTTPException(status_code=500, detail="Failed to fetch cakes")
    if not cakes_data:
        return []

    owned_cake_ids = [c["id_cake"] for c in cakes_data]

    # Step 2: get order_ids that contain any of those cakes
    items_resp = (
        supabase.table("order_items")
        .select("id_order")
        .in_("id_cake", owned_cake_ids)
        .execute()
    )
    items_data, items_error, _ = _parse_supabase_response(items_resp)
    if items_error:
        raise HTTPException(status_code=500, detail="Failed to fetch order items")
    if not items_data:
        return []

    order_ids = list({row["id_order"] for row in items_data})  # deduplicate

    # Step 3: fetch those orders
    query = (
        supabase.table("orders")
        .select("*")
        .in_("id_order", order_ids)
    )
    if status_filter:
        query = query.eq("status", status_filter)

    response = query.order("created_at", desc=True).execute()
    data, error, _ = _parse_supabase_response(response)
    if error:
        raise HTTPException(status_code=500, detail="Failed to fetch orders")

    logger.info("Retrieved %d orders for admin %s", len(data or []), admin_user.get("email"))
    return data or []


@router.get("/admin/orders/{id_order}", response_model=Dict[str, Any])
async def get_order_detail(
    id_order: int,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Get full detail of one order including its items and cake names.
    Only accessible if the order contains a cake owned by this admin.
    """
    id_user = admin_user.get("id_user")

    # Verify this order contains at least one cake owned by this admin
    cakes_resp = (
        supabase.table("cakes").select("id_cake").eq("id_user", id_user).execute()
    )
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

    # Check ownership
    order_cake_ids = [row["id_cake"] for row in items_data]
    if not any(cid in owned_cake_ids for cid in order_cake_ids):
        raise HTTPException(status_code=403, detail="This order does not belong to your cakes")

    # Fetch order header
    order_resp = (
        supabase.table("orders").select("*").eq("id_order", id_order).single().execute()
    )
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
    """Admin-only: Update order status. Only for orders containing this admin's cakes."""
    allowed_statuses = ["pending", "preparing", "out_for_delivery", "delivered", "refused"]
    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed: {', '.join(allowed_statuses)}",
        )

    id_user = admin_user.get("id_user")

    # Verify ownership (same logic as above)
    cakes_resp = supabase.table("cakes").select("id_cake").eq("id_user", id_user).execute()
    cakes_data, _, _ = _parse_supabase_response(cakes_resp)
    owned_cake_ids = [c["id_cake"] for c in (cakes_data or [])]

    items_resp = (
        supabase.table("order_items").select("id_cake").eq("id_order", id_order).execute()
    )
    items_data, _, _ = _parse_supabase_response(items_resp)
    order_cake_ids = [row["id_cake"] for row in (items_data or [])]

    if not any(cid in owned_cake_ids for cid in order_cake_ids):
        raise HTTPException(status_code=403, detail="This order does not belong to your cakes")

    fetch_response = (
        supabase.table("orders").select("*").eq("id_order", id_order).single().execute()
    )
    order_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not order_data:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = order_data.get("status")
    id_client = order_data.get("id_client")

    update_response = (
        supabase.table("orders").update({"status": new_status}).eq("id_order", id_order).execute()
    )
    _, update_error, _ = _parse_supabase_response(update_response)
    if update_error:
        raise HTTPException(status_code=500, detail="Failed to update order status")

    status_messages = {
        "pending": "Your order is pending.",
        "preparing": "Your order is being prepared.",
        "out_for_delivery": "Your order is out for delivery!",
        "delivered": "Your order has been delivered!",
        "refused": "Your order has been refused. Please contact support.",
    }
    await _create_alert(id_client, "order_status_update", status_messages.get(new_status, f"Order status: {new_status}"))

    return {
        "id_order": id_order,
        "previous_status": current_status,
        "new_status": new_status,
        "message": "Order status updated successfully",
    }