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

# Local imports
from database import supabase
from schemas import UserCreate, UserResponse

# Configuration
SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_KEY") or ""
if not SECRET_KEY:
    # Don't raise here to allow test/dev usage; endpoints will fail on token ops if missing.
    logging.getLogger("routes.auth").warning(
        "JWT secret not configured. Set JWT_SECRET in environment for production."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

# PBKDF2 parameters
_PBKDF2_ITERS = 200_000
_SALT_BYTES = 16

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Logger
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("routes.auth")


# --- Supabase response helper ---
def _parse_supabase_response(
    response: Any,
) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    """
    Normalize supabase client response to (data, error, status_code).
    The supabase client may return an object with attributes `.data`, `.error`, `.status_code`
    or a dict with the same keys.
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
        status = getattr(response, "status_code", None)
        return data, error, status
    if isinstance(response, dict):
        return response.get("data"), response.get("error"), response.get("status_code")
    return None, None, None


# --- Password hashing (PBKDF2-HMAC-SHA256) ---
def _pbkdf2_hash(password: str, iters: int = _PBKDF2_ITERS) -> str:
    """
    Produce a stored string in the format:
      pbkdf2$<iters>$<salt_hex>$<dk_hex>

    Uses pbkdf2_hmac with SHA-256.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def _pbkdf2_verify(password: str, stored: str) -> bool:
    """
    Verify a password against pbkdf2 stored format.
    Returns True on match, False otherwise.
    """
    try:
        parts = stored.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2":
            logger.debug("Stored password format is not pbkdf2")
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
    """
    Public helper to create a password hash string to store in DB.
    Uses PBKDF2-HMAC-SHA256 with a random salt.
    """
    try:
        return _pbkdf2_hash(password)
    except Exception as e:
        logger.exception("Failed to hash password: %s", e)
        raise HTTPException(status_code=500, detail="Password hashing failed")


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """
    Public helper to verify password. Accepts stored pbkdf2 format.
    Returns False on invalid inputs or verification error.
    """
    if not isinstance(plain_password, str) or not isinstance(stored_hash, str):
        return False
    # Only supporting pbkdf2 format here (bcrypt removed entirely per request).
    if stored_hash.startswith("pbkdf2$"):
        return _pbkdf2_verify(plain_password, stored_hash)
    # Unknown format - do not verify
    logger.debug("Unknown stored hash format during verify")
    return False


# --- JWT helpers ---
def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return token


# --- Login request model ---
class LoginRequest(BaseModel):
    """Request model for user login with email and password."""

    email: EmailStr
    password: str


# --- Current user helper ---
async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
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

    logger.debug("Fetching user from Supabase with email: %s", email)
    response = supabase.table("users").select("*").eq("email", email).execute()
    data, error, status_code = _parse_supabase_response(response)
    if error:
        logger.error(
            "Supabase error fetching user: %s (status: %s)", error, status_code
        )
        raise HTTPException(status_code=500, detail="Database error")
    if not data:
        logger.warning("User not found in database for email: %s", email)
        raise HTTPException(status_code=404, detail="User not found")
    user = data[0]
    if not isinstance(user, dict):
        logger.error("User record is not a dictionary: %s", type(user))
        raise HTTPException(status_code=500, detail="Invalid user record format")
    logger.debug("Successfully retrieved user: %s", user.get("email"))
    return user


# --- Endpoints ---


@router.post("/register", response_model=UserResponse)
async def register(user_in: UserCreate):
    """
    Register a new user.

    Note: per current schemas, incoming model field `password_hash` carries the plaintext
    password. We prehash it with PBKDF2 and store the resulting string in the same column.
    """
    # 1) Check existing user
    check_resp = (
        supabase.table("users").select("email").eq("email", user_in.email).execute()
    )
    existing_data, check_err, _ = _parse_supabase_response(check_resp)
    if check_err:
        logger.error("Supabase error when checking existing user: %s", check_err)
        raise HTTPException(status_code=500, detail="Database error")
    if existing_data:
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2) Prepare data and hash password
    user_dict = user_in.dict()
    plain_password = user_dict.get("password_hash")
    if not plain_password or not isinstance(plain_password, str):
        raise HTTPException(status_code=400, detail="Password is required")

    # Hash using PBKDF2 and store the result string
    hashed = get_password_hash(plain_password)
    user_dict["password_hash"] = hashed

    # 3) Insert user
    insert_resp = supabase.table("users").insert(user_dict).execute()
    inserted_data, insert_err, insert_status = _parse_supabase_response(insert_resp)
    if insert_err:
        logger.error("Supabase error on insert: %s", insert_err)
        raise HTTPException(status_code=500, detail="Insert failed")
    if not inserted_data:
        # No rows returned — possibly RLS or permissions issue
        logger.warning("Insert returned no data, status=%s", insert_status)
        raise HTTPException(status_code=500, detail="Failed to create user")
    created_user = inserted_data[0]
    return created_user


@router.post("/login")
# 2. Update login to accept OAuth2PasswordRequestForm
# This allows Swagger's "Authorize" form to talk to this endpoint.
async def login(login_data: OAuth2PasswordRequestForm = Depends()):
    """
    Authenticate user.
    Note: OAuth2PasswordRequestForm uses .username even for emails.
    """
    # Use login_data.username (which will be the email entered in Swagger)
    resp = (
        supabase.table("users").select("*").eq("email", login_data.username).execute()
    )

    data, err, _ = _parse_supabase_response(resp)
    if err or not data:
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    user = data[0]
    stored_hash = user.get("password_hash")

    # Use login_data.password
    if not verify_password(login_data.password, stored_hash):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    role = user.get("role") if isinstance(user.get("role"), str) else "client"
    access_token = create_access_token(data={"sub": user.get("email"), "role": role})

    # MUST return "access_token" and "token_type" for OAuth2 spec
    return {"access_token": access_token, "token_type": "bearer", "user_role": role}


@router.get("/me", response_model=UserResponse)
# 3. Apply the dependency here
async def read_users_me(token: str = Depends(oauth2_scheme)):
    """
    Now, clicking 'Authorize' and logging in will automatically
    populate the 'token' variable here.
    """
    # Re-use your existing logic to decode the token and fetch user
    user = await get_current_user(token)
    logger.info("User %s accessed /me endpoint", user.get("email"))
    return user
