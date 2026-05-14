import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import IngredientBase, IngredientResponse, RecipeCreate, StockAmountUpdate

# Configuration
SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# Logger
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.inventory")

router = APIRouter(prefix="/admin/inventory", tags=["Inventory"])


# --- Supabase response helper ---
def _parse_supabase_response(
    response: Any,
) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
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
# Now also returns id_user from the DB so every endpoint can scope by owner
async def get_current_admin_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
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
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        logger.warning("JWT decode failed: %s", e)
        raise credentials_exception

    email = payload.get("sub")
    role = payload.get("role")

    if not email or not isinstance(email, str):
        raise credentials_exception

    if role != "admin":
        raise admin_exception

    # Fetch id_user from DB using email so we can scope all queries
    user_response = (
        supabase.table("users")
        .select("id_user")
        .eq("email", email)
        .single()
        .execute()
    )
    user_data, user_error, _ = _parse_supabase_response(user_response)
    if user_error or not user_data:
        logger.error("Could not fetch user record for email %s: %s", email, user_error)
        raise HTTPException(status_code=500, detail="Failed to resolve user identity")

    id_user = user_data.get("id_user")
    logger.debug("Admin user %s (id_user=%s) verified", email, id_user)
    return {"email": email, "role": role, "id_user": id_user}


# --- Endpoints ---


@router.get("/ingredients", response_model=List[IngredientResponse])
async def get_stock_status(
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Get stock status of ingredients belonging to this admin only.
    """
    id_user = admin_user.get("id_user")
    logger.info("Admin user %s (id=%s) fetching their ingredients", admin_user.get("email"), id_user)

    response = (
        supabase.table("ingredients")
        .select("*")
        .eq("id_user", id_user)   # ← scope to this owner
        .execute()
    )
    data, error, _ = _parse_supabase_response(response)
    if error:
        logger.error("Error fetching ingredients: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch ingredients")
    if not data:
        return []
    logger.info("Retrieved %d ingredients for user %s", len(data), id_user)
    return data


@router.patch("/ingredients/{ingredient_name}")
async def update_ingredient_stock(
    ingredient_name: str,
    update_data: StockAmountUpdate,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Update stock of an ingredient owned by this admin.
    """
    id_user = admin_user.get("id_user")
    logger.info(
        "Admin %s updating stock for '%s' by %f",
        admin_user.get("email"), ingredient_name, update_data.amount,
    )

    # Fetch by name AND owner — prevents touching another user's ingredient
    fetch_response = (
        supabase.table("ingredients")
        .select("*")
        .ilike("name", ingredient_name)
        .eq("id_user", id_user)   # ← scope to this owner
        .single()
        .execute()
    )
    ingredient_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not ingredient_data:
        raise HTTPException(
            status_code=404,
            detail=f"Ingredient '{ingredient_name}' not found in your stock",
        )

    ingredient_id = ingredient_data.get("id_ingredient")
    current_stock = ingredient_data.get("current_stock", 0)
    new_stock = current_stock + update_data.amount

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
    """
    Admin-only: Add a new ingredient scoped to this admin's stock.
    """
    id_user = admin_user.get("id_user")
    logger.info("Admin %s adding ingredient '%s'", admin_user.get("email"), ingredient.name)

    # Check duplicate only within this user's stock
    check_response = (
        supabase.table("ingredients")
        .select("id_ingredient")
        .ilike("name", ingredient.name)
        .eq("id_user", id_user)   # ← scope to this owner
        .execute()
    )
    check_data, check_error, _ = _parse_supabase_response(check_response)
    if check_error:
        raise HTTPException(status_code=500, detail="Failed to check ingredient")
    if check_data and len(check_data) > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Ingredient '{ingredient.name}' already exists in your stock",
        )

    insert_response = (
        supabase.table("ingredients")
        .insert({
            "name": ingredient.name,
            "current_stock": ingredient.current_stock,
            "unit": ingredient.unit,
            "min_stock_threshold": ingredient.min_stock_threshold,
            "id_user": id_user,   # ← tag with owner
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
    Admin-only: Define a recipe for a cake.
    """
    logger.info(
        "Admin %s defining recipe for cake ID %d with %d ingredients",
        admin_user.get("email"), recipe.id_cake, len(recipe.items),
    )

    cake_response = (
        supabase.table("cakes")
        .select("id_cake")
        .eq("id_cake", recipe.id_cake)
        .execute()
    )
    cake_data, cake_error, _ = _parse_supabase_response(cake_response)
    if cake_error or not cake_data:
        raise HTTPException(status_code=404, detail="Cake not found")

    recipe_items = [
        {
            "id_cake": recipe.id_cake,
            "id_ingredient": item.id_ingredient,
            "required_quantity": item.required_quantity,
        }
        for item in recipe.items
    ]

    insert_response = supabase.table("cake_ingredients").insert(recipe_items).execute()
    inserted_data, insert_error, insert_status = _parse_supabase_response(insert_response)
    if insert_error or not inserted_data:
        raise HTTPException(status_code=500, detail="Failed to create recipe")

    logger.info("Created recipe for cake %d with %d ingredients", recipe.id_cake, len(inserted_data))
    return {
        "id_cake": recipe.id_cake,
        "items_created": len(inserted_data),
        "message": "Recipe created successfully",
        "recipe_items": inserted_data,
    }