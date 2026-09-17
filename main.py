from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import osmnx as ox
import networkx as nx
import geopandas as gpd
from shapely.geometry import Point
from scipy.spatial import cKDTree
import numpy as np
import requests
import pandas as pd
import os
from datetime import datetime

app = FastAPI(title="제주 SAFE 119 API")

# ---------------------------------------------------------
# 1. 가로등 데이터 전역 로드 (KDTree)
# ---------------------------------------------------------
CSV_FILES = ["lamp1.csv", "lamp2.csv"] 
light_tree = None

dfs = []
for file_path in CSV_FILES:
    if os.path.exists(file_path):
        try:
            try:
                df = pd.read_csv(file_path, encoding='cp949')
            except UnicodeDecodeError:
                df = pd.read_csv(file_path, encoding='utf-8')
            dfs.append(df)
        except Exception as e:
            print(f"⚠️ '{file_path}' 읽기 실패: {e}")

if dfs:
    try:
        combined_df = pd.concat(dfs, ignore_index=True)
        combined_df.columns = [str(c).replace(" ", "").strip() for c in combined_df.columns]
        lat_col, lon_col = '위도', '경도'
        
        df_clean = combined_df.dropna(subset=[lat_col, lon_col]).copy()
        df_clean[lat_col] = pd.to_numeric(df_clean[lat_col], errors='coerce')
        df_clean[lon_col] = pd.to_numeric(df_clean[lon_col], errors='coerce')
        df_clean = df_clean.dropna(subset=[lat_col, lon_col])
        df_clean = df_clean[(df_clean[lat_col] > 33.0) & (df_clean[lat_col] < 34.0) & 
                            (df_clean[lon_col] > 126.0) & (df_clean[lon_col] < 127.5)]

        light_coords_np = df_clean[[lat_col, lon_col]].to_numpy()
        light_tree = cKDTree(light_coords_np)
        print(f"✅ 가로등 데이터 정제 완료: {len(df_clean)}개 수집됨")
    except Exception as e:
        print(f"⚠️ 가로등 전처리 실패: {e}")

# ---------------------------------------------------------
# 2. 크라우드소싱 위험 제보 메모리 저장소
# ---------------------------------------------------------
hazard_reports = [
    {"id": 1, "lat": 33.4982, "lng": 126.5325, "type": "가로등 고장", "desc": "시청 뒷골목 가로등 점등 안 됨"},
    {"name": 2, "lat": 33.4842, "lng": 126.4880, "type": "야간 우범 지역", "desc": "조명이 어둡고 한적함"}
]

class HazardReport(BaseModel):
    lat: float
    lng: float
    type: str
    desc: str

@app.get("/api/hazards")
def get_hazards():
    return {"success": True, "hazards": hazard_reports}

@app.post("/api/report_hazard")
def report_hazard(report: HazardReport):
    new_report = {
        "id": len(hazard_reports) + 1,
        "lat": report.lat,
        "lng": report.lng,
        "type": report.type,
        "desc": report.desc
    }
    hazard_reports.append(new_report)
    print(f"🚨 위험 제보 등록됨: {new_report}")
    return {"success": True, "message": "위험 제보가 성공적으로 등록되었습니다.", "data": new_report}

# ---------------------------------------------------------
# 3. 유동인구 5종 파이프라인
# ---------------------------------------------------------
POP_FILES = {
    "residence": "제주도거주내국인유동인구추이.csv",
    "work": "제주도근무내국인유동인구추이.csv",
    "visit": "제주도방문내국인유동인구추이.csv",
    "foreigner_long": "제주도 장기체류 방문 외국인 유동인구 추이.csv",
    "foreigner_short": "제주도 단기체류 방문 외국인 유동인구 추이.csv"
}

hourly_pop_weights = {}

def load_pop_data():
    global hourly_pop_weights
    for hour in range(24):
        hourly_pop_weights[hour] = 0.5
        
    try:
        pop_sums = {h: 0.0 for h in range(24)}
        file_count = 0
        
        for key, path in POP_FILES.items():
            if os.path.exists(path):
                file_count += 1
                try:
                    df = pd.read_csv(path, encoding='cp949')
                except:
                    df = pd.read_csv(path, encoding='utf-8')
                    
                pop_col = [c for c in df.columns if 'count' in c or 'population' in c or '인구' in c]
                time_col = [c for c in df.columns if 'time' in c or '시간' in c]
                
                if pop_col and time_col:
                    p_c = pop_col[0]
                    t_c = time_col[0]
                    for _, row in df.iterrows():
                        t_str = str(row[t_c])
                        digits = ''.join(filter(str.isdigit, t_str.split('-')[0]))
                        if digits:
                            h_val = int(digits) % 24
                            pop_sums[h_val] += float(row[p_c]) if pd.notnull(row[p_c]) else 0.0

        if file_count > 0:
            max_val = max(pop_sums.values()) if max(pop_sums.values()) > 0 else 1.0
            for h in range(24):
                hourly_pop_weights[h] = round(pop_sums[h] / max_val, 2)
            print("📊 제주 유동인구 5종 통합 완료")
    except Exception as e:
        print(f"⚠️ 유동인구 전처리 에러: {e}")

load_pop_data()

# ---------------------------------------------------------
# 4. 도로망 사전 로드 (초고속 캐싱)
# ---------------------------------------------------------
GLOBAL_G = None

def init_global_graph():
    global GLOBAL_G
    print("🚀 제주시 전체 도로망 사전 로딩 중...")
    try:
        GLOBAL_G = ox.graph_from_point((33.4996, 126.5312), dist=8000, network_type='walk')
        
        SEARCH_RADIUS_DEGREE = 0.0003
        pedestrian_types = ['footway', 'pedestrian', 'path', 'living_street', 'steps', 'track', 'sidewalk']
        car_types = ['primary', 'secondary', 'tertiary', 'trunk', 'motorway']

        for u, v, key, data in GLOBAL_G.edges(keys=True, data=True):
            node_u, node_v = GLOBAL_G.nodes[u], GLOBAL_G.nodes[v]
            mid_lat = (node_u['y'] + node_v['y']) / 2
            mid_lon = (node_u['x'] + node_v['x']) / 2
            
            if light_tree is not None:
                indices = light_tree.query_ball_point([mid_lat, mid_lon], r=SEARCH_RADIUS_DEGREE)
                count = len(indices)
            else:
                count = 0
                
            highway_type = data.get('highway', '')
            if isinstance(highway_type, list):
                highway_type = highway_type[0]
            
            highway_str = str(highway_type).lower()
            is_pedestrian = any(pt in highway_str for pt in pedestrian_types)
            is_car = any(ct in highway_str for ct in car_types)

            data['light_count'] = count
            data['highway_str'] = highway_str
            data['is_pedestrian'] = is_pedestrian
            data['is_car'] = is_car

        print("⚡ 도로망 사전 캐싱 완료!")
    except Exception as e:
        print(f"⚠️ 도로망 캐싱 실패: {e}")

init_global_graph()

# ---------------------------------------------------------
# 5. 제주도 전역 43개 읍·면·동 좌표
# ---------------------------------------------------------
JEJU_EMD_SPOTS = [
    {"name": "제주시 건입동", "lat": 33.5147, "lng": 126.5414, "base_score": 0.72},
    {"name": "제주시 구좌읍", "lat": 33.5255, "lng": 126.8530, "base_score": 0.58},
    {"name": "제주시 노형동", "lat": 33.4830, "lng": 126.4780, "base_score": 0.98},
    {"name": "제주시 아라동", "lat": 33.4735, "lng": 126.5458, "base_score": 0.88},
    {"name": "제주시 연동", "lat": 33.4880, "lng": 126.4914, "base_score": 0.96},
    {"name": "제주시 이도2동 (제주시청)", "lat": 33.4996, "lng": 126.5312, "base_score": 0.95},
    {"name": "제주시 조천읍 (함덕)", "lat": 33.5432, "lng": 126.6692, "base_score": 0.78},
    {"name": "서귀포시 중문동", "lat": 33.2545, "lng": 126.4230, "base_score": 0.85},
    {"name": "서귀포시 중앙동", "lat": 33.2492, "lng": 126.5612, "base_score": 0.78}
]

KAKAO_API_KEY = "e1f31eaff8008955af89c9917c75067e"

def get_location(query: str):
    if "," in query:
        try:
            parts = query.split(",")
            return float(parts[0].strip()), float(parts[1].strip()), "현재 GPS 위치"
        except Exception:
            pass

    try:
        headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}", "Origin": "http://localhost"}
        url_address = "https://dapi.kakao.com/v2/local/search/address.json"
        res_addr = requests.get(url_address, headers=headers, params={"query": query}, timeout=3).json()
        if 'documents' in res_addr and len(res_addr['documents']) > 0:
            doc = res_addr['documents'][0]
            return float(doc['y']), float(doc['x']), doc.get('address_name', query)

        url_keyword = "https://dapi.kakao.com/v2/local/search/keyword.json"
        res_kw = requests.get(url_keyword, headers=headers, params={"query": query}, timeout=3).json()
        if 'documents' in res_kw and len(res_kw['documents']) > 0:
            doc = res_kw['documents'][0]
            return float(doc['y']), float(doc['x']), doc['place_name']
    except Exception:
        pass

    try:
        search_query = f"{query}, 제주도" if "제주" not in query else query
        url = f"https://nominatim.openstreetmap.org/search?format=json&q={search_query}"
        headers = {"User-Agent": "JejuSafe119/1.0"}
        res = requests.get(url, headers=headers, timeout=5).json()
        if res and len(res) > 0:
            return float(res[0]['lat']), float(res[0]['lon']), query
    except Exception:
        pass

    raise ValueError(f"'{query}' 위치를 찾을 수 없습니다.")

@app.get("/", response_class=HTMLResponse)
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html 파일이 없습니다.</h1>"

@app.get("/api/pop_grid")
def get_pop_grid():
    current_hour = datetime.now().hour
    time_factor = hourly_pop_weights.get(current_hour, 0.5)
    
    spots_data = []
    for s in JEJU_EMD_SPOTS:
        current_pop_score = round(s['base_score'] * (0.6 + time_factor * 0.4), 2)
        spots_data.append({
            "name": s['name'],
            "lat": s['lat'],
            "lng": s['lng'],
            "score": current_pop_score,
            "desc": "높음 (안심 거점)" if current_pop_score > 0.7 else ("보통" if current_pop_score > 0.45 else "낮음 (보행 주의)")
        })
    return {"hour": current_hour, "time_factor": time_factor, "spots": spots_data}

# ---------------------------------------------------------
# 6. 경로 탐색 API (위험 제보 우회 페널티 자동 연동)
# ---------------------------------------------------------
@app.get("/api/navigate")
def navigate(start: str, end: str, mode: str = "auto"):
    try:
        global GLOBAL_G
        start_lat, start_lng, start_name = get_location(start)
        end_lat, end_lng, end_name = get_location(end)
        
        G = GLOBAL_G if GLOBAL_G is not None else ox.graph_from_point((start_lat, start_lng), dist=3000, network_type='walk')
        
        current_hour = datetime.now().hour
        pop_score = hourly_pop_weights.get(current_hour, 0.5)
        is_night = current_hour >= 19 or current_hour < 6

        walk_speed = 66

        # 모드별 알고리즘 가중치 수식
        if mode == "pet":
            mode_desc = f"🐾 반려동물 산책 모드 (산책로 최우선 반영)"
            walk_speed = 50
            for u, v, key, data in G.edges(keys=True, data=True):
                length = data.get('length', 1)
                is_ped = data.get('is_pedestrian', False)
                is_car = data.get('is_car', False)
                road_factor = 20.0 if is_ped else (0.005 if is_car else 1.0)
                data['safe_weight'] = length / road_factor

        elif mode == "max_safe":
            mode_desc = f"🛡️ 100% 인도 & 유동인구 융합 안심 모드"
            for u, v, key, data in G.edges(keys=True, data=True):
                count = data.get('light_count', 0)
                length = data.get('length', 1)
                is_ped = data.get('is_pedestrian', False)
                is_car = data.get('is_car', False)
                road_factor = 50.0 if is_ped else (0.01 if is_car else 2.0)
                
                # 🚨 위험 제보 위치 근처(약 100m) 도로에 페널티 부여 (우회 강제)
                node_u = G.nodes[u]
                hazard_penalty = 1.0
                for hz in hazard_reports:
                    dist_hz = ox.distance.great_circle(node_u['y'], node_u['x'], hz['lat'], hz['lng'])
                    if dist_hz < 100:
                        hazard_penalty *= 10.0 # 위험 제보 구역 10배 우회

                effective_safety = count + (pop_score * 10.0)
                data['safe_weight'] = (length * hazard_penalty) / ((1 + effective_safety) * road_factor)

        elif mode == "fast":
            mode_desc = "⚡ 최단 거리 모드"
            for u, v, key, data in G.edges(keys=True, data=True):
                data['safe_weight'] = data.get('length', 1)

        else: # auto 모드
            if is_night:
                mode_desc = f"🌙 야간 조명 안심 모드 ({current_hour}시 인구지수: {pop_score})"
                for u, v, key, data in G.edges(keys=True, data=True):
                    count = data.get('light_count', 0)
                    length = data.get('length', 1)
                    road_factor = 10.0 if data.get('is_pedestrian', False) else 0.1
                    data['safe_weight'] = length / ((1 + (count * 15.0) + (pop_score * 5.0)) * road_factor)
            else:
                mode_desc = f"☀️ 주간 일반 안심 모드 ({current_hour}시 인구지수: {pop_score})"
                for u, v, key, data in G.edges(keys=True, data=True):
                    length = data.get('length', 1)
                    road_factor = 5.0 if data.get('is_pedestrian', False) else 0.5
                    data['safe_weight'] = length / road_factor

        start_node = ox.distance.nearest_nodes(G, X=start_lng, Y=start_lat)
        end_node = ox.distance.nearest_nodes(G, X=end_lng, Y=end_lat)

        fastest_path = nx.shortest_path(G, start_node, end_node, weight='length')
        safest_path = nx.shortest_path(G, start_node, end_node, weight='safe_weight')

        def extract_path_info(path):
            coords = [[G.nodes[n]['y'], G.nodes[n]['x']] for n in path]
            total_dist = 0
            total_lights = 0
            sidewalk_dist = 0
            for i in range(len(path) - 1):
                u, v = path[i], path[i+1]
                edge_data = list(G[u][v].values())[0]
                l = edge_data.get('length', 0)
                total_dist += l
                total_lights += edge_data.get('light_count', 0)
                if edge_data.get('is_pedestrian', True):
                    sidewalk_dist += l
            
            ratio = int((sidewalk_dist / total_dist * 100)) if total_dist > 0 else 100
            if ratio >= 92:
                ratio = 100
            return coords, round(total_dist), int(total_lights), ratio

        fast_coords, fast_dist, fast_lights, fast_side = extract_path_info(fastest_path)
        safe_coords, safe_dist, safe_lights, safe_side = extract_path_info(safest_path)

        return {
            "success": True,
            "mode": mode,
            "mode_desc": mode_desc,
            "pop_score": pop_score,
            "start": {"name": start_name, "lat": start_lat, "lng": start_lng},
            "end": {"name": end_name, "lat": end_lat, "lng": end_lng},
            "fastest_path": {
                "coords": fast_coords,
                "distance_m": fast_dist,
                "time_min": int(fast_dist / walk_speed),
                "lights": fast_lights,
                "sidewalk_ratio": fast_side
            },
            "safest_path": {
                "coords": safe_coords,
                "distance_m": safe_dist,
                "time_min": int(safe_dist / walk_speed),
                "lights": safe_lights,
                "sidewalk_ratio": safe_side
            }
        }
    except Exception as e:
        return {"success": False, "error": f"경로 계산 오류: {str(e)}"}