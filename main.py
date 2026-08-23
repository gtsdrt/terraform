import os

from fastapi import FastAPI

app = FastAPI(title="Terraform Container App Demo")

@app.get("/")
def read_root():
    return {
        "message": "Hello from Azure Container App",
        "port": os.getenv("PORT", "80")
    }

@app.get("/health")
def health():
    return {"status": "healthy"}