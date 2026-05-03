# Requirements.txt Explanation

This document explains all the dependencies in `requirements.txt` for your Supabase bakery management API.

## 🔴 Core Dependencies (Required)

### FastAPI & Server
```
fastapi==0.104.1
```
- **Purpose**: Modern web framework for building REST APIs
- **Used for**: Route handling, request validation, response models
- **Files using it**: main.py, all route files

```
uvicorn[standard]==0.24.0
```
- **Purpose**: ASGI server to run FastAPI
- **Used for**: Running the application on localhost:8000
- **Files using it**: main.py (uvicorn.run())

---

### Database
```
supabase==2.0.3
supabase-py==2.0.3
```
- **Purpose**: Official Supabase Python client for database operations
- **Used for**: Connecting to Supabase database, querying tables (select, insert, update, delete)
- **Files using it**: database.py, all route files (supabase.table().select()...)

---

### Authentication & JWT
```
python-jose[cryptography]==3.3.0
```
- **Purpose**: JWT token creation and verification
- **Used for**: Creating access tokens, decoding JWT tokens for authentication
- **Files using it**: routes/auth.py, routes/alerts.py, routes/inventory.py, routes/orders.py, routes/cakes.py
- **Methods**: jwt.encode(), jwt.decode()

```
cryptography==41.0.7
```
- **Purpose**: Cryptographic operations
- **Used for**: Password hashing (PBKDF2), JWT encryption
- **Files using it**: routes/auth.py (hashlib.pbkdf2_hmac)

```
python-multipart==0.0.6
```
- **Purpose**: Parse multipart form data (for OAuth2)
- **Used for**: Handling login form in /auth/login endpoint
- **Files using it**: routes/auth.py (OAuth2PasswordRequestForm)

---

### Data Validation
```
pydantic==2.5.0
pydantic[email]==2.5.0
```
- **Purpose**: Data validation and settings management
- **Used for**: Defining data models (schemas), validating request/response bodies
- **Files using it**: schemas.py (all BaseModel classes), all route files
- **Examples**: UserCreate, OrderCreateRequest, CakeBase, etc.

```
email-validator==2.1.0
```
- **Purpose**: Email validation library
- **Used for**: Validating email format in Pydantic models
- **Files using it**: schemas.py (EmailStr type), routes/auth.py
- **Triggered by**: pydantic's EmailStr field

---

### Environment Variables
```
python-dotenv==1.0.0
```
- **Purpose**: Load environment variables from .env file
- **Used for**: Loading SUPABASE_URL, SUPABASE_KEY, JWT_SECRET from .env
- **Files using it**: database.py (load_dotenv())

---

## 🟢 Development Dependencies (Optional)

### Testing
```
pytest==7.4.3
```
- **Purpose**: Testing framework for unit tests
- **Used for**: Writing and running test cases
- **Optional**: Remove if you don't need automated testing

### HTTP Testing
```
httpx==0.25.2
```
- **Purpose**: HTTP client for testing API endpoints
- **Used for**: Making test requests to your FastAPI app
- **Optional**: Remove if you don't need API testing

---

## 📋 Dependency Tree

```
fastapi
├── pydantic
│   └── email-validator (for EmailStr)
├── starlette
└── typing-extensions

uvicorn
└── asgi

python-jose
└── cryptography

supabase
├── postgrest-py
├── realtime-py
├── storage-py
└── gotrue-py

python-dotenv
└── (no dependencies)

pytest (dev only)
httpx (dev only)
```

---

## 🚀 Installation

### Install all dependencies:
```bash
pip install -r requirements.txt
```

### Install only production dependencies (exclude dev tools):
```bash
pip install -r requirements.txt --exclude-groups dev
# Or manually:
pip install fastapi uvicorn supabase python-jose cryptography python-multipart pydantic email-validator python-dotenv
```

---

## 📦 Versions Explained

All versions are pinned (==) for consistency:
- **Pinned versions** ensure reproducible builds
- **All packages are stable and compatible** with each other
- **Major versions**: fastapi 0.x, pydantic 2.x, supabase 2.x

### To update packages:
```bash
pip install --upgrade -r requirements.txt
```

---

## ⚙️ Environment Setup

After installing dependencies, create a `.env` file:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-anon-key
JWT_SECRET=your-secret-key-for-jwt
LOG_LEVEL=INFO
```

---

## 🧪 Testing After Installation

### Verify all packages installed:
```bash
pip list
```

### Run the app:
```bash
python main.py
# Should see: Uvicorn running on http://0.0.0.0:8000
```

### Test an endpoint:
```bash
curl http://localhost:8000/
# Should see: {"status":"running"}
```

---

## 🔄 Using with Virtual Environment (Recommended)

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install requirements
pip install -r requirements.txt

# Run app
python main.py
```

---

## 📝 Dependency Usage Summary

| Package | Purpose | Used In |
|---------|---------|---------|
| fastapi | API framework | main.py, all routes |
| uvicorn | ASGI server | main.py |
| supabase | Database client | database.py, all routes |
| python-jose | JWT handling | auth, alerts, inventory, orders, cakes |
| cryptography | Password hashing | auth.py |
| python-multipart | Form parsing | auth.py |
| pydantic | Data validation | schemas.py, all routes |
| email-validator | Email validation | schemas.py |
| python-dotenv | Env variables | database.py |
| pytest | Testing | (dev only) |
| httpx | HTTP testing | (dev only) |
