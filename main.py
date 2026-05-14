import uvicorn
from fastapi import FastAPI
# 1. ADD THIS IMPORT
from fastapi.middleware.cors import CORSMiddleware 

# import schemas
from routes import alerts, auth, cakes, inventory, orders 

app = FastAPI(title="Supabase App")

# 2. ADD THIS MIDDLEWARE BLOCK HERE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows your React app to connect
    allow_credentials=True,
    allow_methods=["*"],  # Fixes the "405 Method Not Allowed" error
    allow_headers=["*"],  # Allows JSON and Auth headers
)

# Link the routers to the main app
app.include_router(alerts.router)
app.include_router(auth.router)
app.include_router(cakes.router)
app.include_router(inventory.router)
app.include_router(orders.router)

@app.get("/")
def health_check():
    return {"status": "running"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)