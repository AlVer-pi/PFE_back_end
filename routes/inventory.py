import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import IngredientBase, IngredientResponse, RecipeCreate, StockAmountUpdate

SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.inventory")

router = APIRouter(prefix="/admin/inventory", tags=["Inventory"])


# --- Unit conversion ---
# Conversion factors TO the base unit:
#   weight base = g,  volume base = ml
# All other units (pcs, tbsp, tsp) are incompatible with each other — must match stored unit exactly.

UNIT_TO_BASE = {
    # weight
    "g":   ("weight", 1.0),
    "kg":  ("weight", 1000.0),
    # volume
    "ml":  ("volume", 1.0),
    "l":   ("volume", 1000.0),
    "cl":  ("volume", 10.0),
    # count / other — no conversion possible
    "pcs": ("count", 1.0),
    "tbsp":("other", 1.0),
    "tsp": ("other", 1.0),
}

def convert_to_stored_unit(amount: float, from_unit: str, to_unit: str) -> float:
    """
    Convert `amount` expressed in `from_unit` to the equivalent in `to_unit`.
    Both units must be in the same family (weight, volume).
    Raises ValueError if conversion is impossible.

    Examples:
        convert_to_stored_unit(2, "kg", "g")   → 2000.0
        convert_to_stored_unit(500, "ml", "l") → 0.5
        convert_to_stored_unit(3, "g", "g")    → 3.0
    """
    from_unit = from_unit.lower().strip()
    to_unit   = to_unit.lower().strip()

    if from_unit == to_unit:
        return amount

    from_info = UNIT_TO_BASE.get(from_unit)
    to_info   = UNIT_TO_BASE.get(to_unit)

    if not from_info:
        raise ValueError(f"Unknown unit '{from_unit}'")
    if not to_info:
        raise ValueError(f"Unknown unit '{to_unit}'")

    from_family, from_factor = from_info
    to_family,   to_factor   = to_info

    if from_family != to_family:
        raise ValueError(
            f"Cannot convert '{from_unit}' ({from_family}) to '{to_unit}' ({to_family}). "
            f"Units must be in the same family (weight or volume)."
        )

    # Convert: amount × from_factor gives base units, then ÷ to_factor gives target units
    return amount * from_factor / to_factor


# --- Supabase response helper ---
def _parse_supabase_response(response: Any) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    if response is None:
        return None, None, None
    if hasattr(response, "data") or hasattr(response, "error") or hasattr(response, "status_code"):
        return getattr(response, "data", None), getattr(response, "error", None), getattr(response, "status_code", None)
    if isinstance(response, dict):
        return response.get("data"), response.get("error"), response.get("status_code")
    return None, None, None


# --- Auth guard ---
async def get_current_admin_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
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
    role  = payload.get("role")

    if not email or not isinstance(email, str):
        raise credentials_exception
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")

    user_response = (
        supabase.table("users").select("id_user").eq("email", email).single().execute()
    )
    user_data, user_error, _ = _parse_supabase_response(user_response)
    if user_error or not user_data:
        raise HTTPException(status_code=500, detail="Failed to resolve user identity")

    id_user = user_data.get("id_user")
    logger.debug("Admin user %s (id_user=%s) verified", email, id_user)
    return {"email": email, "role": role, "id_user": id_user}


# --- Endpoints ---

@router.get("/ingredients", response_model=List[IngredientResponse])
async def get_stock_status(admin_user: Dict[str, Any] = Depends(get_current_admin_user)):
    id_user = admin_user.get("id_user")
    logger.info("Admin user %s (id=%s) fetching their ingredients", admin_user.get("email"), id_user)

    response = supabase.table("ingredients").select("*").eq("id_user", id_user).execute()
    data, error, _ = _parse_supabase_response(response)
    if error:
        raise HTTPException(status_code=500, detail="Failed to fetch ingredients")
    return data or []


@router.patch("/ingredients/{ingredient_name}")
async def update_ingredient_stock(
    ingredient_name: str,
    update_data: StockAmountUpdate,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    id_user = admin_user.get("id_user")

    fetch_response = (
        supabase.table("ingredients")
        .select("*")
        .ilike("name", ingredient_name)
        .eq("id_user", id_user)
        .single()
        .execute()
    )
    ingredient_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not ingredient_data:
        raise HTTPException(status_code=404, detail=f"Ingredient '{ingredient_name}' not found in your stock")

    ingredient_id = ingredient_data.get("id_ingredient")
    current_stock = ingredient_data.get("current_stock", 0)
    new_stock     = current_stock + update_data.amount

    if new_stock < 0:
        raise HTTPException(status_code=400, detail="Stock cannot be negative")

    update_response = (
        supabase.table("ingredients")
        .update({"current_stock": new_stock})
        .eq("id_ingredient", ingredient_id)
        .execute()
    )
    _, update_error, _ = _parse_supabase_response(update_response)
    if update_error:
        raise HTTPException(status_code=500, detail="Failed to update stock")

    logger.info("Updated '%s' stock: %f → %f", ingredient_data.get("name"), current_stock, new_stock)
    return {
        "id_ingredient": ingredient_id,
        "name": ingredient_data.get("name"),
        "previous_stock": current_stock,
        "new_stock": new_stock,
        "message": "Stock updated successfully",
    }


@router.post("/ingredients/add", status_code=status.HTTP_201_CREATED)
async def add_ingredient(
    ingredient: IngredientBase,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    id_user = admin_user.get("id_user")

    check_response = (
        supabase.table("ingredients")
        .select("id_ingredient")
        .ilike("name", ingredient.name)
        .eq("id_user", id_user)
        .execute()
    )
    check_data, check_error, _ = _parse_supabase_response(check_response)
    if check_error:
        raise HTTPException(status_code=500, detail="Failed to check ingredient")
    if check_data and len(check_data) > 0:
        raise HTTPException(status_code=409, detail=f"Ingredient '{ingredient.name}' already exists in your stock")

    insert_response = (
        supabase.table("ingredients")
        .insert({
            "name": ingredient.name,
            "current_stock": ingredient.current_stock,
            "unit": ingredient.unit,
            "min_stock_threshold": ingredient.min_stock_threshold,
            "id_user": id_user,
        })
        .execute()
    )
    inserted_data, insert_error, _ = _parse_supabase_response(insert_response)
    if insert_error or not inserted_data:
        raise HTTPException(status_code=500, detail="Failed to add ingredient")

    ingredient_id = inserted_data[0].get("id_ingredient")
    logger.info("Added ingredient '%s' (ID %d) for user %s", ingredient.name, ingredient_id, id_user)

    return {
        "id_ingredient": ingredient_id,
        "name": ingredient.name,
        "current_stock": ingredient.current_stock,
        "unit": ingredient.unit,
        "min_stock_threshold": ingredient.min_stock_threshold,
        "message": "Ingredient added successfully",
    }


@router.post("/recipes", status_code=status.HTTP_201_CREATED)
async def define_recipe(
    recipe: RecipeCreate,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Define a recipe for a cake.
    Each item can specify a unit — if it differs from the ingredient's stored unit,
    the required_quantity is automatically converted and stored in the ingredient's unit.
    This ensures deduction logic always works in the same unit as the stock.
    """
    logger.info(
        "Admin %s defining recipe for cake ID %d with %d ingredients",
        admin_user.get("email"), recipe.id_cake, len(recipe.items),
    )

    cake_response = (
        supabase.table("cakes").select("id_cake").eq("id_cake", recipe.id_cake).execute()
    )
    cake_data, cake_error, _ = _parse_supabase_response(cake_response)
    if cake_error or not cake_data:
        raise HTTPException(status_code=404, detail="Cake not found")

    recipe_items = []
    for item in recipe.items:
        # Fetch the ingredient to get its stored unit
        ing_resp = (
            supabase.table("ingredients")
            .select("id_ingredient, name, unit")
            .eq("id_ingredient", item.id_ingredient)
            .single()
            .execute()
        )
        ing_data, ing_error, _ = _parse_supabase_response(ing_resp)
        if ing_error or not ing_data:
            raise HTTPException(
                status_code=404,
                detail=f"Ingredient ID {item.id_ingredient} not found"
            )

        stored_unit  = ing_data.get("unit", "").lower().strip()
        recipe_unit  = (item.unit or stored_unit).lower().strip()
        quantity     = item.required_quantity

        # Convert if the recipe unit differs from the stored unit
        if recipe_unit != stored_unit:
            try:
                quantity = convert_to_stored_unit(quantity, recipe_unit, stored_unit)
                logger.info(
                    "Converted recipe quantity for '%s': %.4f %s → %.4f %s",
                    ing_data.get("name"), item.required_quantity, recipe_unit, quantity, stored_unit
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        recipe_items.append({
            "id_cake": recipe.id_cake,
            "id_ingredient": item.id_ingredient,
            "required_quantity": quantity,   # always stored in the ingredient's own unit
        })

    insert_response = supabase.table("cake_ingredients").insert(recipe_items).execute()
    inserted_data, insert_error, _ = _parse_supabase_response(insert_response)
    if insert_error or not inserted_data:
        raise HTTPException(status_code=500, detail="Failed to create recipe")

    logger.info("Created recipe for cake %d with %d ingredients", recipe.id_cake, len(inserted_data))
    return {
        "id_cake": recipe.id_cake,
        "items_created": len(inserted_data),
        "message": "Recipe created successfully",
        "recipe_items": inserted_data,
    }