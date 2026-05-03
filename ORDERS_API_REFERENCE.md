# Orders API Endpoints Reference

## Base URL
All endpoints are prefixed with `/orders`

---

## Client Endpoints

### 1. Create Order
**POST** `/orders`

**Authentication**: Required (Bearer token)

**Request Body**:
```json
{
  "items": [
    {
      "id_cake": 1,
      "quantity": 2
    },
    {
      "id_cake": 3,
      "quantity": 1
    }
  ],
  "total_price": 45.99,
  "delivery_address": "123 Main Street, City",
  "delivery_lat_lng": {
    "lat": 48.8566,
    "lng": 2.3522
  }
}
```

**Response** (201 Created):
```json
{
  "id_order": 5,
  "status": "pending",
  "items": 2,
  "total_price": 45.99,
  "message": "Order created successfully"
}
```

**Errors**:
- `400`: Insufficient stock (alerts created with details)
- `401`: Missing/invalid token
- `500`: Database error

---

### 2. Get User Order History
**GET** `/orders/my-history`

**Authentication**: Required (Bearer token)

**Query Parameters**: None

**Response** (200 OK):
```json
[
  {
    "id_order": 5,
    "id_client": 2,
    "status": "pending",
    "total_price": "45.99",
    "delivery_address": "123 Main Street, City",
    "delivery_lat_lng": {
      "lat": 48.8566,
      "lng": 2.3522
    }
  },
  {
    "id_order": 3,
    "id_client": 2,
    "status": "delivered",
    "total_price": "32.50",
    "delivery_address": "123 Main Street, City",
    "delivery_lat_lng": {
      "lat": 48.8566,
      "lng": 2.3522
    }
  }
]
```

**Errors**:
- `400`: User ID not found in token
- `401`: Missing/invalid token
- `500`: Database error

---

## Admin/Logistics Endpoints

### 3. List All Orders
**GET** `/admin/orders`

**Authentication**: Required (Admin role)

**Query Parameters**:
- `status_filter` (optional): Filter by status (`pending`, `preparing`, `out_for_delivery`, `delivered`, `refused`)

**Examples**:
- `/admin/orders` - Get all orders
- `/admin/orders?status_filter=pending` - Get pending orders
- `/admin/orders?status_filter=out_for_delivery` - Get orders being delivered

**Response** (200 OK):
```json
[
  {
    "id_order": 5,
    "id_client": 2,
    "status": "pending",
    "total_price": "45.99",
    "delivery_address": "123 Main Street, City",
    "delivery_lat_lng": {
      "lat": 48.8566,
      "lng": 2.3522
    },
    "created_at": "2024-01-15T10:30:00Z"
  }
]
```

**Errors**:
- `401`: Missing/invalid token
- `403`: User is not admin
- `500`: Database error

---

### 4. Update Order Status
**PATCH** `/admin/orders/{id_order}/status`

**Authentication**: Required (Admin role)

**Path Parameters**:
- `id_order`: Order ID (integer)

**Query Parameters**:
- `new_status`: New status (required)
  - Valid values: `pending`, `preparing`, `out_for_delivery`, `delivered`, `refused`

**Examples**:
- `/admin/orders/5/status?new_status=preparing` - Mark order as being prepared
- `/admin/orders/5/status?new_status=out_for_delivery` - Mark order as out for delivery
- `/admin/orders/5/status?new_status=delivered` - Mark order as delivered
- `/admin/orders/5/status?new_status=refused` - Refuse the order

**Response** (200 OK):
```json
{
  "id_order": 5,
  "previous_status": "pending",
  "new_status": "preparing",
  "message": "Order status updated successfully"
}
```

**Errors**:
- `400`: Invalid status value
- `401`: Missing/invalid token
- `403`: User is not admin
- `404`: Order not found
- `500`: Database error

---

## Stock Verification & Alerts

### Stock Verification Flow
When an order is created, the system performs two checks:

1. **Low Stock Warning** (non-blocking):
   - If any ingredient `current_stock <= min_stock_threshold`
   - Creates alert but allows order to proceed
   - Alert type: `low_stock`

2. **Insufficient Stock** (blocking):
   - If any ingredient `current_stock < required_quantity`
   - Rejects order with 400 error
   - Creates alert with details
   - Alert type: `insufficient_ingredients`

### Automatic Alerts Created

| Event | Type | Recipient | Example Message |
|-------|------|-----------|-----------------|
| Order Created | `new_order` | User | "New order #5 created for 45.99. 2 items ordered." |
| Low Stock | `low_stock` | User | "Warning: Ingredient 'Flour' is at or below minimum stock threshold (500 gr <= 500 gr)" |
| Insufficient Stock | `insufficient_ingredients` | User | "Insufficient stock for ingredient 'Chocolate'. Required: 200 gr, Available: 150 gr" |
| Status Updated | `order_status_update` | User | "Your order is being prepared." |

---

## Stock Reduction

Stock is automatically reduced **at order creation time**, not when the status is updated.

**Calculation**:
- For each ingredient in the cake recipe:
  - `amount_to_reduce = required_quantity_in_recipe × order_quantity`
  - `new_stock = current_stock - amount_to_reduce` (minimum 0)

**Example**:
- Recipe for cake #1: 200g Flour, 100g Sugar
- Order: 2 × Cake #1
- Stock reduction: 400g Flour, 200g Sugar

---

## Authentication

All endpoints require a valid JWT bearer token in the Authorization header:

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Token Requirements

**Client Endpoints** (`/orders`, `/orders/my-history`):
- Must contain: `sub` (email), `id_user`
- `role` can be any value (admin, pastry_chef, delivery_man, client, etc.)

**Admin Endpoints** (`/admin/orders`, `/admin/orders/{id_order}/status`):
- Must contain: `sub` (email), `role`
- `role` must equal `"admin"` exactly

---

## Status Transitions

Recommended flow:
```
pending → preparing → out_for_delivery → delivered
         ↘ refused (can happen from any state)
```

However, the API allows direct transitions between any statuses. Implement business logic validation on the client side if needed.

---

## Example cURL Commands

### Create an order
```bash
curl -X POST http://localhost:8000/orders \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"id_cake": 1, "quantity": 2}],
    "total_price": 45.99,
    "delivery_address": "123 Main St",
    "delivery_lat_lng": {"lat": 48.8566, "lng": 2.3522}
  }'
```

### Get order history
```bash
curl -X GET http://localhost:8000/orders/my-history \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### List all orders (admin)
```bash
curl -X GET "http://localhost:8000/admin/orders?status_filter=pending" \
  -H "Authorization: Bearer ADMIN_TOKEN"
```

### Update order status (admin)
```bash
curl -X PATCH "http://localhost:8000/admin/orders/5/status?new_status=preparing" \
  -H "Authorization: Bearer ADMIN_TOKEN"
```
