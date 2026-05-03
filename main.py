import uvicorn
from fastapi import FastAPI

#import schemas
from routes import alerts, auth, cakes, inventory, orders  # Import your route files

app = FastAPI(title="Supabase App")

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
