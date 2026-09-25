"""
ors_routing.py
--------------
Module thay thế OSRM bằng OpenRouteService (ORS) khi bạn không tự chạy được
OSRM (không cần Docker / server riêng - chỉ cần API key miễn phí).

CÁCH DÙNG trong app_osrm_block4.py:
1. Đổi dòng import (khoảng dòng 40):
       from routing import DEFAULT_OSRM_BASE_URL, OSRMError, check_point_count_limit, get_osrm_matrices, get_osrm_route_geometry
   thành:
       from ors_routing import DEFAULT_OSRM_BASE_URL, OSRMError, check_point_count_limit, get_osrm_matrices, get_osrm_route_geometry

2. Xoá hẳn định nghĩa hàm get_osrm_matrices_safe hiện có trong app.py (dòng
   49-146) và thêm dòng import:
       from ors_routing import get_osrm_matrices_safe
   (đặt cạnh dòng import ở bước 1 luôn cũng được)

3. Không cần sửa gì thêm - các lời gọi get_osrm_matrices_safe(...),
   get_osrm_route_geometry(...), check_point_count_limit(...) trong phần
   còn lại của app.py giữ nguyên, vì chữ ký hàm y hệt bản OSRM cũ.

LƯU Ý QUAN TRỌNG:
- ORS free tier có giới hạn số request/phút và request/ngày, và giới hạn số
  điểm mỗi lần gọi Matrix API. Các con số này có thể thay đổi theo thời gian
  nên hãy tự kiểm tra chính xác trên dashboard tài khoản của bạn tại
  https://openrouteservice.org/dev/#/home (mục "Token" -> xem quota còn lại).
  Code dưới đây cố tình đi CHẬM MÀ CHẮC (batch vừa phải + nghỉ giữa các
  request) để tránh bị chặn 429 Too Many Requests; nếu quota của bạn rộng,
  có thể tăng batch_size / giảm sleep_between cho nhanh hơn.
- KHÔNG commit API key thẳng vào code nếu đưa lên Git public. Cách an toàn
  hơn: set biến môi trường ORS_API_KEY trước khi chạy app, ví dụ:
      export ORS_API_KEY="eyJvcmci..."
  hoặc lưu trong st.secrets khi deploy lên Streamlit Cloud. Nếu không set,
  code sẽ dùng key mặc định điền sẵn bên dưới (tiện để chạy thử ngay).
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import numpy as np
import requests

# Ưu tiên đọc key từ biến môi trường; nếu không có thì dùng key bạn vừa gửi
# (chỉ nên dùng cách này khi chạy thử cá nhân, không đưa lên Git public).
ORS_API_KEY = os.environ.get(
    "ORS_API_KEY",
    "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjBlZWNjN2ZiOTNkYTQ1OWVhNDg2NGEyZWE1ZmMyOTk5IiwiaCI6Im11cm11cjY0In0=",
)

ORS_BASE_URL = "https://api.openrouteservice.org"
# Giữ tên biến DEFAULT_OSRM_BASE_URL để app.py không cần sửa các chỗ khác
# đang dùng nó (ví dụ ô nhập "OSRM base URL" trên UI).
DEFAULT_OSRM_BASE_URL = ORS_BASE_URL


class OSRMError(Exception):
    """Giữ nguyên TÊN OSRMError để mọi `except OSRMError` sẵn có trong
    app.py vẫn bắt lỗi đúng, dù thực chất giờ là lỗi từ ORS."""


def _ors_headers() -> dict:
    return {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json; charset=utf-8",
    }


def check_point_count_limit(num_points: int, base_url: str | None = None) -> str | None:
    """Cảnh báo mềm (không chặn cứng) khi số điểm khá lớn so với quota ORS
    free tier. Trả về chuỗi cảnh báo để app.py hiển thị st.warning, hoặc
    None nếu không cần cảnh báo."""
    if num_points > 100:
        return (
            f"Bạn đang dùng {num_points} điểm. ORS free tier giới hạn số "
            "request/phút và request/ngày khá thấp - số điểm lớn sẽ tạo "
            "nhiều request khi chia batch, có thể mất vài phút hoặc bị "
            "429 Too Many Requests. Kiểm tra quota thực tế trên dashboard "
            "tài khoản ORS của bạn trước khi chạy."
        )
    return None


def get_osrm_matrices(coords, base_url: str | None = None, timeout: int = 60):
    """Lấy ma trận FULL trong 1 request duy nhất (chỉ nên dùng khi số điểm
    đủ nhỏ, ví dụ dưới ~40 điểm). Với số điểm lớn, dùng
    get_osrm_matrices_safe bên dưới để tự chia batch."""
    locations = [[float(lon), float(lat)] for lat, lon in coords]  # ORS dùng [lon, lat]
    body = {"locations": locations, "metrics": ["distance", "duration"]}
    url = f"{ORS_BASE_URL}/v2/matrix/driving-car"

    resp = requests.post(url, json=body, headers=_ors_headers(), timeout=timeout)
    if resp.status_code == 429:
        raise OSRMError(
            "ORS trả về 429 Too Many Requests - đang gọi quá nhanh so với "
            "quota tài khoản. Hãy thử lại sau ít phút hoặc dùng "
            "get_osrm_matrices_safe với sleep_between lớn hơn."
        )
    if resp.status_code != 200:
        raise OSRMError(f"ORS HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    distances = data.get("distances")
    durations = data.get("durations")
    if distances is None or durations is None:
        raise OSRMError(f"ORS không trả về distances/durations. Response: {data}")

    return SimpleNamespace(
        distance_matrix_m=np.array(distances, dtype=float),
        duration_matrix_s=np.array(durations, dtype=float),
        source="ORS",
        warning=None,
    )


def get_osrm_matrices_safe(
    coords,
    base_url: str | None = None,
    batch_size: int = 25,
    timeout: int = 60,
    sleep_between: float = 1.5,
):
    """Ma trận chia theo BLOCK x BLOCK (giống hàm cũ dùng cho OSRM), gọi
    ORS Matrix API cho từng block, có nghỉ `sleep_between` giây giữa các
    request để tránh bị 429 do vượt rate-limit/phút của ORS free tier.

    Nếu OSRM cũ dùng batch_size=4 do giới hạn độ dài URL (GET), ORS dùng
    POST nên không bị giới hạn đó - batch_size có thể để cao hơn hẳn (mặc
    định 25). Nếu tài khoản của bạn có quota thấp hơn, giảm batch_size hoặc
    tăng sleep_between.
    """
    coords = tuple((float(lat), float(lon)) for lat, lon in coords)
    n = len(coords)
    if n < 2:
        raise OSRMError("Cần ít nhất 2 điểm để lấy ma trận.")

    distance = np.full((n, n), np.nan, dtype=float)
    duration = np.full((n, n), np.nan, dtype=float)
    batch_size = max(1, int(batch_size))

    blocks = []
    for oi in range(0, n, batch_size):
        origin_idx = list(range(oi, min(oi + batch_size, n)))
        for di in range(0, n, batch_size):
            dest_idx = list(range(di, min(di + batch_size, n)))
            blocks.append((origin_idx, dest_idx))

    url = f"{ORS_BASE_URL}/v2/matrix/driving-car"

    for bi, (origin_idx, dest_idx) in enumerate(blocks):
        unique_idx = list(dict.fromkeys(origin_idx + dest_idx))
        local_pos = {idx: pos for pos, idx in enumerate(unique_idx)}
        locations = [[coords[idx][1], coords[idx][0]] for idx in unique_idx]  # [lon, lat]

        body = {
            "locations": locations,
            "sources": [local_pos[idx] for idx in origin_idx],
            "destinations": [local_pos[idx] for idx in dest_idx],
            "metrics": ["distance", "duration"],
        }

        last_error = None
        for attempt in range(3):
            try:
                resp = requests.post(url, json=body, headers=_ors_headers(), timeout=timeout)

                if resp.status_code == 429:
                    raise OSRMError("ORS 429 Too Many Requests.")
                if resp.status_code != 200:
                    raise OSRMError(f"ORS HTTP {resp.status_code}: {resp.text[:300]}")

                data = resp.json()
                ds = data.get("distances")
                ts = data.get("durations")
                if ds is None or ts is None:
                    raise OSRMError(f"ORS không trả về distances/durations. Response: {data}")

                for r, global_i in enumerate(origin_idx):
                    for c, global_j in enumerate(dest_idx):
                        if ds[r][c] is None or ts[r][c] is None:
                            raise OSRMError(
                                f"ORS không tìm được đường từ điểm {global_i} đến điểm {global_j}."
                            )
                        distance[global_i, global_j] = float(ds[r][c])
                        duration[global_i, global_j] = float(ts[r][c])
                break

            except (requests.RequestException, ValueError, OSRMError) as exc:
                last_error = exc
                if attempt < 2:
                    # 429/lỗi mạng: nghỉ dài hơn hẳn giữa các lần retry.
                    time.sleep(3.0 * (attempt + 1))
                else:
                    raise last_error

        # Nghỉ giữa các block kế tiếp để tránh vượt rate-limit/phút.
        if bi < len(blocks) - 1:
            time.sleep(sleep_between)

    if np.isnan(distance).any() or np.isnan(duration).any():
        raise OSRMError("Ma trận còn ô trống sau khi chia block.")

    return SimpleNamespace(
        distance_matrix_m=distance,
        duration_matrix_s=duration,
        source="ORS",
        warning=None,
    )


def get_osrm_route_geometry(coords, base_url: str | None = None, timeout: int = 30):
    """Lấy geometry (list [lat, lon]) của tuyến đường đi qua các điểm coords
    (list các (lat, lon)), dùng ORS Directions API. Trả về None nếu ORS
    không tìm được đường - y hệt hành vi hàm cũ của OSRM để chỗ gọi nó
    trong app.py (vẽ nét đứt khi None) không cần sửa gì."""
    coords = list(coords)
    if len(coords) < 2:
        return None

    body = {"coordinates": [[lon, lat] for lat, lon in coords]}
    url = f"{ORS_BASE_URL}/v2/directions/driving-car/geojson"

    try:
        resp = requests.post(url, json=body, headers=_ors_headers(), timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        feats = data.get("features")
        if not feats:
            return None
        line = feats[0]["geometry"]["coordinates"]  # [[lon, lat], ...]
        return [[lat, lon] for lon, lat in line]
    except requests.RequestException:
        return None
