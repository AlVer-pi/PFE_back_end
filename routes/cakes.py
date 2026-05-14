import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from database import supabase
from schemas import CakeBase, CakeResponse, CakeWithRecipe

SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.cakes")

router = APIRouter(prefix="/cakes", tags=["Cakes"])


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


# --- Admin guard (returns id_user) ---
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
    role = payload.get("role")

    if not email or not isinstance(email, str):
        raise credentials_exception
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")

    id_user = _get_id_user(email)
    logger.debug("Admin %s (id_user=%s) verified", email, id_user)
    return {"email": email, "role": role, "id_user": id_user}


# --- User guard (any logged-in user, returns id_user) ---
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
    if not email or not isinstance(email, str):
        raise credentials_exception

    id_user = _get_id_user(email)
    return {"email": email, "id_user": id_user}


# --- Endpoints ---


@router.get("/", response_model=List[CakeResponse])
async def list_cakes(
    limit: int = 50,
    offset: int = 0,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List cakes belonging to this user only."""
    id_user = current_user.get("id_user")
    logger.info("User %s fetching their cakes", current_user.get("email"))

    response = (
        supabase.table("cakes")
        .select("*")
        .eq("id_user", id_user)          # ← scoped to owner
        .range(offset, offset + limit - 1)
        .execute()
    )
    data, error, _ = _parse_supabase_response(response)
    if error:
        raise HTTPException(status_code=500, detail="Failed to fetch cakes")
    return data or []


@router.get("/{id_cake}", response_model=CakeWithRecipe)
async def get_cake_details(
    id_cake: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get cake details + recipe. Only accessible if this cake belongs to the user."""
    id_user = current_user.get("id_user")

    cake_response = (
        supabase.table("cakes")
        .select("*")
        .eq("id_cake", id_cake)
        .eq("id_user", id_user)          # ← scoped to owner
        .single()
        .execute()
    )
    cake_data, cake_error, _ = _parse_supabase_response(cake_response)
    if cake_error or not cake_data:
        raise HTTPException(status_code=404, detail="Cake not found")

    recipe_response = (
        supabase.table("cake_ingredients")
        .select("id_recipe, id_cake, required_quantity, ingredients(name, unit)")
        .eq("id_cake", id_cake)
        .execute()
    )
    recipe_data, recipe_error, _ = _parse_supabase_response(recipe_response)
    if recipe_error:
        raise HTTPException(status_code=500, detail="Failed to fetch recipe")

    ingredients_list = []
    if recipe_data:
        for item in recipe_data:
            ingredient_obj = item.get("ingredients")
            if ingredient_obj:
                ingredients_list.append({
                    "name": ingredient_obj.get("name"),
                    "required_quantity": item.get("required_quantity"),
                    "unit": ingredient_obj.get("unit"),
                })

    cake_data["recipe"] = ingredients_list
    return cake_data


@router.post("/admin/cakes", response_model=CakeResponse, status_code=status.HTTP_201_CREATED)
async def create_cake(
    cake: CakeBase,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Admin-only: Add a new cake tagged to this admin."""
    id_user = admin_user.get("id_user")
    logger.info("Admin %s creating cake '%s'", admin_user.get("email"), cake.name)

    cake_data = cake.dict()
    cake_data["price"] = float(cake_data["price"])
    cake_data["id_user"] = id_user          # ← tag with owner

    response = supabase.table("cakes").insert(cake_data).execute()
    data, error, _ = _parse_supabase_response(response)
    if error or not data:
        raise HTTPException(status_code=500, detail="Failed to create cake")
    return data[0]


@router.delete("/{id_cake}", status_code=status.HTTP_200_OK)
async def delete_cake(
    id_cake: int,
    admin_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Admin-only: Delete a cake. Only works if the cake belongs to this admin."""
    id_user = admin_user.get("id_user")
    logger.info("Admin %s deleting cake ID %d", admin_user.get("email"), id_cake)

    # Verify cake exists AND belongs to this admin
    check_resp = (
        supabase.table("cakes")
        .select("id_cake")
        .eq("id_cake", id_cake)
        .eq("id_user", id_user)          # ← scoped to owner
        .execute()
    )
    cake_exists, error, _ = _parse_supabase_response(check_resp)
    if error or not cake_exists:
        raise HTTPException(status_code=404, detail="Cake not found")

    response = supabase.table("cakes").delete().eq("id_cake", id_cake).execute()
    _, error, _ = _parse_supabase_response(response)
    if error:
        raise HTTPException(status_code=500, detail="Failed to delete cake")

    return {"message": f"Cake {id_cake} successfully removed from menu"}