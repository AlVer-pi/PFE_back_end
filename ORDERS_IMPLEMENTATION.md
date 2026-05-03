# Orders Implementation Summary

## Overview
Fully implemented `orders.py` route with complete order management system following the same patterns as `alerts.py` and `inventory.py`.

## Features Implemented

### 1. **Client Endpoints** (require authenticated user)

#### POST `/orders` - Create Order
- Extracts `id_client` from JWT token (from `id_user` claim)
- **Stock Verification** (2-level check):
  1. **Low Stock Warning**: If ingredient `current_stock <= min_stock_threshold`, creates alert but allows order
  2. **Insufficient Stock**: If ingredient `current_stock < required_quantity`, rejects order and creates alert
- Automatically reduces ingredient stock upon successful order creation
- Creates alert for user about new order
- Returns order ID and confirmation

#### GET `/orders/my-history` - User Order History
- Extracts `id_client` from JWT token
- Returns all orders for the authenticated user, sorted by most recent first
- Returns empty list if no orders found

### 2. **Admin/Logistics Endpoints** (require admin role)

#### GET `/admin/orders` - List All Orders
- Optional query parameter `status_filter` to filter by status
- Returns all orders sorted by most recent first
- Returns empty list if no orders found

#### PATCH `/admin/orders/{id_order}/status` - Update Order Status
- Allowed statuses: `pending`, `preparing`, `out_for_delivery`, `delivered`, `refused`
- Validates status before updating
- Creates alert for user about status change
- Stock is automatically reduced at order creation, not at status update

### 3. **Key Features**

#### Stock Verification Logic
```
For each order item:
  For each ingredient in cake recipe:
    - Check if current_stock <= min_stock_threshold (warning only)
    - Check if current_stock < required_quantity (blocking)
    - If insufficient, create alert with ingredient details and reject order
```

#### Alert Creation
Automatically creates alerts for:
- `new_order`: When order is successfully created
- `low_stock`: When ingredient is at/below minimum threshold
- `insufficient_ingredients`: When stock is insufficient to fulfill order
- `order_status_update`: When order status changes

#### Stock Reduction
- Happens automatically at order creation (not at status updates)
- Calculates total needed: `required_quantity * order_quantity`
- Reduces from ingredients table
- Never goes below 0 (uses `max(0, new_stock)`)

## Schema Updates

### New Models in `schemas.py`
- `OrderCreateRequest`: Request model for creating orders (no `id_client` field - extracted from JWT)
- `OrderResponse`: Response model including `id_client` and `id_order`
- `OrderBase`: Base model for order data

## Authentication & Authorization

### User Authentication (`get_current_user`)
- Requires valid JWT bearer token
- Extracts: `email`, `role`, `id_user`
- Used by client endpoints

### Admin Authentication (`get_current_admin_user`)
- Requires valid JWT bearer token with `role == "admin"`
- Returns 403 Forbidden if user is not admin
- Used by admin/logistics endpoints

## Error Handling

| Status | Scenario |
|--------|----------|
| 400 | Insufficient stock, invalid order data, user ID not in token |
| 401 | Missing/invalid JWT token |
| 403 | User attempting admin endpoint without admin role |
| 404 | Order not found, recipe not found |
| 500 | Database errors |

## Database Operations

### Tables Used
- `orders`: Main order table
- `order_items`: Order line items
- `cake_ingredients`: Recipe data (required quantities)
- `ingredients`: Stock levels and thresholds
- `alerts`: Alert notifications

### Transaction-like Behavior
- Order and items are created together
- If items creation fails, order is rolled back (deleted)
- Stock reduction is best-effort (logs warnings but doesn't fail order)

## Logging
- Debug: Token parsing, stock checks, ingredient updates
- Info: User actions, order creation/updates, alerts
- Warning: Invalid operations, stock issues, missing data
- Error: Database failures, recipe/ingredient not found

## Notes

⚠️ **Important**: The JWT token must include an `id_user` claim for client endpoints to work. If this claim is missing from your auth implementation, the endpoint will return a 400 error with message "User ID not found in token. Please contact support."

If `id_user` is named differently in your JWT (e.g., `user_id`, `uid`), update the extraction in both `get_current_user()` function to use the correct claim name.

Status values in the database:
- `pending` (default for new orders)
- `preparing`
- `out_for_delivery`
- `delivered`
- `refused`

The old `accepted` status from your endpoint definition is not in the database schema CHECK constraint. If you need it, either add it to the database constraint or map `accepted` → `preparing`.
