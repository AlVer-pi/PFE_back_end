import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import OrderCreateRequest, OrderItemBase, OrderResponse

# Configuration
SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# Logger
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.orders")

router = APIRouter(prefix="/orders", tags=["Orders"])


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


# --- Authentication helpers ---
async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    Verify that the user is authenticated.
    Raises 401 if token is invalid.
    Returns user info (email, role, id_user if available).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Please provide a valid Bearer token in Authorization header.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        logger.debug("Decoding JWT token for user authentication")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        logger.debug("JWT decoded successfully")
    except JWTError as e:
        logger.warning("JWT decode failed: %s", e)
        raise credentials_exception

    email = payload.get("sub")
    role = payload.get("role")
    id_user = payload.get("id_user")  # Assuming id_user is in the token
    logger.debug(
        "Extracted email from token: %s, role: %s, id_user: %s", email, role, id_user
    )

    if not email or not isinstance(email, str):
        logger.warning("Email not found in token or is not a string")
        raise credentials_exception

    logger.debug("User %s verified", email)
    return {"email": email, "role": role, "id_user": id_user}


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


# --- Alert creation helper ---
async def _create_alert(
    id_user: int,
    alert_type: str,
    message: str,
) -> bool:
    """
    Helper function to create an alert in the alerts table.
    Returns True if successful, False otherwise.
    """
    try:
        response = (
            supabase.table("alerts")
            .insert(
                {
                    "id_user": id_user,
                    "type": alert_type,
                    "message": message,
                    "is_read": False,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            .execute()
        )

        data, error, _ = _parse_supabase_response(response)
        if error:
            logger.error("Error creating alert: %s", error)
            return False
        logger.debug("Alert created successfully for user %d", id_user)
        return True
    except Exception as e:
        logger.error("Exception while creating alert: %s", e)
        return False


# --- Stock verification helper ---
async def _verify_order_stock(
    order_items: List[OrderItemBase], id_user: int
) -> Tuple[bool, Optional[str]]:
    """
    Verify that stock is available for all order items.
    Checks:
    1. If any ingredient has current_stock <= min_stock_threshold (low stock warning)
    2. If there's enough current_stock for the required_quantity in cake_ingredients

    Returns (is_valid, error_message)
    - is_valid=True if order can proceed (sufficient stock)
    - is_valid=False if order cannot proceed, error_message explains why
    """
    logger.info("Verifying stock for order with %d items", len(order_items))

    for item in order_items:
        id_cake = item.id_cake
        quantity = item.quantity

        logger.debug("Checking stock for cake ID %d, quantity %d", id_cake, quantity)

        # Fetch recipe for this cake (all ingredients needed)
        recipe_response = (
            supabase.table("cake_ingredients")
            .select("id_ingredient, required_quantity")
            .eq("id_cake", id_cake)
            .execute()
        )
        recipe_data, recipe_error, _ = _parse_supabase_response(recipe_response)
        if recipe_error or not recipe_data:
            logger.error("Error fetching recipe for cake %d: %s", id_cake, recipe_error)
            error_msg = f"Recipe not found for cake ID {id_cake}"
            await _create_alert(id_user, "insufficient_ingredients", error_msg)
            return False, error_msg

        logger.debug(
            "Found %d ingredients in recipe for cake %d", len(recipe_data), id_cake
        )

        # Check each ingredient's stock
        for recipe_item in recipe_data:
            id_ingredient = recipe_item.get("id_ingredient")
            required_quantity = recipe_item.get("required_quantity")
            needed_quantity = (
                required_quantity * quantity
            )  # Total needed for this order

            # Fetch ingredient details
            ingredient_response = (
                supabase.table("ingredients")
                .select("name, current_stock, min_stock_threshold, unit")
                .eq("id_ingredient", id_ingredient)
                .single()
                .execute()
            )
            ingredient_data, ingredient_error, _ = _parse_supabase_response(
                ingredient_response
            )
            if ingredient_error or not ingredient_data:
                logger.error(
                    "Error fetching ingredient %d: %s", id_ingredient, ingredient_error
                )
                error_msg = f"Ingredient ID {id_ingredient} not found"
                await _create_alert(id_user, "insufficient_ingredients", error_msg)
                return False, error_msg

            current_stock = ingredient_data.get("current_stock", 0)
            min_threshold = ingredient_data.get("min_stock_threshold", 0)
            ingredient_name = ingredient_data.get("name", f"Ingredient {id_ingredient}")
            unit = ingredient_data.get("unit", "unit")

            logger.debug(
                "Ingredient '%s': current=%f, min_threshold=%f, needed=%f, unit=%s",
                ingredient_name,
                current_stock,
                min_threshold,
                needed_quantity,
                unit,
            )

            # Check 1: Warn if current stock is at or below minimum threshold
            if current_stock <= min_threshold:
                warning_msg = f"Warning: Ingredient '{ingredient_name}' is at or below minimum stock threshold ({current_stock} {unit} <= {min_threshold} {unit})"
                logger.warning(warning_msg)
                # Create alert for admins, but don't block the order
                await _create_alert(id_user, "low_stock", warning_msg)

            # Check 2: Verify sufficient stock for order
            if current_stock < needed_quantity:
                error_msg = f"Insufficient stock for ingredient '{ingredient_name}'. Required: {needed_quantity} {unit}, Available: {current_stock} {unit}"
                logger.error(error_msg)
                await _create_alert(id_user, "insufficient_ingredients", error_msg)
                return False, error_msg

    logger.info("Stock verification passed for order")
    return True, None


# --- Endpoints ---


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Dict[str, Any])
async def create_order(
    order_data: OrderCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Client-only: Create a new order.
    Verifies stock availability before creating the order.
    If stock is insufficient, creates an alert and rejects the order.
    If accepted, reduces ingredient stock and creates a 'new_order' alert for admins.
    """
    id_client = current_user.get("id_user")
    email = current_user.get("email")

    if not id_client:
        logger.error("id_user not found in token for user %s", email)
        raise HTTPException(
            status_code=400,
            detail="User ID not found in token. Please contact support.",
        )

    logger.info(
        "User %s (ID %d) creating order with %d items, total price: %s",
        email,
        id_client,
        len(order_data.items),
        order_data.total_price,
    )

    # Verify stock availability
    is_valid, error_message = await _verify_order_stock(order_data.items, id_client)
    if not is_valid:
        logger.warning(
            "Stock verification failed for order by user %d: %s",
            id_client,
            error_message,
        )
        raise HTTPException(
            status_code=400,
            detail=error_message or "Stock verification failed",
        )

    # Insert order
    insert_response = (
        supabase.table("orders")
        .insert(
            {
                "id_client": id_client,
                "status": "pending",
                "total_price": str(order_data.total_price),
                "delivery_address": order_data.delivery_address,
                "delivery_lat_lng": order_data.delivery_lat_lng,
            }
        )
        .execute()
    )
    order_insert_data, order_insert_error, _ = _parse_supabase_response(insert_response)
    if order_insert_error or not order_insert_data:
        logger.error("Error creating order: %s", order_insert_error)
        raise HTTPException(status_code=500, detail="Failed to create order")

    id_order = order_insert_data[0].get("id_order")
    logger.debug("Order created with ID %d", id_order)

    # Insert order items
    order_items_to_insert = [
        {
            "id_order": id_order,
            "id_cake": item.id_cake,
            "quantity": item.quantity,
        }
        for item in order_data.items
    ]

    items_response = (
        supabase.table("order_items").insert(order_items_to_insert).execute()
    )
    items_data, items_error, _ = _parse_supabase_response(items_response)
    if items_error:
        logger.error("Error creating order items: %s", items_error)
        # Try to rollback the order (delete it)
        supabase.table("orders").delete().eq("id_order", id_order).execute()
        raise HTTPException(status_code=500, detail="Failed to create order items")

    # Log number of items created - Choose one of these options:

    # OPTION 1: Simple message (safest, no type issues)
    logger.debug("Order items created successfully")

    # OPTION 2: Log based on request (what we know we sent)
    # logger.debug("Order items created: %d items", len(order_data.items))

    # OPTION 3: Log based on response (what database returned, type-safe)
    # items_count = len(items_data) if isinstance(items_data, list) else 1
    # logger.debug("Order items created: %d items", items_count)

    # Reduce ingredient stock for each order item
    for item in order_data.items:
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
            logger.warning(
                "Could not fetch recipe for cake %d to reduce stock", id_cake
            )
            continue

        for recipe_item in recipe_data:
            id_ingredient = recipe_item.get("id_ingredient")
            required_quantity = recipe_item.get("required_quantity")
            reduction_amount = required_quantity * quantity

            # Get current stock
            ing_response = (
                supabase.table("ingredients")
                .select("current_stock")
                .eq("id_ingredient", id_ingredient)
                .single()
                .execute()
            )
            ing_data, ing_error, _ = _parse_supabase_response(ing_response)
            if ing_error or not ing_data:
                logger.warning(
                    "Could not fetch ingredient %d to reduce stock", id_ingredient
                )
                continue

            current_stock = ing_data.get("current_stock", 0)
            new_stock = max(0, current_stock - reduction_amount)  # Don't go below 0

            # Update stock
            update_response = (
                supabase.table("ingredients")
                .update({"current_stock": new_stock})
                .eq("id_ingredient", id_ingredient)
                .execute()
            )
            update_data, update_error, _ = _parse_supabase_response(update_response)
            if update_error:
                logger.warning(
                    "Error reducing stock for ingredient %d: %s",
                    id_ingredient,
                    update_error,
                )
            else:
                logger.debug(
                    "Reduced stock for ingredient %d by %f",
                    id_ingredient,
                    reduction_amount,
                )

    # Create alert for admins about new order
    await _create_alert(
        id_client,
        "new_order",
        f"New order #{id_order} created for {order_data.total_price}. {len(order_data.items)} items ordered.",
    )

    logger.info("Order %d created successfully by user %d", id_order, id_client)
    return {
        "id_order": id_order,
        "status": "pending",
        "items": len(order_data.items),
        "total_price": order_data.total_price,
        "message": "Order created successfully",
    }


@router.get("/my-history", response_model=List[OrderResponse])
async def get_user_orders(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Client-only: Get order history for the current user.
    Returns all orders created by this user, sorted by most recent first.
    """
    id_client = current_user.get("id_user")
    email = current_user.get("email")

    if not id_client:
        logger.error("id_user not found in token for user %s", email)
        raise HTTPException(
            status_code=400,
            detail="User ID not found in token. Please contact support.",
        )

    logger.info("User %s (ID %d) fetching order history", email, id_client)

    response = (
        supabase.table("orders")
        .select("*")
        .eq("id_client", id_client)
        .order("created_at", desc=True)
        .execute()
    )
    data, error, status_code = _parse_supabase_response(response)
    if error:
        logger.error("Error fetching user orders: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch order history")

    if not data:
        logger.info("No orders found for user %d", id_client)
        return []

    logger.info("Retrieved %d orders for user %d", len(data), id_client)
    return data


# --- Admin / Logistics Endpoints ---


@router.get("/admin/orders", response_model=List[Dict[str, Any]])
async def list_all_orders(
    status_filter: Optional[str] = None,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Get all orders, optionally filtered by status.
    Returns all orders sorted by most recent first.
    """
    logger.info(
        "Admin user %s fetching all orders (status filter: %s)",
        admin_user.get("email"),
        status_filter,
    )

    query = supabase.table("orders").select("*")

    if status_filter:
        query = query.eq("status", status_filter)

    response = query.order("created_at", desc=True).execute()
    data, error, status_code = _parse_supabase_response(response)

    if error:
        logger.error("Error fetching all orders: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch orders")

    if not data:
        logger.info("No orders found")
        return []

    logger.info("Retrieved %d orders", len(data))
    return data


@router.patch("/admin/orders/{id_order}/status", status_code=status.HTTP_200_OK)
async def update_order_status(
    id_order: int,
    new_status: str,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Update order status.
    Allowed statuses: 'pending', 'preparing', 'out_for_delivery', 'delivered', 'refused'

    When status changes to 'preparing', ingredient stock is already reduced (done at order creation).
    Creates alert for user about status change.
    """
    allowed_statuses = [
        "pending",
        "preparing",
        "out_for_delivery",
        "delivered",
        "refused",
    ]

    if new_status not in allowed_statuses:
        logger.warning(
            "Admin user %s attempted invalid status %s for order %d",
            admin_user.get("email"),
            new_status,
            id_order,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed values: {', '.join(allowed_statuses)}",
        )

    logger.info(
        "Admin user %s updating order %d status to '%s'",
        admin_user.get("email"),
        id_order,
        new_status,
    )

    # Fetch current order
    fetch_response = (
        supabase.table("orders").select("*").eq("id_order", id_order).single().execute()
    )
    order_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not order_data:
        logger.error("Error fetching order %d: %s", id_order, fetch_error)
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = order_data.get("status")
    id_client = order_data.get("id_client")

    logger.debug("Order %d current status: %s", id_order, current_status)

    # Update order status
    update_response = (
        supabase.table("orders")
        .update({"status": new_status})
        .eq("id_order", id_order)
        .execute()
    )
    updated_data, update_error, _ = _parse_supabase_response(update_response)
    if update_error:
        logger.error("Error updating order %d status: %s", id_order, update_error)
        raise HTTPException(status_code=500, detail="Failed to update order status")

    logger.debug("Order %d status updated successfully", id_order)

    # Create alert for user about status change
    status_message = {
        "pending": "Your order is pending.",
        "preparing": "Your order is being prepared.",
        "out_for_delivery": "Your order is out for delivery!",
        "delivered": "Your order has been delivered!",
        "refused": "Your order has been refused. Please contact support.",
    }

    alert_msg = status_message.get(new_status, f"Order status changed to: {new_status}")
    await _create_alert(id_client, "order_status_update", alert_msg)

    logger.info("Alert created for user %d about status change", id_client)

    return {
        "id_order": id_order,
        "previous_status": current_status,
        "new_status": new_status,
        "message": "Order status updated successfully",
    }
