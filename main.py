import sqlite3
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import pandas as pd

app = FastAPI(title="제주 SAFE 119 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "jeju119.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/")
def read_root():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"message": "Jeju SAFE 119 API is running"}

@app.get("/manifest.json")
def get_manifest():
    if os.path.exists("manifest.json"):
        return FileResponse("manifest.json")
    return {"error": "manifest.json not found"}

@app.get("/api/safety-routes")
def get_safety_routes():
    conn = get_db_connection()
    try:
        df = pd.read_sql_query("SELECT * FROM safety_routes", conn)
        return JSONResponse(content=df.to_dict(orient="records"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()

@app.get("/api/lamps")
def get_lamps(limit: int = 1000):
    conn = get_db_connection()
    try:
        df1 = pd.read_sql_query(f"SELECT * FROM lamp1 LIMIT {limit}", conn)
        df2 = pd.read_sql_query(f"SELECT * FROM lamp2 LIMIT {limit}", conn)
        combined = pd.concat([df1, df2], ignore_index=True)
        return JSONResponse(content=combined.to_dict(orient="records"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()

@app.get("/api/floating-population")
def get_floating_population(pop_type: str = "resident", limit: int = 500):
    table_map = {
        "resident": "floating_resident",
        "work": "floating_work",
        "visit": "floating_visit",
        "foreign_short": "foreign_short",
        "foreign_long": "foreign_long"
    }
    table_name = table_map.get(pop_type, "floating_resident")
    
    conn = get_db_connection()
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table_name} LIMIT {limit}", conn)
        return JSONResponse(content=df.to_dict(orient="records"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()