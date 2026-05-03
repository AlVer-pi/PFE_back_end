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


@router.get("/ingredients", response_model=List[IngredientResponse])
async def get_stock_status(
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Get stock status of all ingredients.
    Lists all ingredients and their current quantities.
    """
    logger.info(
        "Admin user %s fetching ingredient stock status", admin_user.get("email")
    )
    response = supabase.table("ingredients").select("*").execute()
    data, error, status_code = _parse_supabase_response(response)
    if error:
        logger.error("Error fetching ingredients: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch ingredients")
    if not data:
        logger.info("No ingredients found")
        return []
    logger.info("Retrieved %d ingredients", len(data))
    return data


@router.patch("/ingredients/{ingredient_name}")
async def update_ingredient_stock(
    ingredient_name: str,
    update_data: StockAmountUpdate,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """
    Admin-only: Update ingredient stock by ingredient name.
    Updates the current_stock of an ingredient by the specified amount.
    Can be positive (restock) or negative (usage).
    The ingredient name is used to identify which row to update.
    """
    logger.info(
        "Admin user %s updating stock for ingredient name '%s' by amount %f",
        admin_user.get("email"),
        ingredient_name,
        update_data.amount,
    )

    # Fetch ingredient by name (case-insensitive)
    fetch_response = (
        supabase.table("ingredients")
        .select("*")
        .ilike("name", ingredient_name)
        .single()
        .execute()
    )
    ingredient_data, fetch_error, _ = _parse_supabase_response(fetch_response)
    if fetch_error or not ingredient_data:
        logger.error(
            "Error fetching ingredient with name '%s': %s", ingredient_name, fetch_error
        )
        raise HTTPException(
            status_code=404,
            detail=f"Ingredient with name '{ingredient_name}' not found",
        )

    # Calculate new stock
    ingredient_id = ingredient_data.get("id_ingredient")
    current_stock = ingredient_data.get("current_stock", 0)
    new_stock = current_stock + update_data.amount

    if new_stock < 0:
        logger.warning(
            "Stock update would result in negative stock for ingredient '%s'",
            ingredient_name,
        )
        raise HTTPException(status_code=400, detail="Stock cannot be negative")

    # Update stock
    update_response = (
        supabase.table("ingredients")
        .update({"current_stock": new_stock})
        .eq("id_ingredient", ingredient_id)
        .execute()
    )
    updated_data, update_error, _ = _parse_supabase_response(update_response)
    if update_error:
        logger.error("Error updating ingredient stock: %s", update_error)
        raise HTTPException(status_code=500, detail="Failed to update stock")

    logger.info(
        "Successfully updated ingredient '%s' (ID %d) stock from %f to %f",
        ingredient_data.get("name"),
        ingredient_id,
        current_stock,
        new_stock,
    )
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
    Admin-only: Add a new ingredient to the inventory.
    Checks if ingredient already exists before inserting.
    Returns the created ingredient ID and success message.
    """
    logger.info(
        "Admin user %s adding new ingredient: '%s'",
        admin_user.get("email"),
        ingredient.name,
    )

    # Check if ingredient already exists (case-insensitive)
    check_response = (
        supabase.table("ingredients")
        .select("id_ingredient")
        .ilike("name", ingredient.name)
        .execute()
    )
    check_data, check_error, _ = _parse_supabase_response(check_response)
    if check_error:
        logger.error("Error checking ingredient existence: %s", check_error)
        raise HTTPException(status_code=500, detail="Failed to check ingredient")

    if check_data and len(check_data) > 0:
        logger.warning(
            "Admin user %s attempted to add duplicate ingredient: '%s'",
            admin_user.get("email"),
            ingredient.name,
        )
        raise HTTPException(
            status_code=409,
            detail=f"Ingredient with name '{ingredient.name}' already exists",
        )

    # Insert new ingredient
    insert_response = (
        supabase.table("ingredients")
        .insert(
            {
                "name": ingredient.name,
                "current_stock": ingredient.current_stock,
                "unit": ingredient.unit,
                "min_stock_threshold": ingredient.min_stock_threshold,
            }
        )
        .execute()
    )
    inserted_data, insert_error, _ = _parse_supabase_response(insert_response)
    if insert_error:
        logger.error("Error adding ingredient: %s", insert_error)
        raise HTTPException(status_code=500, detail="Failed to add ingredient")

    if not inserted_data or len(inserted_data) == 0:
        logger.error("Insert returned no data for ingredient '%s'", ingredient.name)
        raise HTTPException(status_code=500, detail="Failed to add ingredient")

    ingredient_id = inserted_data[0].get("id_ingredient")
    logger.info(
        "Successfully added ingredient '%s' with ID %d",
        ingredient.name,
        ingredient_id,
    )

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
    Links a cake to multiple ingredients with their required quantities.
    Creates multiple rows in cake_ingredients table for the same id_cake.
    """
    logger.info(
        "Admin user %s defining recipe for cake ID %d with %d ingredients",
        admin_user.get("email"),
        recipe.id_cake,
        len(recipe.items),
    )

    # Verify cake exists
    cake_response = (
        supabase.table("cakes")
        .select("id_cake")
        .eq("id_cake", recipe.id_cake)
        .execute()
    )
    cake_data, cake_error, _ = _parse_supabase_response(cake_response)
    if cake_error or not cake_data:
        logger.error("Error verifying cake: %s", cake_error)
        raise HTTPException(status_code=404, detail="Cake not found")

    # Prepare recipe items for insertion
    recipe_items = []
    for item in recipe.items:
        recipe_items.append(
            {
                "id_cake": recipe.id_cake,
                "id_ingredient": item.id_ingredient,
                "required_quantity": item.required_quantity,
            }
        )

    # Insert all recipe items
    insert_response = supabase.table("cake_ingredients").insert(recipe_items).execute()
    inserted_data, insert_error, insert_status = _parse_supabase_response(
        insert_response
    )
    if insert_error:
        logger.error("Error creating recipe: %s", insert_error)
        raise HTTPException(status_code=500, detail="Failed to create recipe")

    if not inserted_data:
        logger.warning("Insert returned no data, status=%s", insert_status)
        raise HTTPException(status_code=500, detail="Failed to create recipe")

    logger.info(
        "Successfully created recipe for cake %d with %d ingredients",
        recipe.id_cake,
        len(inserted_data),
    )
    return {
        "id_cake": recipe.id_cake,
        "items_created": len(inserted_data),
        "message": "Recipe created successfully",
        "recipe_items": inserted_data,
    }
