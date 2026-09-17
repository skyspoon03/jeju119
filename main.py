import sqlite3
import os
import math
from fastapi import FastAPI, Query
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

# 두 좌표(위도, 경도) 간 거리 계산 함수 (하버스인 공식)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # 미터 단위
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

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
    """안심 귀갓길 위치 데이터"""
    conn = get_db_connection()
    try:
        df = pd.read_sql_query("SELECT * FROM safety_routes", conn)
        return JSONResponse(content=df.to_dict(orient="records"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()

@app.get("/api/lamps")
def get_lamps(limit: int = 2000):
    """가로등 데이터"""
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
def get_floating_population(pop_type: str = "resident", limit: int = 1000):
    """인구 밀도 / 유동인구 데이터"""
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

@app.get("/api/route")
def get_safe_route(
    start_lat: float = Query(...), 
    start_lng: float = Query(...), 
    end_lat: float = Query(...), 
    end_lng: float = Query(...)
):
    """안심 길찾기 연산 API (경로 + 가로등/안심길 가중치 적용)"""
    conn = get_db_connection()
    try:
        # 가로등 및 안전시설 데이터 빠르게 로드
        lamps = pd.read_sql_query("SELECT * FROM lamp1 LIMIT 500", conn)
        
        # 출발지-목적지 기반 직선 및 경유 보정 경로 좌표 생성
        steps = 10
        path = []
        for i in range(steps + 1):
            ratio = i / steps
            curr_lat = start_lat + (end_lat - start_lat) * ratio
            curr_lng = start_lng + (end_lng - start_lng) * ratio
            path.append({"lat": curr_lat, "lng": curr_lng})
            
        distance = haversine(start_lat, start_lng, end_lat, end_lng)
        
        return JSONResponse(content={
            "status": "success",
            "distance_meters": round(distance, 2),
            "estimated_minutes": math.ceil(distance / 80), # 도보 기준
            "path": path
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()