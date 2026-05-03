import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import CakeBase, CakeResponse, CakeWithRecipe

# Configuration
SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# Logger
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.cakes")

router = APIRouter(prefix="/cakes", tags=["Cakes"])


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


# --- Current user check (login required) ---
async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    Verify that the user is logged in.
    Raises 401 if token is invalid or user not found.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Please provide a valid Bearer token in Authorization header.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        logger.debug("Decoding JWT token")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        logger.debug("JWT decoded successfully")
    except JWTError as e:
        logger.warning("JWT decode failed: %s", e)
        raise credentials_exception

    email = payload.get("sub")
    logger.debug("Extracted email from token: %s", email)
    if not email or not isinstance(email, str):
        logger.warning("Email not found in token or is not a string")
        raise credentials_exception

    logger.debug("User %s verified", email)
    return {"email": email}


# --- Endpoints ---


@router.get("/", response_model=List[CakeResponse])
async def list_cakes(
    limit: int = 10,
    offset: int = 0,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    List cakes with pagination. Requires user login.
    """
    logger.info(
        "User %s fetching cakes with limit=%d and offset=%d",
        current_user.get("email"),
        limit,
        offset,
    )
    response = (
        supabase.table("cakes").select("*").range(offset, offset + limit - 1).execute()
    )
    data, error, status_code = _parse_supabase_response(response)
    if error:
        logger.error("Error fetching cakes: %s", error)
        raise HTTPException(status_code=500, detail="Failed to fetch cakes")
    return data


@router.get("/{id_cake}", response_model=CakeWithRecipe)
async def get_cake_details(
    id_cake: int, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Get cake details along with its ingredients (recipe). Requires user login.
    """
    logger.info(
        "User %s fetching details for cake ID: %d",
        current_user.get("email"),
        id_cake,
    )
    # Fetch cake details
    cake_response = (
        supabase.table("cakes").select("*").eq("id_cake", id_cake).single().execute()
    )
    cake_data, cake_error, _ = _parse_supabase_response(cake_response)
    if cake_error or not cake_data:
        logger.error("Error fetching cake: %s", cake_error)
        raise HTTPException(status_code=404, detail="Cake not found")

    # Fetch recipe with ingredient names using join
    recipe_response = (
        supabase.table("cake_ingredients")
        .select("id_recipe, id_cake, required_quantity, ingredients(name, unit)")
        .eq("id_cake", id_cake)
        .execute()
    )
    recipe_data, recipe_error, _ = _parse_supabase_response(recipe_response)
    if recipe_error:
        logger.error("Error fetching recipe: %s", recipe_error)
        raise HTTPException(status_code=500, detail="Failed to fetch recipe")

    # Extract ingredient names and details from recipe data
    ingredients_list = []
    if recipe_data:
        for item in recipe_data:
            ingredient_obj = item.get("ingredients")
            if ingredient_obj:
                ingredient_info = {
                    "name": ingredient_obj.get("name"),
                    "required_quantity": item.get("required_quantity"),
                    "unit": ingredient_obj.get("unit"),
                }
                ingredients_list.append(ingredient_info)
        logger.info(
            "Extracted %d ingredients for cake ID %d", len(ingredients_list), id_cake
        )
    else:
        logger.info("No ingredients found for cake ID %d", id_cake)

    # Attach recipe to cake details
    cake_data["recipe"] = ingredients_list
    return cake_data


@router.post(
    "/admin/cakes", response_model=CakeResponse, status_code=status.HTTP_201_CREATED
)
async def create_cake(
    cake: CakeBase, admin_user: Dict[str, Any] = Depends(get_current_admin_user)
):
    """
    Admin-only: Add a new cake and its photo URL.
    Requires admin role to access this endpoint.
    """
    logger.info(
        "Admin user %s creating a new cake: %s", admin_user.get("email"), cake.name
    )
    # Insert cake into the database
    cake_data = cake.dict()
    cake_data["price"] = float(
        cake_data["price"]
    )  # Convert Decimal to float for JSON serialization
    response = supabase.table("cakes").insert(cake_data).execute()
    data, error, status_code = _parse_supabase_response(response)
    if error:
        logger.error("Error creating cake: %s", error)
        raise HTTPException(status_code=500, detail="Failed to create cake")
    if not data:
        logger.warning("Insert returned no data, status=%s", status_code)
        raise HTTPException(status_code=500, detail="Failed to create cake")
    return data[0]
