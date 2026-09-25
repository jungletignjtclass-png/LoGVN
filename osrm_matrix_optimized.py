"""
Bản tối ưu của get_osrm_matrices_safe (app_osrm_block4.py).

Thay đổi so với bản gốc:
1. batch_size mặc định tăng lên 25 (chỉnh tuỳ theo giới hạn URL của OSRM
   server bạn đang chạy). batch_size=4 chỉ cần thiết khi có proxy/nginx
   giới hạn độ dài URL rất chặt hoặc dùng server public/demo.
2. Các block được gọi SONG SONG bằng ThreadPoolExecutor thay vì tuần tự
   -> giảm tổng thời gian chờ từ "tổng thời gian từng request cộng lại"
   xuống còn xấp xỉ "thời gian của request chậm nhất trong 1 đợt".
3. Dùng requests.Session() để tái sử dụng kết nối TCP/HTTP, giảm overhead
   handshake khi gọi nhiều request tới cùng 1 host.
4. Giữ nguyên logic retry (3 lần) và xử lý lỗi HTTP 414 / OSRM error như
   bản gốc, chỉ giảm sleep khi thất bại để không cộng dồn quá nhiều thời
   gian chờ vô ích.

Cách dùng: thay hàm get_osrm_matrices_safe trong app_osrm_block4.py bằng
hàm dưới đây (giữ nguyên chữ ký gọi ở 2 chỗ dòng 547 và 915).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

import numpy as np
import requests

from routing import DEFAULT_OSRM_BASE_URL, OSRMError


def _fetch_one_block(session, base_url, coords, origin_idx, dest_idx, timeout):
    """Gọi 1 block origin x destination, trả về (origin_idx, dest_idx, ds, ts) hoặc raise."""
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
            response = session.get(url, params=params, timeout=timeout)

            if response.status_code == 414:
                raise OSRMError(
                    f"OSRM HTTP 414 ở block origin {origin_idx[0] + 1}-{origin_idx[-1] + 1}, "
                    f"destination {dest_idx[0] + 1}-{dest_idx[-1] + 1}. "
                    "Giảm batch_size hoặc tăng giới hạn URL trên OSRM/nginx."
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
            return origin_idx, dest_idx, ds, ts

        except (requests.RequestException, ValueError, OSRMError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))  # sleep ngắn hơn bản gốc (1.5s -> 0.5s)
            else:
                raise last_error


def get_osrm_matrices_safe(
    coords,
    base_url=DEFAULT_OSRM_BASE_URL,
    batch_size=25,          # tăng từ 4 -> 25 (chỉnh nếu OSRM/proxy giới hạn URL chặt hơn)
    timeout=60,
    max_workers=8,          # số request chạy song song cùng lúc
):
    coords = tuple((float(lat), float(lon)) for lat, lon in coords)
    n = len(coords)
    if n < 2:
        raise OSRMError("Cần ít nhất 2 điểm để lấy ma trận OSRM.")

    distance = np.full((n, n), np.nan, dtype=float)
    duration = np.full((n, n), np.nan, dtype=float)
    base_url = base_url.rstrip("/")
    batch_size = max(1, int(batch_size))

    blocks = []
    for oi in range(0, n, batch_size):
        origin_idx = list(range(oi, min(oi + batch_size, n)))
        for di in range(0, n, batch_size):
            dest_idx = list(range(di, min(di + batch_size, n)))
            blocks.append((origin_idx, dest_idx))

    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_one_block, session, base_url, coords, oi, di, timeout): (oi, di)
                for oi, di in blocks
            }
            for future in as_completed(futures):
                # Nếu 1 block lỗi sau khi retry, dừng toàn bộ và báo lỗi ngay
                # (giống hành vi "raise last_error" của bản gốc).
                origin_idx, dest_idx, ds, ts = future.result()
                for r, global_i in enumerate(origin_idx):
                    for c, global_j in enumerate(dest_idx):
                        distance[global_i, global_j] = float(ds[r][c])
                        duration[global_i, global_j] = float(ts[r][c])

    if np.isnan(distance).any() or np.isnan(duration).any():
        raise OSRMError("Ma trận OSRM còn ô trống sau khi chia block.")

    return SimpleNamespace(
        distance_matrix_m=distance,
        duration_matrix_s=duration,
        source="OSRM",
        warning=None,
    )
