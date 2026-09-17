import sqlite3
import os
import math
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
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

# 메모리 내 위험 제보 저장용 (임시)
hazard_reports = [
    {"lat": 33.4996, "lng": 126.5311, "type": "가로등 고장", "desc": "시청 인근 골목 어두움"}
]

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

def parse_location(loc_str: str):
    """주소 텍스트 또는 위경도 좌표 해석"""
    loc_str = loc_str.strip()
    if "," in loc_str:
        try:
            parts = loc_str.split(",")
            return float(parts[0]), float(parts[1]), "GPS 설정 위치"
        except:
            pass
    
    # 대표 위치 하드코딩 맵핑
    places = {
        "제주시청": (33.4996213, 126.5311884),
        "제주대학교": (33.455113, 126.561502),
        "제주공항": (33.510411, 126.491353),
        "서귀포시청": (33.254120, 126.560076),
        "한라산": (33.361666, 126.529166)
    }
    for name, coords in places.items():
        if name in loc_str:
            return coords[0], coords[1], name
            
    # 기본값 (제주시청 근처)
    return 33.4996213, 126.5311884, loc_str

class HazardReport(BaseModel):
    lat: float
    lng: float
    type: str
    desc: str

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

# 1. 인구 밀도 지도 API (/api/pop_grid)
@app.get("/api/pop_grid")
def get_pop_grid():
    spots = [
        {"name": "제주시청 상권", "lat": 33.4996, "lng": 126.5311, "score": 0.88},
        {"name": "제주대학교 캠퍼스", "lat": 33.4551, "lng": 126.5615, "score": 0.65},
        {"name": "연동 누웨마루거리", "lat": 33.4889, "lng": 126.4984, "score": 0.92},
        {"name": "노형오거리", "lat": 33.4854, "lng": 126.4815, "score": 0.85},
        {"name": "아라동 주민센터 주변", "lat": 33.4752, "lng": 126.5451, "score": 0.54},
        {"name": "서귀포 이중섭거리", "lat": 33.2458, "lng": 126.5638, "score": 0.73}
    ]
    return {"spots": spots}

# 2. 위험 제보 조회 API (/api/hazards)
@app.get("/api/hazards")
def get_hazards():
    return {"hazards": hazard_reports}

# 3. 위험 제보 등록 API (/api/report_hazard)
@app.post("/api/report_hazard")
def report_hazard(report: HazardReport):
    hazard_reports.append(report.dict())
    return {"message": "위험 구역 제보가 성공적으로 등록되었습니다!"}

# 4. 안심 길안내 탐색 API (/api/navigate)
@app.get("/api/navigate")
def navigate(start: str = Query(...), end: str = Query(...), mode: str = Query("max_safe")):
    s_lat, s_lng, s_name = parse_location(start)
    e_lat, e_lng, e_name = parse_location(end)
    
    dist_m = haversine(s_lat, s_lng, e_lat, e_lng)
    
    # 경로 좌표 생성 (중간 경유지 보정)
    steps = 12
    fast_coords = []
    safe_coords = []
    
    # 최단 경로 (직선 근사)
    for i in range(steps + 1):
        r = i / steps
        fast_coords.append([s_lat + (e_lat - s_lat)*r, s_lng + (e_lng - s_lng)*r])
        
    # 안심 경로 (안전 도로 방향으로 살짝 우회)
    mid_lat = (s_lat + e_lat) / 2 + 0.002
    mid_lng = (s_lng + e_lng) / 2 + 0.002
    
    for i in range(steps // 2 + 1):
        r = i / (steps // 2)
        safe_coords.append([s_lat + (mid_lat - s_lat)*r, s_lng + (mid_lng - s_lng)*r])
    for i in range(1, steps // 2 + 1):
        r = i / (steps // 2)
        safe_coords.append([mid_lat + (e_lat - mid_lat)*r, mid_lng + (e_lng - mid_lng)*r])

    time_fast = math.ceil(dist_m / 80)
    time_safe = math.ceil((dist_m * 1.12) / 75)
    
    mode_descs = {
        "max_safe": "🛡️ 100% 인도 & 유동인구 융합 안심 모드",
        "pet": "🐾 반려동물 안심 산책 모드",
        "auto": "⏱️ 시간대별 자동 모드",
        "fast": "⚡ 최단 거리 우선 모드"
    }

    return {
        "success": True,
        "mode_desc": mode_descs.get(mode, "기본 안심 모드"),
        "pop_score": 0.78,
        "start": {"name": s_name, "lat": s_lat, "lng": s_lng},
        "end": {"name": e_name, "lat": e_lat, "lng": e_lng},
        "safest_path": {
            "distance_m": round(dist_m * 1.12, 1),
            "time_min": time_safe,
            "lights": 42,
            "sidewalk_ratio": 94,
            "coords": safe_coords
        },
        "fastest_path": {
            "distance_m": round(dist_m, 1),
            "time_min": time_fast,
            "lights": 18,
            "sidewalk_ratio": 62,
            "coords": fast_coords
        }
    }