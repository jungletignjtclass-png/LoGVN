"""
osrm_public_routing.py
-----------------------
Module dùng OSRM PUBLIC DEMO SERVER (router.project-osrm.org) - miễn phí,
KHÔNG cần API key. Thay thế cho ors_routing.py khi bạn không muốn/không thể
xin thêm quota ORS.

QUAN TRỌNG - đọc trước khi dùng (chính sách chính thức của OSRM):
- Server này CHỈ dành cho mục đích demo/phi thương mại, KHÔNG có cam kết
  uptime/độ trễ/dữ liệu cập nhật.
- GIỚI HẠN CỨNG: không được vượt quá 1 request/giây. Nếu request của bạn
  ảnh hưởng tới sự ổn định của server, họ SẼ CHẶN IP của bạn (không báo
  trước, không cần lý do).
  Nguồn: https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server
- KHÔNG dùng cho sản phẩm thương mại thu phí người dùng cuối trừ khi truy
  cập vẫn công khai miễn phí.
- Vì giới hạn 1 req/s, module này CHỦ ĐỘNG nghỉ tối thiểu 1.1 giây giữa mỗi
  request (kể cả lúc retry) - đừng chỉnh sleep_between xuống thấp hơn 1.1s,
  sẽ dễ bị chặn IP.
- Nếu app của bạn có nhiều người dùng cùng lúc / cần chạy production thật,
  bắt buộc phải tự host OSRM riêng (Docker) hoặc dùng dịch vụ trả phí
  (ORS/Google/Mapbox) - server public này không phù hợp cho việc đó.

Cách dùng trong app_osrm_block4.py: đổi dòng import
    from ors_routing import DEFAULT_OSRM_BASE_URL, OSRMError, check_point_count_limit, get_osrm_matrices, get_osrm_route_geometry, get_osrm_matrices_safe
thành
    from osrm_public_routing import DEFAULT_OSRM_BASE_URL, OSRMError, check_point_count_limit, get_osrm_matrices, get_osrm_route_geometry, get_osrm_matrices_safe
Không cần sửa gì khác - chữ ký hàm giữ nguyên y hệt.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import requests

DEFAULT_OSRM_BASE_URL = "https://router.project-osrm.org"

# Giới hạn chính thức: không vượt 1 request/giây. Để an toàn, mặc định nghỉ
# 1.1 giây giữa các request liên tiếp.
MIN_SECONDS_BETWEEN_REQUESTS = 1.1

_HEADERS = {
    # Server public yêu cầu User-Agent hợp lệ định danh app - KHÔNG giả
    # User-Agent của app khác (sẽ bị chặn ngay).
    "User-Agent": "WasteRouteOptimizer-Prototype/1.0 (demo/non-commercial)"
}


class OSRMError(Exception):
    pass


def _throttled_get(url, params, timeout, last_call_ts):
    """Gọi GET nhưng đảm bảo cách lần gọi trước tối thiểu
    MIN_SECONDS_BETWEEN_REQUESTS giây. Trả về (response, new_last_call_ts)."""
    elapsed = time.time() - last_call_ts[0]
    wait = MIN_SECONDS_BETWEEN_REQUESTS - elapsed
    if wait > 0:
        time.sleep(wait)
    response = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
    last_call_ts[0] = time.time()
    return response


def check_point_count_limit(num_points: int, base_url: str | None = None) -> str | None:
    if num_points > 60:
        est_seconds = ((num_points // 4) + 1) ** 2 * MIN_SECONDS_BETWEEN_REQUESTS
        return (
            f"Bạn đang dùng {num_points} điểm với OSRM public server (giới hạn "
            f"1 request/giây). Ước tính sẽ mất khoảng {est_seconds:.0f} giây "
            "chỉ để lấy ma trận. Với số điểm lớn, nên tự host OSRM riêng "
            "(Docker) hoặc dùng dịch vụ trả phí/có quota lớn hơn."
        )
    return None


def get_osrm_matrices(coords, base_url: str = DEFAULT_OSRM_BASE_URL, timeout: int = 60):
    """Lấy ma trận FULL trong 1 request (chỉ dùng khi số điểm rất nhỏ, dưới
    khoảng 8-10 điểm, để tránh HTTP 414 do URL quá dài trên server public)."""
    base_url = base_url.rstrip("/")
    coord_string = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in coords)
    url = f"{base_url}/table/v1/driving/{coord_string}"
    params = {"annotations": "distance,duration"}

    response = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
    if response.status_code != 200:
        raise OSRMError(f"OSRM HTTP {response.status_code}: {response.text[:250]}")
    data = response.json()
    if data.get("code") != "Ok":
        raise OSRMError(f"OSRM trả về lỗi: {data.get('code', 'Unknown')}")

    return SimpleNamespace(
        distance_matrix_m=np.array(data["distances"], dtype=float),
        duration_matrix_s=np.array(data["durations"], dtype=float),
        source="OSRM_PUBLIC",
        warning=None,
    )


def get_osrm_matrices_safe(
    coords,
    base_url: str = DEFAULT_OSRM_BASE_URL,
    batch_size: int = 4,
    timeout: int = 60,
):
    """Lấy ma trận theo BLOCK x BLOCK (batch_size=4 mặc định để tránh HTTP
    414 - URL quá dài trên server public), có throttle 1 request/giây bắt
    buộc để không bị chặn IP.

    KHÔNG tăng batch_size quá cao: server public không có giới hạn kiểu ORS
    (theo request/ngày) mà giới hạn theo TỐC ĐỘ gọi (request/giây), nên tăng
    batch_size không giúp nhanh hơn - chỉ khiến mỗi request nặng hơn và dễ
    timeout/414 hơn. Muốn nhanh hơn thật sự thì phải tự host OSRM.
    """
    coords = tuple((float(lat), float(lon)) for lat, lon in coords)
    n = len(coords)
    if n < 2:
        raise OSRMError("Cần ít nhất 2 điểm để lấy ma trận OSRM.")

    distance = np.full((n, n), np.nan, dtype=float)
    duration = np.full((n, n), np.nan, dtype=float)
    base_url = base_url.rstrip("/")
    batch_size = max(1, int(batch_size))
    last_call_ts = [0.0]  # dùng list để mutate được trong hàm con

    for oi in range(0, n, batch_size):
        origin_idx = list(range(oi, min(oi + batch_size, n)))

        for di in range(0, n, batch_size):
            dest_idx = list(range(di, min(di + batch_size, n)))

            unique_idx = list(dict.fromkeys(origin_idx + dest_idx))
            local_pos = {idx: pos for pos, idx in enumerate(unique_idx)}

            coord_string = ";".join(
                f"{coords[idx][1]:.7f},{coords[idx][0]:.7f}" for idx in unique_idx
            )
            sources = ";".join(str(local_pos[idx]) for idx in origin_idx)
            destinations = ";".join(str(local_pos[idx]) for idx in dest_idx)

            url = f"{base_url}/table/v1/driving/{coord_string}"
            params = {
                "sources": sources,
                "destinations": destinations,
                "annotations": "distance,duration",
            }

            last_error = None
            for attempt in range(3):
                try:
                    response = _throttled_get(url, params, timeout, last_call_ts)

                    if response.status_code == 429:
                        raise OSRMError(
                            "OSRM public server trả về 429 (gọi quá nhanh). "
                            "Đợi ít phút rồi thử lại, hoặc tự host OSRM riêng."
                        )
                    if response.status_code == 414:
                        raise OSRMError(
                            f"OSRM HTTP 414 ở block origin {oi + 1}-{origin_idx[-1] + 1}, "
                            f"destination {di + 1}-{dest_idx[-1] + 1}. Giảm batch_size xuống."
                        )
                    if response.status_code != 200:
                        raise OSRMError(f"OSRM HTTP {response.status_code}: {response.text[:250]}")

                    data = response.json()
                    if data.get("code") != "Ok":
                        raise OSRMError(f"OSRM trả về lỗi: {data.get('code', 'Unknown')}")

                    ds = data.get("distances")
                    ts = data.get("durations")
                    if ds is None or ts is None:
                        raise OSRMError("OSRM không trả về distances/durations.")

                    for r, global_i in enumerate(origin_idx):
                        for c, global_j in enumerate(dest_idx):
                            if ds[r][c] is None or ts[r][c] is None:
                                raise OSRMError(
                                    f"OSRM không tìm được đường từ điểm {global_i} đến điểm {global_j}."
                                )
                            distance[global_i, global_j] = float(ds[r][c])
                            duration[global_i, global_j] = float(ts[r][c])
                    break

                except (requests.RequestException, ValueError, OSRMError) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(2.0 * (attempt + 1))
                    else:
                        raise last_error

    if np.isnan(distance).any() or np.isnan(duration).any():
        raise OSRMError("Ma trận OSRM còn ô trống sau khi chia block.")

    return SimpleNamespace(
        distance_matrix_m=distance,
        duration_matrix_s=duration,
        source="OSRM_PUBLIC",
        warning=None,
    )


def get_osrm_route_geometry(coords, base_url: str = DEFAULT_OSRM_BASE_URL, timeout: int = 30):
    """Lấy geometry (list [lat, lon]) tuyến đường qua các điểm coords, dùng
    OSRM Route API. Trả về None nếu không tìm được đường."""
    coords = list(coords)
    if len(coords) < 2:
        return None
    base_url = base_url.rstrip("/")
    coord_string = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in coords)
    url = f"{base_url}/route/v1/driving/{coord_string}"
    params = {"geometries": "geojson", "overview": "full"}

    try:
        response = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
        if response.status_code != 200:
            return None
        data = response.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        line = data["routes"][0]["geometry"]["coordinates"]  # [[lon, lat], ...]
        return [[lat, lon] for lon, lat in line]
    except requests.RequestException:
        return None
