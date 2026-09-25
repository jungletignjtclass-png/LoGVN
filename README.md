# 🌱 LoGVN — Prototype tối ưu tuyến thu gom chất thải rắn sinh hoạt
## Khu vực nghiên cứu: Tuyến Lê Văn Việt và lân cận, TP. Thủ Đức, TP.HCM

## 0. Giao diện & luồng sử dụng

`app.py` dùng **sidebar cổ điển** (không phải layout 2 cột thu gọn) với 6 mục
cấu hình, cộng với thân trang hiển thị kết quả theo từng bước:

- **Sidebar**:
  1. **Dữ liệu** — chọn nguồn: bộ 100 điểm P01–P100 (Excel bundled cạnh
     `app.py`), dữ liệu demo quanh Lê Văn Việt (20–100 điểm), hoặc upload
     CSV/XLSX riêng.
  2. **Dự báo nhu cầu** — chọn mô hình (XGBoost/Prophet) và số ngày dự báo
     (1–365 ngày); dữ liệu lịch sử được upload/huấn luyện ở thân trang.
  3. **Routing (OSRM)** — OSRM base URL, bật/tắt fallback Haversine.
  4. **Xe & ràng buộc** — số xe tối đa (upper bound cho OR-Tools), tải trọng
     xe (kg), thời lượng tuyến tối đa (giờ). **Không có auto fleet sizing
     theo %** — người dùng đặt trực tiếp `num_vehicles`/`vehicle_capacity_kg`,
     OR-Tools tự động thử thêm xe (`solve_cvrp_with_auto_scaling`) nếu cấu
     hình ban đầu infeasible.
  5. **Thuật toán tối ưu (OR-Tools)** — bật/tắt Guided Local Search, chiến
     lược khởi tạo, thời gian chạy tối ưu (giây).
  6. **Hệ số tiêu hao & phát thải** — fuel rate (lít/km), emission factor
     (kg CO2/lít) — dùng để ước tính chi phí nhiên liệu và CO2 (Estimated).
- **Thân trang**, theo thứ tự sau khi bấm **"Chạy tối ưu"**:
  1. Dự báo nhu cầu rác (upload lịch sử 365 ngày → XGBoost/Prophet → chọn
     ngày dùng để định tuyến → demand được truyền thẳng vào bước tối ưu, có
     thể xuất Excel dự báo).
  2. So sánh KPI Baseline (Nearest Neighbor) vs Optimized (OR-Tools + GLS).
  3. Bảng **Daily Collection Operations** (cấu trúc Table 4) + bảng phân bổ
     điểm → xe + xuất Excel kết quả.
  4. Bản đồ Folium (road geometry thật từ OSRM Route Service).
  5. So sánh 3 phương pháp: Nearest Neighbor · CVRP trực tiếp · Mô hình đề
     xuất (K-means++ + CVRP theo cụm) — dùng bộ dữ liệu mô phỏng riêng.
  6. Dashboard thành phần rác + tối ưu đa luồng theo phân loại rác tại nguồn
     (tái chế / thực phẩm / còn lại).
  7. **Dynamic Routing (GPS tự động)** — theo dõi 1 xe, auto-completion, auto
     re-optimize khi về depot.
  8. **Fleet Real-time Simulation** — mô phỏng NHIỀU xe cùng lúc (đầy tải, xe
     hỏng, chặn đường), dùng module `simulation.py`.

Giao diện dùng **dark theme toàn app** (`.streamlit/config.toml`, `base =
"dark"`) — nền đen, chữ sáng cho toàn bộ widget Streamlit gốc.

## 1. Kiến trúc code

| File | Vai trò |
|---|---|
| `app.py` | Giao diện Streamlit, điều phối toàn bộ luồng xử lý |
| `routing.py` | Toàn bộ chức năng OSM/OSRM (distance/duration matrix, road geometry), cache, xử lý lỗi |
| `baseline.py` | Baseline heuristic Nearest Neighbor / Greedy và Clarke-Wright Savings (có capacity, service time, depot). `app.py` hiện dùng Nearest Neighbor cho pipeline chính; Clarke-Wright có sẵn trong module và được dùng ở `test_harness2.py`/so sánh baseline mạnh hơn |
| `optimizer.py` | OR-Tools CVRP/VRPTW + Guided Local Search, auto-scaling số xe khi infeasible |
| `clustering.py` | Mô hình đề xuất "Cluster-first, Route-second": K-means++ phân cụm (tự động số cụm k, cân bằng tải trọng) + CVRP theo từng cụm |
| `waste_streams.py` | Tối ưu tuyến riêng cho từng luồng rác (tái chế / thực phẩm / còn lại), tái sử dụng ma trận OSRM đã cache |
| `forecasting.py` | Dự báo nhu cầu rác (XGBoost / Facebook Prophet) từ lịch sử tối thiểu 14 ngày (khuyến nghị 365 ngày), xuất demand cho routing |
| `dynamic_routing.py` | Theo dõi GPS 1 xe thời gian thực: auto-completion điểm thu gom, auto depot detection, tự động re-optimize khi xe về depot; báo sự cố/vật cản (`mark_deferred`) và tự động tạo chặng phụ (`requeue_deferred`) |
| `simulation.py` | Mô phỏng vận hành **nhiều xe cùng lúc** (`FleetSimulator`): di chuyển theo road geometry OSRM, đầy tải giữa tuyến, xe hỏng (đẩy điểm còn lại vào pool cho xe khác), chặn/gỡ chặn đường, dashboard KPI chi phí — dùng ở mục "🚦 Mô phỏng vận hành đội xe thời gian thực" |
| `data_generator.py` | Sinh dữ liệu demo trong phạm vi Lê Văn Việt; chuẩn hoá dữ liệu upload |
| `requirements.txt` | Thư viện cần cài |
| `.streamlit/config.toml` | Dark theme toàn app + cấu hình giảm tải tài nguyên khi deploy Streamlit Community Cloud |

## 2. Cài đặt

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **Lưu ý về Prophet:** `prophet` (Facebook/Meta) cần trình biên dịch C++ và
> có thể mất vài phút để cài/biên dịch `cmdstanpy` lần đầu, tuỳ hệ điều hành,
> và khá nặng RAM khi fit model. Nếu chỉ cần demo nhanh hoặc deploy trên gói
> miễn phí (VD Streamlit Community Cloud ~1GB RAM), có thể dùng XGBoost
> (không cần biên dịch gì thêm) và cân nhắc bỏ Prophet khỏi
> `requirements.txt` nếu gặp lỗi/timeout khi cài đặt hoặc lúc huấn luyện.

## 3. Chạy ứng dụng

```bash
streamlit run app.py
```

Mở trình duyệt tại địa chỉ Streamlit hiển thị (mặc định `http://localhost:8501`).

### Deploy lên Streamlit Community Cloud

- Đẩy toàn bộ repo (gồm cả thư mục `.streamlit/` với `config.toml`) lên
  GitHub rồi kết nối trên [share.streamlit.io](https://share.streamlit.io).
- `.streamlit/config.toml` đã được cấu hình sẵn để nhẹ tài nguyên hơn trên
  gói miễn phí (giới hạn upload 50MB, tắt usage stats...); `_gitignore` chỉ
  loại trừ `.streamlit/secrets.toml` (bí mật), **không** loại trừ
  `config.toml` nên theme/cấu hình vẫn được commit và áp dụng khi deploy.
- Nếu app bị chậm/crash do hết RAM trên gói miễn phí, ưu tiên kiểm tra: (1)
  bỏ Prophet nếu không dùng, (2) giảm số điểm dữ liệu demo, (3) tắt bớt tính
  năng autorefresh (Dynamic Routing GPS / Fleet Simulation) khi không dùng
  tới — cả hai đều chạy vòng lặp tính toán liên tục qua
  `streamlit_autorefresh`.

> **Lưu ý về OSRM:** Ứng dụng mặc định gọi OSRM demo server công khai
> (`https://router.project-osrm.org`). Server này có giới hạn tốc độ và chỉ
> phù hợp cho mục đích thử nghiệm/nghiên cứu (giới hạn mềm ~100 điểm/lần gọi
> Table Service — `routing.py` sẽ cảnh báo nếu vượt ngưỡng này). Nếu cần dùng
> ổn định/số lượng lớn, nên tự dựng OSRM server (Docker + dữ liệu OSM khu vực
> TP.HCM) và đổi "OSRM base URL" trong sidebar sang địa chỉ server riêng.

## 4. Chuẩn bị file CSV/XLSX (nếu không dùng dữ liệu demo)

Cột bắt buộc:

| Cột | Ý nghĩa |
|---|---|
| `node_id` | Mã điểm (duy nhất) |
| `latitude` | Vĩ độ |
| `longitude` | Kinh độ |
| `waste_kg` | Khối lượng rác phát sinh tại điểm (kg) |

Cột tuỳ chọn:

| Cột | Ý nghĩa | Mặc định nếu thiếu |
|---|---|---|
| `service_time` | Thời gian phục vụ tại điểm (phút) | 5 |
| `time_window_start` | Bắt đầu khung giờ được phép thu gom (phút, tính từ 00:00) | 0 |
| `time_window_end` | Kết thúc khung giờ (phút) | 1440 |
| `is_depot` | `True` cho đúng 1 hàng là depot | hàng đầu tiên |
| `is_major_food_generator` | `True` nếu là chợ/nhà hàng/khách sạn (phân 3 luồng rác) | `False` |
| `waste_recyclable_kg` / `waste_food_kg` / `waste_other_kg` | Khối lượng theo từng luồng rác | suy ra tự động theo tỷ lệ mặc định |

Tất cả toạ độ nên nằm trong/quanh khu vực Lê Văn Việt, TP. Thủ Đức để đúng
phạm vi nghiên cứu đã xác định.

## 5. Luồng xử lý (pipeline)

```
Data (demo hoặc upload)
   -> (tuỳ chọn) Dự báo nhu cầu (XGBoost/Prophet, forecasting.py)
   -> OSM/OSRM (Table Service)
   -> Distance matrix (m) + Time matrix (s)   [ma trận CHÍNH cho toàn bộ mô hình]
   -> Baseline (Nearest Neighbor / Greedy, cùng ma trận OSRM)
   -> OR-Tools (CVRP / VRPTW)
   -> Guided Local Search (metaheuristic)
   -> Optimized Routes
   -> OSRM Route Service -> Road geometry thực tế cho từng tuyến
   -> Folium Map (baseline vs optimized)
   -> Bảng KPI (distance, time, số xe, capacity utilization, fuel, CO2)
   -> So sánh % giảm (distance / time / CO2)
   -> (tuỳ chọn) Dynamic Routing GPS 1 xe  |  Fleet Simulation nhiều xe
```

## 6. Các nguyên tắc đã áp dụng theo yêu cầu

- **Không dùng Haversine làm khoảng cách chính** – chỉ dùng khi người dùng
  chủ động bật "cho phép fallback Haversine", và giao diện sẽ hiển thị rõ
  cảnh báo "Fallback mode – không sử dụng mạng lưới đường thực tế".
- **Không dùng `distance / average_speed` làm travel time chính** – thời
  gian di chuyển luôn lấy từ ma trận `duration` của OSRM Table Service.
- **Baseline và Optimized dùng chung một ma trận OSRM** để đảm bảo so sánh
  công bằng.
- **CO2 là giá trị ước tính** ("Estimated CO2 emissions"), tính từ
  `distance × fuel_rate × emission_factor`; các hệ số này có thể chỉnh trong
  sidebar.
- **Không âm thầm chuyển sang Haversine khi OSRM lỗi** – mặc định báo lỗi rõ
  ràng: *"Không thể lấy dữ liệu mạng lưới đường từ OSRM. Vui lòng thử lại."*
- **Cache OSRM** bằng `st.cache_data` cho distance/duration matrix và cho
  road geometry; bộ dữ liệu 100 điểm × 365 ngày cũng được cache
  (`_load_100_point_case`) để tránh đọc/groupby lại Excel mỗi lần Streamlit
  rerun.
- **Dữ liệu demo** chỉ được sinh quanh các waypoint dọc tuyến Lê Văn Việt
  (mặc định 20–30 điểm, cho phép tối đa 150 để phục vụ các phân tích mở
  rộng như clustering), bán kính lệch tối đa ~200m để mô phỏng các hẻm/đường
  nhánh kết nối trực tiếp với trục chính — không rải ra toàn TP.HCM/TP. Thủ
  Đức.
- Nhãn khu vực hiển thị trên giao diện: **"Khu vực thử nghiệm: Tuyến Lê Văn
  Việt – TP. Thủ Đức, TP.HCM"**.

## 7. Giới hạn của prototype (đúng với mục tiêu đề tài sinh viên)

- Chưa xét traffic thời gian thực (chỉ có mô phỏng thủ công "sự cố giao
  thông" bằng hệ số nhân thời gian di chuyển thủ công qua 1 điểm, xem mục 9).
- Toạ độ waypoint dọc Lê Văn Việt trong `data_generator.py` là toạ độ tham
  khảo/xấp xỉ để mô phỏng khu vực nghiên cứu, không phải kết quả khảo sát
  thực địa chính xác từng mét — nếu dùng cho báo cáo chính thức, nên thay
  bằng toạ độ khảo sát thực tế (qua file CSV/XLSX upload).
- OR-Tools + Guided Local Search là thuật toán tối ưu duy nhất được sử dụng,
  không thay thế bằng thuật toán AI khác.
- `optimizer.py` đã hỗ trợ sẵn đội xe không đồng nhất (`vehicle_capacities_kg`)
  và ràng buộc "xe nhỏ cho hẻm sâu" (`small_vehicle_only_nodes`,
  `small_vehicle_flags`) ở tầng hàm số; `data_generator.py` cũng đã sinh sẵn
  cột `requires_small_vehicle`. Tuy nhiên `app.py` hiện **chưa có control nào
  ở sidebar** để bật ràng buộc này khi gọi tối ưu chính — cột
  `requires_small_vehicle` mới chỉ được hiển thị tham khảo, chưa được truyền
  vào `OptimizeConfig` của pipeline chính. Đây là hướng mở rộng UI ở phiên
  bản sau, không phải lỗi của các module xử lý.
- `simulation.py` (Fleet Real-time Simulation) mô phỏng bằng đồng hồ ảo (tăng
  tốc theo `fleet_sim_accel` giây mô phỏng/lần refresh qua
  `streamlit_autorefresh`), **không phải thời gian thực tuyệt đối**, và chi
  phí vận hành (fuel/driver/maintenance) là các thông số giả định có thể
  chỉnh, không phải chi phí thực tế của doanh nghiệp cụ thể nào.

## 8. Dynamic Routing (GPS tự động, 1 xe)

Section **"🛰️ Dynamic Routing – GPS tự động"** theo dõi 1 xe theo thời gian
thực và tự động re-optimize, KHÔNG cần tài xế bấm nút xác nhận:

- **Auto completion**: xe vào bán kính 30–50m quanh 1 điểm thu gom và đứng
  yên trong vùng đó tối thiểu 10 giây (mặc định, có thể chỉnh) → điểm tự
  chuyển `pending → completed`.
- **Auto depot detection**: xe vào bán kính 30–50m quanh DEPOT và đứng đủ
  thời gian → hệ thống tự xác nhận "xe đã về DEPOT", không cần bấm nút.
- **Auto re-optimization**: ngay khi xác nhận về DEPOT, hệ thống lấy toàn bộ
  điểm còn `pending`, đặt DEPOT làm điểm xuất phát, tự động chạy lại
  OR-Tools + Guided Local Search và hiển thị tuyến mới trên bản đồ.
- **Không suy luận "xe đầy" từ GPS**: tín hiệu duy nhất để tái tối ưu là
  "xe đã về DEPOT" (dwell tại depot đủ lâu). Không có cảm biến tải trọng
  trong phạm vi module này (mục 8) — cảm biến tải trọng chỉ được **mô phỏng**
  ở Fleet Simulation (mục 8b), qua `demands_predicted`/`set_actual_waste`.

Có 2 nguồn GPS:

1. **Mô phỏng GPS (demo/test)** — xe di chuyển ảo dọc theo road geometry của
   tuyến hiện tại với tốc độ và hệ số tăng tốc do người dùng chọn. Đây là
   cách khuyến nghị để demo/kiểm chứng logic auto-completion/auto
   re-optimize mà không cần xe thật.
2. **GPS thực từ điện thoại (thử nghiệm)** — dùng package
   `streamlit-geolocation` để đọc vị trí trình duyệt. **Giới hạn quan trọng**:
   trình duyệt yêu cầu quyền định vị và, tuỳ thiết bị/trình duyệt, có thể cần
   tương tác lại để cấp phép hoặc lấy vị trí mới — đây là giới hạn bảo mật
   của trình duyệt web. Nếu cần độ tin cậy cao hơn cho vận hành thực tế, nên
   dùng app di động gốc (native) gửi GPS định kỳ qua API riêng.

Module logic (`dynamic_routing.py`) hoàn toàn tách biệt khỏi Streamlit nên có
thể unit-test độc lập (xem `test_harness.py`, `test_harness2.py`) và tái sử
dụng cho backend khác nếu cần mở rộng sau này.

## 8b. Fleet Real-time Simulation (nhiều xe cùng lúc) — `simulation.py`

Section **"🚦 Mô phỏng vận hành đội xe thời gian thực"** mô phỏng **TẤT CẢ**
xe trong tuyến Optimized di chuyển cùng lúc theo road geometry OSRM thật
(`FleetSimulator` trong `simulation.py`), khác với mục 8 (chỉ theo dõi 1 xe):

- **Đầy tải giữa tuyến**: khi tải trọng xe vượt ngưỡng cấu hình (mặc định
  95%), xe chuyển trạng thái `FULL` và tự động được điều hướng quay thẳng về
  DEPOT bằng 1 leg OSRM thật tính từ vị trí hiện tại (`redirect_to_depot`).
- **Xe hỏng**: đánh dấu 1 xe `BROKEN` sẽ đẩy toàn bộ điểm CHƯA thu gom của xe
  đó vào "pending pool" dùng chung; xe khác đang rảnh (`idle_trucks`) sẽ được
  tự động tái tối ưu để nhận các điểm này. "Sửa xong" đưa xe về DEPOT, sẵn
  sàng nhận tuyến mới.
- **Chặn/gỡ chặn đường**: mô phỏng đoạn đường bị chặn giữa 2 điểm bất kỳ —
  xe đang đi vào đoạn đó sẽ dừng lại (`BLOCKED`) cho tới khi được gỡ chặn.
- **Cập nhật rác thực tế**: cho phép sửa khối lượng rác thực tế tại 1 điểm
  (khác với dự báo) — ảnh hưởng trực tiếp tới tải trọng xe khi thu gom điểm
  đó, có thể kích hoạt trạng thái `FULL` sớm hơn dự kiến.
- **Dashboard KPI**: đồng hồ mô phỏng (ảo), tổng khối lượng dự báo/đã thu/còn
  lại, số xe hoạt động/hỏng, tổng quãng đường, service level (%), tải trọng
  trung bình (%), và **tổng chi phí ước tính** (nhiên liệu + tài xế theo giờ +
  bảo trì theo km + chi phí khác — tất cả là thông số giả định có thể chỉnh).
- **Bản đồ + bảng trạng thái xe/tuyến + nhật ký sự kiện** (Event Log) cập
  nhật theo thời gian thực qua `streamlit_autorefresh`.

`simulation.py` hoàn toàn tách biệt khỏi Streamlit (chỉ phụ thuộc
`dynamic_routing.interpolate_along_route` để nội suy vị trí dọc geometry),
nên có thể unit-test độc lập tương tự `dynamic_routing.py`.

## 9. Tối ưu đa luồng theo đề án phân loại rác tại nguồn

Module `waste_streams.py` mô hình hoá đúng lộ trình phân loại rác thực tế
(Luật Bảo vệ môi trường 2020, Quyết định 63/2024/QĐ-UBND TP.HCM):

- Đa số điểm (hộ gia đình): phân **2 nhóm** — Tái chế / Còn lại.
- Nhóm "chủ nguồn thải phát sinh nhiều rác thực phẩm" (chợ, nhà hàng, khách
  sạn, TTTM có dịch vụ ăn uống — đánh dấu bằng cột `is_major_food_generator`
  trong dữ liệu demo/upload): phân **3 nhóm** — thêm luồng Thực phẩm riêng.

Mỗi luồng (`recyclable`, `food`, `other`) được coi là **một bài toán CVRP độc
lập**, có thể cấu hình số xe/tải trọng xe riêng (VD xe rác thực phẩm thường
nhỏ hơn, kín mùi), nhưng vẫn dùng lại đúng OR-Tools + Guided Local Search và
**tái sử dụng ma trận OSRM đã cache** từ lần chạy tối ưu chính (không gọi lại
OSRM cho từng luồng) để tiết kiệm request và đảm bảo so sánh công bằng.

Section **"♻️ Tối ưu tuyến theo từng luồng rác"** trong `app.py` hiển thị:
- Dashboard thành phần rác (biểu đồ tròn theo luồng, top 10 điểm khối lượng
  lớn nhất, bảng chi tiết theo điểm).
- KPI Baseline vs Optimized riêng cho từng luồng.
- Bảng so sánh **"Trước phân loại (1 luồng gộp)" vs "Sau phân loại (tổng các
  luồng riêng)"** — minh hoạ đánh đổi thực tế: tách luồng thường làm tăng
  tổng quãng đường/số xe, nhưng đổi lại tách bạch được dòng rác tái chế (miễn
  phí thu gom theo biểu giá TP.HCM hiện hành) và rác thực phẩm (giảm khối
  lượng chôn lấp).

**Giới hạn**: đây là mô phỏng tại 1 thời điểm (không mô hình hoá lịch thu gom
nhiều ngày/tần suất khác nhau giữa các luồng — ví dụ rác thực phẩm thường cần
thu hằng ngày còn tái chế 2–3 lần/tuần). Nếu cần mô hình hoá lịch thu gom định
kỳ, nên mở rộng sang bài toán Periodic VRP (PVRP) ở phiên bản sau.

## 10. So sánh Clustering (K-means++) vs không phân cụm

Module `clustering.py` triển khai mô hình "Cluster-first, Route-second":

1. **Giai đoạn phân cụm**: K-means với khởi tạo **k-means++**, số cụm k được
   xác định **tự động** theo công thức `k = ⌈ΣTᵢ / (Q × safety_margin)⌉`
   (safety_margin mặc định 0,9 để chừa dư địa cho bước cân bằng tải trọng).
   Sau phân cụm không gian, một bước **cân bằng tải trọng** (capacity
   balancing) sẽ chuyển các điểm ở cụm quá tải sang cụm liền kề còn dư sức
   chứa, ưu tiên cụm gần nhất, cho đến khi mọi cụm ≤ capacity xe (hoặc hết
   khả năng cân bằng nếu dữ liệu quá khít).
2. **Giai đoạn định tuyến**: mỗi cụm được giải như MỘT bài toán CVRP độc lập
   bằng OR-Tools (PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH), tái sử dụng đúng
   `optimizer.py`, rồi gộp kết quả các cụm lại thành lời giải tổng thể.

Section **"🧩 So sánh: Đường đi ngắn nhất · CVRP trực tiếp · Mô hình đề
xuất"** trong `app.py` sinh một **bộ dữ liệu mô phỏng riêng (mặc định 100
điểm)** dọc Lê Văn Việt, độc lập với dữ liệu ở phần tối ưu chính, rồi chạy và
so sánh 3 phương pháp trên CÙNG một ma trận OSRM:

1. Đường đi ngắn nhất (Nearest Neighbor), không phân cụm.
2. CVRP trực tiếp (OR-Tools + GLS) trên toàn bộ điểm, không phân cụm.
3. Mô hình đề xuất (K-means++ phân cụm + CVRP theo từng cụm).

Bảng kết quả gồm số cụm, số xe, quãng đường, % giảm so với phương án 1, và
**runtime thuật toán** — ngân sách thời gian được **chia đều cho từng cụm**
(`split_time_budget`) để so sánh runtime với phương pháp không phân cụm là
công bằng.

**Phát hiện thực nghiệm đáng lưu ý**: với bộ 100 điểm demo, CVRP trực tiếp có
thể cho kết quả quãng đường TỐT HƠN mô hình phân cụm, và mô hình phân cụm có
runtime CAO HƠN CVRP trực tiếp khi giải TUẦN TỰ (mỗi cụm cần tối thiểu ~2 giây
để OR-Tools khởi tạo, nhân với số cụm k). Đây không phải lỗi mà là đặc điểm cố
hữu của cách tiếp cận "chia để trị" khi giải tuần tự — trong triển khai thực
tế, các cụm là bài toán ĐỘC LẬP nên có thể giải SONG SONG để runtime thực tế
giảm gần bằng runtime của cụm chậm nhất; đây là điểm nên đưa vào phần thảo
luận/hạn chế của đề tài thay vì mặc định cho rằng phân cụm luôn nhanh hơn/tốt
hơn.

## 11. Dự báo từ Excel lịch sử (khuyến nghị 365 ngày) → định tuyến

Giao diện hiện có pipeline theo thứ tự:

1. **Dữ liệu điểm thu gom**: bộ 100 điểm bundled, dữ liệu demo, hoặc CSV/XLSX
   upload chứa `node_id`, `latitude`, `longitude`, `waste_kg` và các cột tuỳ
   chọn.
2. **Dự báo nhu cầu**: dùng luôn lịch sử bundled (nếu chọn bộ 100 điểm) hoặc
   upload file lịch sử riêng (khuyến nghị 365 ngày, tối thiểu 14 ngày để tạo
   được `lag_7`/`rolling_mean_7`), chọn XGBoost hoặc Prophet và horizon 1–365
   ngày, rồi bấm **"🧠 Huấn luyện & dự báo"**.
3. **Chọn ngày dùng để định tuyến**: chọn 1 khoảng ngày dự báo để xem tổng
   quan, sau đó chọn đúng 1 ngày cụ thể — demand ngày đó được dùng trực tiếp
   cho bước tối ưu (không cần tải Excel dự báo xuống rồi upload lại).
4. **Xuất Excel dự báo**: workbook gồm lịch sử đã chuẩn hoá, dự báo theo
   điểm/ngày và bảng kiểm định mô hình (MAE, feature quan trọng nhất).
5. **Định tuyến**: demand của ngày dự báo (nếu có) được truyền trực tiếp sang
   baseline Nearest Neighbor và OR-Tools + GLS.
6. **Phân bổ xe & báo cáo**: giao diện và Excel có bảng Daily Collection
   Operations theo cấu trúc Table 4, cùng bảng Vehicle Allocation.

### Định dạng file lịch sử

Tối thiểu: `date`, `node_id` (cần `waste_kg` **hoặc** đủ 3 cột thành phần).
Nếu có dữ liệu phân loại theo 3 luồng, dùng thêm `waste_organic_kg`,
`waste_recyclable_kg`, `waste_other_kg`. `num_households` là cột tuỳ chọn.
Nếu chỉ có `waste_kg`, hệ thống tạm phân bổ thành phần 55% hữu cơ / 20% tái
chế / 25% còn lại để chạy mô hình đa luồng; đây là giả định của prototype,
không phải dữ liệu đo thực tế. Có thể tải file mẫu ngay trên giao diện (nút
"📄 Tải file mẫu Excel lịch sử").
