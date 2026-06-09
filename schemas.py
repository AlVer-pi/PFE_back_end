from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, EmailStr


# --- USERS ---
class UserBase(BaseModel):
    email: EmailStr
    last_name: str
    first_name: str
    phone_number: Optional[str] = None
    address: Optional[str] = None
    role: str = "client"


class UserCreate(UserBase):
    password_hash: str


class UserResponse(UserBase):
    id_user: int

    class Config:
        from_attributes = True


# --- INGREDIENTS ---
class IngredientBase(BaseModel):
    name: str
    current_stock: float
    unit: str
    min_stock_threshold: float


class IngredientResponse(IngredientBase):
    id_ingredient: int

    class Config:
        from_attributes = True


class StockAmountUpdate(BaseModel):
    amount: float


class IngredientStockUpdate(BaseModel):
    name: str
    amount: float


# --- CAKES ---
class CakeBase(BaseModel):
    name: str
    photo_url: Optional[str] = None
    description: Optional[str] = None
    price: Decimal
    average_rating: float = 0.0
    is_available: bool = True


class CakeResponse(CakeBase):
    id_cake: int

    class Config:
        from_attributes = True


# --- CAKE DETAILS WITH RECIPE ---
class IngredientDetail(BaseModel):
    name: str
    required_quantity: float
    unit: str


class CakeWithRecipe(CakeResponse):
    recipe: List[IngredientDetail] = []


# --- RECIPES ---
class RecipeBase(BaseModel):
    id_cake: int
    id_ingredient: int
    required_quantity: float
    unit: Optional[str] = None  # unit used in the recipe (may differ from stored unit)


class RecipeResponse(RecipeBase):
    id_recipe: int

    class Config:
        from_attributes = True


class RecipeItemCreate(BaseModel):
    id_ingredient: int
    required_quantity: float
    unit: Optional[str] = None  # if omitted, defaults to the ingredient's stored unit


class RecipeCreate(BaseModel):
    id_cake: int
    items: List[RecipeItemCreate]


# --- ORDERS ---
class OrderItemBase(BaseModel):
    id_cake: int
    quantity: int


class OrderBase(BaseModel):
    status: str = "pending"
    total_price: Decimal
    delivery_address: str
    delivery_lat_lng: Optional[str] = None


class OrderCreateRequest(BaseModel):
    items: List[OrderItemBase]
    total_price: Decimal
    delivery_address: str
    delivery_lat_lng: Optional[str] = None


class OrderResponse(OrderBase):
    id_order: int
    id_client: int

    class Config:
        from_attributes = True


# --- ALERTS ---
class AlertBase(BaseModel):
    id_user: int
    type: str
    message: str
    is_read: bool = False


class AlertResponse(AlertBase):
    id_alert: int
    created_at: datetime

    class Config:
        from_attributes = True