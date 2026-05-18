import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr

from database import supabase
from schemas import UserCreate, UserResponse

SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
if not SECRET_KEY:
    logging.getLogger("routes.auth").warning(
        "JWT secret not configured. Set JWT_SECRET in environment for production."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480

_PBKDF2_ITERS = 200_000
_SALT_BYTES = 16

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
router = APIRouter(prefix="/auth", tags=["Authentication"])

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.auth")


# --- Supabase response helper ---
def _parse_supabase_response(response: Any) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    if response is None:
        return None, None, None
    if hasattr(response, "data") or hasattr(response, "error") or hasattr(response, "status_code"):
        return getattr(response, "data", None), getattr(response, "error", None), getattr(response, "status_code", None)
    if isinstance(response, dict):
        return response.get("data"), response.get("error"), response.get("status_code")
    return None, None, None


# --- Password hashing ---
def _pbkdf2_hash(password: str, iters: int = _PBKDF2_ITERS) -> str:
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def _pbkdf2_verify(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2":
            return False
        _, iters_s, salt_hex, dk_hex = parts
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return secrets.compare_digest(derived, expected)
    except Exception as e:
        logger.exception("PBKDF2 verify error: %s", e)
        return False


def get_password_hash(password: str) -> str:
    try:
        return _pbkdf2_hash(password)
    except Exception as e:
        logger.exception("Failed to hash password: %s", e)
        raise HTTPException(status_code=500, detail="Password hashing failed")


def verify_password(plain_password: str, stored_hash: str) -> bool:
    if not isinstance(plain_password, str) or not isinstance(stored_hash, str):
        return False
    if stored_hash.startswith("pbkdf2$"):
        return _pbkdf2_verify(plain_password, stored_hash)
    return False


# --- JWT ---
def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# --- Current user helper ---
async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Please provide a valid Bearer token in Authorization header.",
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

    response = supabase.table("users").select("*").eq("email", email).execute()
    data, error, status_code = _parse_supabase_response(response)
    if error:
        raise HTTPException(status_code=500, detail="Database error")
    if not data:
        raise HTTPException(status_code=404, detail="User not found")
    user = data[0]
    if not isinstance(user, dict):
        raise HTTPException(status_code=500, detail="Invalid user record format")
    return user


# --- Request models ---
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UpdateProfileRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# --- Endpoints ---

@router.post("/register", response_model=UserResponse)
async def register(user_in: UserCreate):
    print(f"!!! REGISTER REACHED WITH DATA: {user_in}")
    check_resp = supabase.table("users").select("email").eq("email", user_in.email).execute()
    existing_data, check_err, _ = _parse_supabase_response(check_resp)
    if check_err:
        raise HTTPException(status_code=500, detail="Database error")
    if existing_data:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_dict = user_in.dict()
    plain_password = user_dict.get("password_hash")
    if not plain_password or not isinstance(plain_password, str):
        raise HTTPException(status_code=400, detail="Password is required")
    user_dict["role"] = "admin"
    user_dict["password_hash"] = get_password_hash(plain_password)

    insert_resp = supabase.table("users").insert(user_dict).execute()
    inserted_data, insert_err, insert_status = _parse_supabase_response(insert_resp)
    if insert_err:
        raise HTTPException(status_code=500, detail="Insert failed")
    if not inserted_data:
        raise HTTPException(status_code=500, detail="Failed to create user")
    return inserted_data[0]


@router.post("/login")
async def login(login_data: OAuth2PasswordRequestForm = Depends()):
    resp = supabase.table("users").select("*").eq("email", login_data.username).execute()
    data, err, _ = _parse_supabase_response(resp)
    if err or not data:
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    user = data[0]
    stored_hash = user.get("password_hash")

    if not verify_password(login_data.password, stored_hash):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    role = user.get("role") if isinstance(user.get("role"), str) else "client"
    access_token = create_access_token(data={"sub": user.get("email"), "role": role})
    return {"access_token": access_token, "token_type": "bearer", "user_role": role}


@router.get("/me", response_model=UserResponse)
async def read_users_me(token: str = Depends(oauth2_scheme)):
    user = await get_current_user(token)
    logger.info("User %s accessed /me endpoint", user.get("email"))
    return user


@router.put("/me", response_model=UserResponse)
async def update_profile(
    payload: UpdateProfileRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Update the authenticated user's profile info.
    Only non-null fields in the request body are updated.
    """
    id_user = current_user.get("id_user")
    logger.info("User %s updating profile", current_user.get("email"))

    # Build update dict from only the fields that were actually provided
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    resp = (
        supabase.table("users")
        .update(updates)
        .eq("id_user", id_user)
        .execute()
    )
    data, error, _ = _parse_supabase_response(resp)
    if error:
        logger.error("Error updating profile for user %s: %s", id_user, error)
        raise HTTPException(status_code=500, detail="Failed to update profile")

    # Return fresh user record
    fresh_resp = supabase.table("users").select("*").eq("id_user", id_user).single().execute()
    fresh_data, fresh_error, _ = _parse_supabase_response(fresh_resp)
    if fresh_error or not fresh_data:
        raise HTTPException(status_code=500, detail="Failed to fetch updated profile")

    logger.info("Profile updated for user %s", current_user.get("email"))
    return fresh_data


@router.patch("/me/password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Change the authenticated user's password.
    Requires current password for verification before updating.
    """
    id_user = current_user.get("id_user")
    logger.info("User %s attempting password change", current_user.get("email"))

    # Verify current password
    stored_hash = current_user.get("password_hash", "")
    if not verify_password(payload.current_password, stored_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    new_hash = get_password_hash(payload.new_password)

    resp = (
        supabase.table("users")
        .update({"password_hash": new_hash})
        .eq("id_user", id_user)
        .execute()
    )
    _, error, _ = _parse_supabase_response(resp)
    if error:
        logger.error("Error changing password for user %s: %s", id_user, error)
        raise HTTPException(status_code=500, detail="Failed to update password")

    logger.info("Password changed successfully for user %s", current_user.get("email"))
    return {"message": "Password changed successfully"}