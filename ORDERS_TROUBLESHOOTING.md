# Orders Implementation - Troubleshooting & Integration Guide

## Before You Deploy

### 1. JWT Token Configuration
Your auth system must include `id_user` in the JWT token for client endpoints to work.

**Token payload should look like:**
```json
{
  "sub": "user@example.com",
  "role": "client",
  "id_user": 2,
  "iat": 1234567890,
  "exp": 1234571490
}
```

**If your token structure is different**, modify the token extraction in `orders.py`:

Find this line in both `get_current_user()` and where needed:
```python
id_user = payload.get("id_user")  # Assuming id_user is in the token
```

Change to match your claim name, e.g.:
```python
id_user = payload.get("user_id")  # If your claim is user_id
# or
id_user = payload.get("uid")  # If your claim is uid
```

---

## Common Issues & Solutions

### Issue 1: "User ID not found in token" Error

**Symptom**: POST `/orders` returns 400 with message "User ID not found in token"

**Cause**: The `id_user` claim is missing or named differently in your JWT token

**Solution**:
1. Decode your JWT at https://jwt.io/ to see the actual payload
2. Find the claim that contains the user ID
3. Update `orders.py` line 73 to use the correct claim name:
   ```python
   id_user = payload.get("your_actual_claim_name")
   ```

---

### Issue 2: Orders are created but stock isn't being reduced

**Symptom**: 
- Orders are created successfully
- No errors in logs
- Ingredient stock in database doesn't change

**Cause**: 
- Recipe for cake not found in `cake_ingredients` table
- Error is silently logged as warning (not blocking)

**Solution**:
1. Verify recipe exists: 
   ```sql
   SELECT * FROM cake_ingredients WHERE id_cake = YOUR_CAKE_ID;
   ```
2. If empty, add recipe first using admin inventory endpoints
3. Check logs for "Could not fetch recipe for cake" warnings

---

### Issue 3: Orders rejected even with sufficient stock

**Symptom**: 
- POST `/orders` returns 400
- Message mentions ingredient stock

**Cause**: 
- Checking `current_stock < required_quantity` (after multiplying by quantity)
- Stock verification includes **both** individual cakes AND total quantity

**Example of failure**:
- Cake #1 needs 100g Flour per unit
- Current stock: 250g
- Order: 3 × Cake #1 = 300g needed
- **Result**: Rejected (300 > 250)

**Solution**: 
- Increase ingredient stock using `/admin/inventory/ingredients/{ingredient_name}` 
- Or reduce order quantity

---

### Issue 4: Alert not being created when order fails

**Symptom**:
- Order is rejected
- User sees error message
- But no alert in alerts table

**Cause**: 
- Alert creation is async but not awaited properly (shouldn't happen with current code)
- Or database write permission issue
- Or alerts table has RLS policies blocking inserts

**Solution**:
1. Check Supabase RLS policies on `alerts` table
2. Ensure service role can write to alerts
3. Check logs for "Error creating alert:" messages
4. Verify alerts table exists and has correct columns

---

### Issue 5: Status update doesn't reflect in frontend

**Symptom**:
- PATCH request succeeds (200 OK)
- Database updated correctly
- But alert not received by user

**Cause**: 
- User is polling old endpoint
- Alert is created but user's app doesn't fetch alerts
- Frontend not refreshing order list

**Solution**:
1. Ensure frontend subscribes to alerts via `/admin/alerts` endpoint
2. Implement real-time subscription (WebSocket/polling)
3. Frontend should refresh order list after status change

---

## Integration Checklist

- [ ] JWT token includes `id_user` claim
- [ ] Admin users have `role: "admin"` in token
- [ ] `/orders` route is registered in main FastAPI app
- [ ] `OrderCreateRequest` and `OrderResponse` schemas are importable
- [ ] Supabase connection is configured (database.py exists)
- [ ] All required tables exist: orders, order_items, cake_ingredients, ingredients, alerts
- [ ] Foreign keys are properly set up
- [ ] Test creating order with valid stock
- [ ] Test creating order with insufficient stock
- [ ] Test admin status update
- [ ] Verify alerts are created for all events

---

## Database Schema Verification

Run these queries to verify setup:

```sql
-- Check orders table structure
\d orders;

-- Check order_items table structure
\d order_items;

-- Verify foreign keys
SELECT constraint_name, table_name 
FROM information_schema.table_constraints 
WHERE table_name IN ('orders', 'order_items') 
AND constraint_type = 'FOREIGN KEY';

-- Test with sample data
INSERT INTO orders (id_client, status, total_price, delivery_address, delivery_lat_lng)
VALUES (1, 'pending', 45.99, 'Test St', '{"lat": 0, "lng": 0}');

-- Should create ID automatically
SELECT * FROM orders WHERE id_client = 1 ORDER BY created_at DESC LIMIT 1;
```

---

## Performance Considerations

### For Large Orders
If orders frequently have many items:
1. **N+1 Query Problem**: Currently queries for each ingredient
2. **Solution**: Consider batch querying with IN clause or materialized views

### For High Volume
1. **Stock verification is sync**: Could be slow with complex recipes
2. **Solution**: Consider async tasks/queue for alert creation

### Suggested Improvements
```python
# Instead of sequential queries, use IN clause:
ingredient_ids = [item.get("id_ingredient") for item in recipe_data]
ingredients = (
    supabase.table("ingredients")
    .select("*")
    .in_("id_ingredient", ingredient_ids)
    .execute()
)
# Then create dict for lookup
ingredient_map = {item["id_ingredient"]: item for item in data}
```

---

## Testing

### Test Case 1: Normal Order
```bash
# Prerequisites: id_user=1, cake_id=1 exists with recipe, sufficient stock

curl -X POST http://localhost:8000/orders \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"id_cake": 1, "quantity": 1}],
    "total_price": 25.00,
    "delivery_address": "Test St",
    "delivery_lat_lng": {"lat": 0, "lng": 0}
  }'

# Expected: 201 Created with order ID
```

### Test Case 2: Insufficient Stock
```bash
# Prerequisites: Setup recipe with 100g Flour, only 50g available

curl -X POST http://localhost:8000/orders \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"id_cake": 1, "quantity": 2}],
    "total_price": 50.00,
    "delivery_address": "Test St",
    "delivery_lat_lng": {"lat": 0, "lng": 0}
  }'

# Expected: 400 with error message
# Check alerts table for insufficient_ingredients alert
```

### Test Case 3: Admin Status Update
```bash
# Prerequisites: id_order=1 exists, user has admin role

curl -X PATCH "http://localhost:8000/admin/orders/1/status?new_status=preparing" \
  -H "Authorization: Bearer ADMIN_TOKEN"

# Expected: 200 OK with updated status
# Check alerts table for order_status_update alert
```

---

## Logging Output Examples

### Successful Order Creation
```
INFO    User user@example.com (ID 1) creating order with 1 items, total price: 25.0
DEBUG   Verifying stock for order with 1 items
DEBUG   Checking stock for cake ID 1, quantity 1
DEBUG   Found 2 ingredients in recipe for cake 1
DEBUG   Ingredient 'Flour': current=1000.0, min_threshold=500.0, needed=100.0, unit=gr
DEBUG   Ingredient 'Sugar': current=500.0, min_threshold=200.0, needed=50.0, unit=gr
INFO    Stock verification passed for order
DEBUG   Order created with ID 5
DEBUG   Order items created: 1 items
DEBUG   Reduced stock for ingredient 1 by 100.0
DEBUG   Reduced stock for ingredient 2 by 50.0
INFO    Order 5 created successfully by user 1
```

### Failed Order - Insufficient Stock
```
INFO    User user@example.com (ID 1) creating order with 1 items, total price: 25.0
INFO    Verifying stock for order with 1 items
DEBUG   Checking stock for cake ID 1, quantity 1
ERROR   Insufficient stock for ingredient 'Chocolate'. Required: 200 gr, Available: 100 gr
INFO    Alert created successfully for user 1
WARNING Stock verification failed for order by user 1: Insufficient stock...
```

---

## Rate Limiting & Security

The current implementation doesn't include rate limiting. Consider adding:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("", limiter.limit("10/minute"))
async def create_order(...):
    ...
```

Also consider:
- CORS configuration if frontend is on different domain
- Input validation for decimal places in prices
- SQL injection prevention (using Supabase client handles this)
- HTTPS-only in production
