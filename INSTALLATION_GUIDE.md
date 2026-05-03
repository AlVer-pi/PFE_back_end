# Quick Installation Guide

## Prerequisites
- Python 3.8+ installed
- pip package manager
- Git (optional)

---

## ⚡ Quick Start (5 minutes)

### 1. Clone/Download the Project
```bash
cd your-project-directory
```

### 2. Create Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Setup Environment Variables
Create a `.env` file in the `code` directory:
```
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-key
JWT_SECRET=your-secret-key-change-this
LOG_LEVEL=INFO
```

### 5. Run the Server
```bash
python main.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 6. Test the API
Open in browser: http://localhost:8000
- **Swagger docs**: http://localhost:8000/docs
- **Health check**: http://localhost:8000/

---

## 📋 Installation Checklist

- [ ] Python 3.8+ installed
- [ ] Virtual environment created
- [ ] Virtual environment activated
- [ ] `pip install -r requirements.txt` completed
- [ ] `.env` file created with Supabase credentials
- [ ] Server runs without errors
- [ ] Swagger docs accessible at /docs

---

## 🐛 Troubleshooting

### "pip: command not found"
```bash
# Use python3 instead
python3 -m pip install -r requirements.txt
```

### "ModuleNotFoundError: No module named 'fastapi'"
```bash
# Ensure virtual environment is activated
# Then reinstall
pip install -r requirements.txt
```

### "SUPABASE_URL not found in .env"
- Check `.env` file exists in code directory
- Check it has the correct variables:
  ```
  SUPABASE_URL=...
  SUPABASE_KEY=...
  JWT_SECRET=...
  ```

### Port 8000 already in use
```bash
# Use different port
python main.py --port 8001
# Or in main.py: uvicorn.run(..., port=8001)
```

---

## 📦 What Gets Installed

```
✓ FastAPI - Web framework
✓ Uvicorn - ASGI server
✓ Supabase - Database client
✓ Python-jose - JWT authentication
✓ Pydantic - Data validation
✓ Python-dotenv - Environment variables
✓ Cryptography - Password hashing
✓ Email-validator - Email validation
✓ Pytest - Testing (optional)
✓ Httpx - HTTP testing (optional)
```

Total download: ~50-100 MB

---

## 🚀 Next Steps

1. **Check API docs**: Go to http://localhost:8000/docs
2. **Run test endpoints**:
   ```bash
   curl http://localhost:8000/
   # Response: {"status":"running"}
   ```
3. **Register a user**:
   ```bash
   curl -X POST http://localhost:8000/auth/register \
     -H "Content-Type: application/json" \
     -d '{
       "email": "test@example.com",
       "password_hash": "testpassword123",
       "last_name": "Test",
       "first_name": "User",
       "phone_number": "1234567890",
       "address": "123 Main St",
       "role": "client"
     }'
   ```

---

## 💾 Save/Update Dependencies

### Export current environment to requirements.txt
```bash
pip freeze > requirements.txt
```

### Install a new package
```bash
pip install package-name
pip freeze > requirements.txt
```

---

## 🔄 Deactivate Virtual Environment

```bash
deactivate
```

Then activate again when needed:
```bash
# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

---

## ✅ Verify Installation

```bash
# Check all packages
pip list

# Should show:
# Package            Version
# ------------------ ----------
# fastapi            0.104.1
# uvicorn            0.24.0
# supabase           2.0.3
# pydantic           2.5.0
# python-jose        3.3.0
# ... etc
```

---

## 📚 Additional Resources

- **FastAPI docs**: https://fastapi.tiangolo.com/
- **Supabase docs**: https://supabase.com/docs
- **Pydantic docs**: https://docs.pydantic.dev/
- **JWT docs**: https://jwt.io/

---

## 🎉 You're Ready!

Your API is now running and ready to use. Check the API documentation at:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
