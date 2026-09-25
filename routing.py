"""
routing.py
----------
Toàn bộ chức năng liên quan tới OpenStreetMap (OSM) + OSRM.

Nguyên tắc bắt buộc:
- OSRM là nguồn khoảng cách/thời gian di chuyển CHÍNH.
- KHÔNG âm thầm chuyển sang Haversine nếu OSRM lỗi.
- Haversine chỉ được dùng khi người dùng CHỦ ĐỘNG bật allow_haversine_fallback.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Sequence

import requests
import streamlit as st

from dynamic_routing import haversine_m

DEFAULT_OSRM_BASE_URL = "https://router.project-osrm.org"
DEFAULT_PROFILE = "driving"
OSRM_TIMEOUT_SEC = 15
OSRM_MAX_RETRIES = 3
OSRM_RETRY_BACKOFF_SEC = 1.5

# OSRM demo server công khai giới hạn số điểm trong 1 lần gọi Table Service
# (mặc định máy chủ demo ~100). Cảnh báo sớm cho người dùng thay vì để lỗi
# HTTP khó hiểu khi vượt ngưỡng.
OSRM_PUBLIC_SERVER_SOFT_LIMIT = 100


class OSRMError(Exception):
    """Lỗi khi gọi OSRM - không được âm thầm nuốt lỗi này."""


@dataclass
class MatrixResult:
    distance_matrix_m: list  # mét
    duration_matrix_s: list  # giây
    source: str  # "OSRM" hoặc "HAVERSINE_FALLBACK"
    warning: str | None = None


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _haversine_fallback_matrices(coordinates: Sequence[tuple], avg_speed_kmh: float):
    n = len(coordinates)
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]
    speed_ms = max(1.0, avg_speed_kmh) * 1000 / 3600
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            lat1, lon1 = coordinates[i]
            lat2, lon2 = coordinates[j]
            d = _haversine_m(lat1, lon1, lat2, lon2)
            dist[i][j] = d
            dur[i][j] = d / speed_ms
    return dist, dur


def _request_with_retry(url: str, params: dict) -> requests.Response:
    """Gọi OSRM có retry với exponential backoff để giảm rủi ro OSRM demo
    server công khai bị timeout/rate-limit tạm thời (điểm yếu đã ghi nhận:
    phụ thuộc vào server công khai không có SLA)."""
    last_exc = None
    for attempt in range(OSRM_MAX_RETRIES):
        try:
            return requests.get(url, params=params, timeout=OSRM_TIMEOUT_SEC)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < OSRM_MAX_RETRIES - 1:
                time.sleep(OSRM_RETRY_BACKOFF_SEC * (attempt + 1))
    raise last_exc


def check_point_count_limit(num_points: int, base_url: str) -> str | None:
    """Trả về cảnh báo (str) nếu số điểm có nguy cơ vượt giới hạn của OSRM
    demo server công khai, hoặc None nếu ổn / đang dùng server riêng."""
    if base_url.rstrip("/") == DEFAULT_OSRM_BASE_URL and num_points > OSRM_PUBLIC_SERVER_SOFT_LIMIT:
        return (
            f"Số điểm ({num_points}) gần/vượt giới hạn khuyến nghị "
            f"({OSRM_PUBLIC_SERVER_SOFT_LIMIT}) của OSRM demo server công khai. "
            "Có thể gặp lỗi hoặc bị từ chối request. Khuyến nghị tự dựng OSRM "
            "server riêng (xem README) nếu cần chạy với số điểm lớn hơn."
        )
    return None


@st.cache_data(show_spinner=False, ttl=3600)
def get_osrm_matrices(
    coordinates: tuple,
    base_url: str = DEFAULT_OSRM_BASE_URL,
    profile: str = DEFAULT_PROFILE,
    allow_haversine_fallback: bool = False,
    fallback_avg_speed_kmh: float = 25.0,
    traffic_sensitivity_multiplier: float = 1.0,
) -> MatrixResult:
    """Lấy ma trận khoảng cách (m) và thời gian (s) đường bộ giữa mọi cặp điểm
    bằng OSRM Table Service.

    coordinates: tuple các (lat, lon) - dùng tuple để có thể cache_data hash được.

    traffic_sensitivity_multiplier: hệ số nhân THỦ CÔNG lên ma trận thời gian
    (KHÔNG áp lên khoảng cách) để làm SENSITIVITY ANALYSIS mô phỏng giờ cao
    điểm/thấp điểm (VD 1.3 = thời gian di chuyển tăng 30%). Đây KHÔNG phải dữ
    liệu traffic thời gian thực - mặc định = 1.0 (không áp dụng).
    """
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coordinates)
    url = f"{base_url}/table/v1/{profile}/{coord_str}"
    params = {"annotations": "distance,duration"}

    try:
        resp = _request_with_retry(url, params)
    except requests.exceptions.RequestException as exc:
        if allow_haversine_fallback:
            dist, dur = _haversine_fallback_matrices(coordinates, fallback_avg_speed_kmh)
            return MatrixResult(
                dist, dur, source="HAVERSINE_FALLBACK",
                warning="Fallback mode – không sử dụng mạng lưới đường thực tế "
                        f"(lý do: không kết nối được OSRM: {exc}).",
            )
        raise OSRMError(
            "Không thể lấy dữ liệu mạng lưới đường từ OSRM. Vui lòng thử lại."
        ) from exc

    if resp.status_code != 200:
        if allow_haversine_fallback:
            dist, dur = _haversine_fallback_matrices(coordinates, fallback_avg_speed_kmh)
            return MatrixResult(
                dist, dur, source="HAVERSINE_FALLBACK",
                warning=f"Fallback mode – không sử dụng mạng lưới đường thực tế "
                        f"(OSRM trả về HTTP {resp.status_code}).",
            )
        raise OSRMError(
            f"Không thể lấy dữ liệu mạng lưới đường từ OSRM (HTTP {resp.status_code}). "
            "Vui lòng thử lại."
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        if allow_haversine_fallback:
            dist, dur = _haversine_fallback_matrices(coordinates, fallback_avg_speed_kmh)
            return MatrixResult(
                dist, dur, source="HAVERSINE_FALLBACK",
                warning="Fallback mode – không sử dụng mạng lưới đường thực tế "
                        "(phản hồi OSRM không phải JSON hợp lệ).",
            )
        raise OSRMError(
            "Không thể lấy dữ liệu mạng lưới đường từ OSRM. Vui lòng thử lại."
        ) from exc

    if payload.get("code") != "Ok":
        msg = payload.get("message", "unknown error")
        if allow_haversine_fallback:
            dist, dur = _haversine_fallback_matrices(coordinates, fallback_avg_speed_kmh)
            return MatrixResult(
                dist, dur, source="HAVERSINE_FALLBACK",
                warning=f"Fallback mode – không sử dụng mạng lưới đường thực tế "
                        f"(OSRM báo lỗi: {msg}).",
            )
        raise OSRMError(
            f"Không thể lấy dữ liệu mạng lưới đường từ OSRM ({msg}). Vui lòng thử lại."
        )

    dist = payload.get("distances")
    dur = payload.get("durations")
    n = len(coordinates)

    def _has_none(m):
        return m is None or any(m[i][j] is None for i in range(n) for j in range(n))

    if _has_none(dist) or _has_none(dur):
        if allow_haversine_fallback:
            fdist, fdur = _haversine_fallback_matrices(coordinates, fallback_avg_speed_kmh)
            return MatrixResult(
                fdist, fdur, source="HAVERSINE_FALLBACK",
                warning="Fallback mode – không sử dụng mạng lưới đường thực tế "
                        "(ma trận OSRM chứa giá trị None, có thể do điểm nằm ngoài "
                        "mạng lưới đường)."
            )
        raise OSRMError(
            "Ma trận khoảng cách/thời gian từ OSRM chứa giá trị thiếu (None). "
            "Một số điểm có thể nằm ngoài mạng lưới đường hoặc không thể tới được. "
            "Vui lòng kiểm tra lại toạ độ hoặc thử lại."
        )

    warning = None
    if traffic_sensitivity_multiplier != 1.0:
        dur = [[v * traffic_sensitivity_multiplier for v in row] for row in dur]
        warning = (
            f"Đã áp hệ số sensitivity ×{traffic_sensitivity_multiplier:g} lên thời gian di "
            "chuyển (KHÔNG phải traffic thời gian thực) để phân tích độ nhạy."
        )

    return MatrixResult(dist, dur, source="OSRM", warning=warning)


@st.cache_data(show_spinner=False, ttl=3600)
def get_osrm_route_geometry(
    ordered_coords: tuple,
    base_url: str = DEFAULT_OSRM_BASE_URL,
    profile: str = DEFAULT_PROFILE,
) -> list:
    """Lấy geometry đường đi thực tế (road geometry) cho một chuỗi điểm theo
    thứ tự tuyến (Depot -> P.. -> P.. -> Depot) bằng OSRM Route Service.

    Trả về danh sách (lat, lon) để vẽ trên Folium. Nếu OSRM lỗi, trả về danh
    sách rỗng (map sẽ tự fallback vẽ đường thẳng nối các điểm - đã xử lý ở app.py)
    và không được coi là dữ liệu mạng lưới đường thực tế.
    """
    if len(ordered_coords) < 2:
        return []

    coord_str = ";".join(f"{lon},{lat}" for lat, lon in ordered_coords)
    url = f"{base_url}/route/v1/{profile}/{coord_str}"
    params = {"overview": "full", "geometries": "geojson"}

    try:
        resp = _request_with_retry(url, params)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            return []
        coords = payload["routes"][0]["geometry"]["coordinates"]  # [lon, lat]
        return [(lat, lon) for lon, lat in coords]
    except requests.exceptions.RequestException:
        return []
    except (ValueError, KeyError, IndexError):
        return []


# ---------------------------------------------------------------------------
# Mô phỏng sự cố giao thông (chặn đường) - vẫn bắt buộc dùng OSRM thật, KHÔNG
# được vẽ đường thẳng giả lập. "Segment" ở đây là 1 leg (from_node -> to_node)
# của tuyến hiện tại đang được vẽ trên bản đồ, kèm road geometry OSRM thật của
# chính leg đó - đây là đơn vị "đoạn đường" nhỏ nhất mà kiến trúc hiện tại
# (ma trận OSRM giữa các node thu gom) có thể biểu diễn được.
# ---------------------------------------------------------------------------

INCIDENT_CLICK_MAX_DISTANCE_M = 120.0  # click cách xa mọi leg quá ngưỡng này -> bỏ qua


def _point_to_polyline_min_distance_m(lat: float, lon: float, polyline: Sequence[tuple]) -> float:
    """Khoảng cách (m) nhỏ nhất từ 1 điểm tới các ĐỈNH của 1 polyline road
    geometry. Dùng haversine tới từng đỉnh (đủ chính xác cho geometry OSRM có
    mật độ điểm dày), tránh phải thêm thư viện hình học mới."""
    if not polyline:
        return float("inf")
    return min(haversine_m(lat, lon, plat, plon) for plat, plon in polyline)


def find_nearest_road_segment(
    click_lat: float,
    click_lon: float,
    edge_geometries: dict,
    max_distance_m: float = INCIDENT_CLICK_MAX_DISTANCE_M,
):
    """Tìm leg (a, b) gần vị trí click nhất trong số các leg ĐANG được vẽ
    trên bản đồ (đến từ tuyến thật của các xe, road geometry OSRM thật -
    KHÔNG phải suy diễn từ toạ độ node).

    edge_geometries: dict {(a, b): [(lat, lon), ...]} - lấy từ các
    RouteLeg.geometry hiện có của FleetSimulator (xem app.py:
    _collect_edge_geometries).

    Trả về (a, b, distance_m) của leg gần nhất, hoặc None nếu không có leg
    nào trong bán kính max_distance_m (click quá xa mọi tuyến hiện tại).
    """
    best = None
    best_dist = None
    for (a, b), geometry in edge_geometries.items():
        d = _point_to_polyline_min_distance_m(click_lat, click_lon, geometry)
        if best_dist is None or d < best_dist:
            best_dist = d
            best = (a, b)
    if best is None or best_dist is None or best_dist > max_distance_m:
        return None
    return best[0], best[1], best_dist


def _offset_waypoint(lat_a, lon_a, lat_b, lon_b, incident_lat, incident_lon, offset_m: float = 150.0):
    """Tính 1 điểm nằm lệch sang hai bên đoạn thẳng A-B, dùng làm waypoint để
    'gợi ý' OSRM đi vòng qua khu vực khác. Chọn phía XA điểm sự cố hơn. Đây
    chỉ là một toạ độ trung gian (via point) truyền cho OSRM Route Service -
    OSRM vẫn tự chọn đường thực tế bám theo mạng lưới đường quanh điểm đó,
    không phải vẽ đường thẳng tới điểm này."""
    mid_lat = (lat_a + lat_b) / 2.0
    mid_lon = (lon_a + lon_b) / 2.0
    dlat = lat_b - lat_a
    dlon = lon_b - lon_a
    # vector vuông góc (xấp xỉ, đủ dùng cho khoảng cách nhỏ cấp phường/khu vực)
    perp_lat = -dlon
    perp_lon = dlat
    norm = math.hypot(perp_lat, perp_lon) or 1e-9
    # đổi mét -> độ (xấp xỉ tại vĩ độ hiện tại)
    meters_per_deg_lat = 111320.0
    meters_per_deg_lon = 111320.0 * max(0.1, math.cos(math.radians(mid_lat)))
    unit_lat = perp_lat / norm
    unit_lon = perp_lon / norm

    candidates = []
    for sign in (1, -1):
        cand_lat = mid_lat + sign * unit_lat * offset_m / meters_per_deg_lat
        cand_lon = mid_lon + sign * unit_lon * offset_m / meters_per_deg_lon
        d_to_incident = haversine_m(cand_lat, cand_lon, incident_lat, incident_lon)
        candidates.append((d_to_incident, cand_lat, cand_lon))
    # Chọn phía XA điểm sự cố hơn (khả năng né được sự cố cao hơn)
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_lat, best_lon = candidates[0]
    return best_lat, best_lon


def get_osrm_avoiding_route(
    start_coord: tuple,
    end_coord: tuple,
    incident_lat: float,
    incident_lon: float,
    base_url: str = DEFAULT_OSRM_BASE_URL,
    profile: str = DEFAULT_PROFILE,
    avoid_radius_m: float = 200.0,
    waypoint_offset_m: float = 150.0,
) -> list | None:
    """Tìm 1 tuyến đường OSRM THẬT từ start_coord -> end_coord mà đi cách xa
    điểm sự cố (incident_lat, incident_lon) ít nhất avoid_radius_m mét.

    Chiến lược (luôn gọi OSRM Route Service thật, KHÔNG tự vẽ đường thẳng):
    1. Gọi OSRM với `alternatives=true` - nếu OSRM trả về >1 phương án, chọn
       phương án NGẮN NHẤT trong số các phương án đi đủ xa điểm sự cố.
    2. Nếu không có phương án nào thoả (OSRM demo server thường chỉ trả 1
       route cho quãng ngắn), thử "ép" OSRM đi qua 1 waypoint trung gian nằm
       lệch khỏi đoạn thẳng start-end, về phía xa điểm sự cố - OSRM vẫn tự
       route theo mạng lưới đường thật quanh waypoint đó.
    3. Nếu vẫn không tìm được tuyến nào đủ xa sự cố, trả về None (KHÔNG bịa
       ra 1 tuyến giả) - app.py sẽ báo "Không tìm được tuyến thay thế".
    """

    def _routes_from_osrm(coords_seq: Sequence[tuple], alternatives: bool):
        coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords_seq)
        url = f"{base_url}/route/v1/{profile}/{coord_str}"
        params = {"overview": "full", "geometries": "geojson"}
        if alternatives:
            params["alternatives"] = "true"
        try:
            resp = _request_with_retry(url, params)
            if resp.status_code != 200:
                return []
            payload = resp.json()
            if payload.get("code") != "Ok" or not payload.get("routes"):
                return []
            out = []
            for route in payload["routes"]:
                coords = route["geometry"]["coordinates"]
                geom = [(lat, lon) for lon, lat in coords]
                out.append((route.get("distance", float("inf")), geom))
            return out
        except requests.exceptions.RequestException:
            return []
        except (ValueError, KeyError, IndexError):
            return []

    def _far_enough(geom: list) -> bool:
        return _point_to_polyline_min_distance_m(incident_lat, incident_lon, geom) >= avoid_radius_m

    # ---- Bước 1: alternatives=true trên OSRM thật ----
    candidates = _routes_from_osrm((start_coord, end_coord), alternatives=True)
    valid = [g for _, g in sorted(candidates, key=lambda x: x[0]) if _far_enough(g)]
    if valid:
        return valid[0]

    # ---- Bước 2: ép qua waypoint lệch sang bên xa sự cố ----
    via_lat, via_lon = _offset_waypoint(
        start_coord[0], start_coord[1], end_coord[0], end_coord[1],
        incident_lat, incident_lon, offset_m=waypoint_offset_m,
    )
    candidates2 = _routes_from_osrm((start_coord, (via_lat, via_lon), end_coord), alternatives=False)
    valid2 = [g for _, g in candidates2 if _far_enough(g)]
    if valid2:
        return valid2[0]

    # ---- Bước 3: thử waypoint lệch xa hơn (double offset) trước khi bỏ cuộc ----
    via_lat2, via_lon2 = _offset_waypoint(
        start_coord[0], start_coord[1], end_coord[0], end_coord[1],
        incident_lat, incident_lon, offset_m=waypoint_offset_m * 2.2,
    )
    candidates3 = _routes_from_osrm((start_coord, (via_lat2, via_lon2), end_coord), alternatives=False)
    valid3 = [g for _, g in candidates3 if _far_enough(g)]
    if valid3:
        return valid3[0]

    return None
