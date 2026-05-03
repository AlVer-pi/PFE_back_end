# Orders Implementation - Quick Reference Card

## 🔴 CRITICAL: JWT Token Claim

⚠️ Your JWT **MUST** include `id_user` claim for client endpoints!

```json
{
  "sub": "user@example.com",
  "role": "client",
  "id_user": 2  ← THIS IS REQUIRED
}
```

**If missing, you'll get**: `"User ID not found in token. Please contact support."`

**If named differently**, edit `orders.py` line 73:
```python
id_user = payload.get("your_actual_claim_name")
```

---

## 📍 Endpoint Map

```
CLIENT ROUTES (Authenticated)
POST   /orders                           → Create order
GET    /orders/my-history                → Get user's orders

ADMIN ROUTES (Admin role required)
GET    /admin/orders                     → List all orders
GET    /admin/orders?status_filter=prep  → Filter by status
PATCH  /admin/orders/{id}/status?new_status=preparing  → Update status
```

---

## ✅ Stock Verification

**Two checks happen automatically:**

1. **Low Stock Warning** (non-blocking)
   - If: `ingredient.current_stock <= ingredient.min_stock_threshold`
   - Action: Create alert, allow order

2. **Insufficient Stock** (blocking)
   - If: `ingredient.current_stock < (recipe.required_qty × order.quantity)`
   - Action: Create alert, reject with 400

---

## 🔔 Automatic Alerts

| Type | When | Message Example |
|------|------|-----------------|
| `new_order` | Order created | "New order #5 created for 45.99. 2 items ordered." |
| `low_stock` | Stock ≤ threshold | "Warning: Ingredient 'Flour' is at or below minimum stock..." |
| `insufficient_ingredients` | Stock insufficient | "Insufficient stock for ingredient 'Chocolate'..." |
| `order_status_update` | Status changes | "Your order is being prepared." |

---

## 📦 Stock Reduction

Happens **at order creation**, calculated as:
```
reduction = required_quantity_in_recipe × order_quantity
new_stock = max(0, current_stock - reduction)
```

Example:
- Recipe: 100g Flour per cake
- Order: 3 cakes
- Reduction: 300g Flour

---

## 🚨 HTTP Status Codes

| Code | When | Solution |
|------|------|----------|
| 201 | Order created successfully | ✓ Success |
| 400 | Insufficient stock OR id_user missing | Check stock or JWT token |
| 401 | No/invalid JWT token | Provide Bearer token |
| 403 | Not admin (admin endpoint) | Only admins can access |
| 404 | Order/recipe not found | Order ID incorrect |
| 500 | Database error | Check logs, contact support |

---

## 🔧 Allowed Order Statuses

Valid values: `pending`, `preparing`, `out_for_delivery`, `delivered`, `refused`

```
pending → preparing → out_for_delivery → delivered
           ↘ refused (any time)
```

---

## 📋 Request/Response Examples

### Create Order Request
```json
{
  "items": [
    {"id_cake": 1, "quantity": 2},
    {"id_cake": 3, "quantity": 1}
  ],
  "total_price": 45.99,
  "delivery_address": "123 Main Street",
  "delivery_lat_lng": {"lat": 48.8566, "lng": 2.3522}
}
```

### Create Order Response (201)
```json
{
  "id_order": 5,
  "status": "pending",
  "items": 2,
  "total_price": 45.99,
  "message": "Order created successfully"
}
```

### Get Order History Response (200)
```json
[
  {
    "id_order": 5,
    "id_client": 2,
    "status": "pending",
    "total_price": "45.99",
    "delivery_address": "123 Main Street",
    "delivery_lat_lng": {"lat": 48.8566, "lng": 2.3522}
  }
]
```

### Update Status Response (200)
```json
{
  "id_order": 5,
  "previous_status": "pending",
  "new_status": "preparing",
  "message": "Order status updated successfully"
}
```

---

## 🧪 Test with cURL

### Create Order
```bash
curl -X POST http://localhost:8000/orders \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"id_cake": 1, "quantity": 1}],
    "total_price": 25.00,
    "delivery_address": "Test St",
    "delivery_lat_lng": {"lat": 0, "lng": 0}
  }'
```

### Get Order History
```bash
curl -X GET http://localhost:8000/orders/my-history \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### List All Orders (Admin)
```bash
curl -X GET "http://localhost:8000/admin/orders" \
  -H "Authorization: Bearer ADMIN_TOKEN"
```

### Filter by Status (Admin)
```bash
curl -X GET "http://localhost:8000/admin/orders?status_filter=pending" \
  -H "Authorization: Bearer ADMIN_TOKEN"
```

### Update Status (Admin)
```bash
curl -X PATCH "http://localhost:8000/admin/orders/5/status?new_status=preparing" \
  -H "Authorization: Bearer ADMIN_TOKEN"
```

---

## ⚙️ Configuration

The endpoint requires:
- JWT_SECRET or SUPABASE_KEY environment variable
- Supabase database connection (database.py)
- All required tables: orders, order_items, cake_ingredients, ingredients, alerts

---

## 🐛 Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| 400 "User ID not found" | id_user missing in token | Add id_user to JWT |
| Orders created but stock not reduced | Recipe not found | Create recipe first |
| 400 "Insufficient stock" | Not enough inventory | Increase stock |
| 403 "Admin role required" | Not admin user | Use admin token |
| No alerts created | RLS policy blocked write | Check Supabase RLS |

---

## 📚 Full Documentation

- **ORDERS_README.md** → This overview
- **ORDERS_IMPLEMENTATION.md** → Architecture details
- **ORDERS_API_REFERENCE.md** → Complete API docs
- **ORDERS_TROUBLESHOOTING.md** → Integration guide

---

## 💡 Tips

1. **Test stock verification**: Create test orders with low/high stock
2. **Monitor alerts**: Check alerts table for all events
3. **Check logs**: Set LOG_LEVEL=DEBUG for detailed output
4. **Verify recipes**: Ensure recipes exist before creating orders
5. **Test pagination**: Try with multiple orders

---

## 🎯 Integration Checklist

Before going live:
- [ ] JWT includes id_user
- [ ] Recipes created for all cakes
- [ ] Initial stock populated
- [ ] Test order creation
- [ ] Test stock reduction
- [ ] Test admin functions
- [ ] Alert system working
- [ ] Error handling tested
