# 多源 · 稳定传输 · 实时更新 改造方案

> **适用版本**：基于 `live-vlm-webui` 0.4.0

---

## 1. 背景与目标

当前 `live-vlm-webui` 是「单路视频源 → 单路 VLM 分析 → 单浏览器显示」的实时视觉分析工具。现要将其改造为一个**局域网内的多摄像头 VLM 监控墙**。

### 目标场景（已与需求方确认）

| 维度 | 确认结论 | 对设计的影响 |
|------|----------|--------------|
| 浏览器显示形态 | **多路画面同屏**（监控墙式网格） | 需要服务端→浏览器的多路视频分发 |
| 「多源」含义 | **多个 IP/RTSP 摄像头** + **多用户/多会话** | 采集层需多路并行；分发层需会话隔离 |
| 部署/网络环境 | **本地 / 局域网** | NAT 穿透是纯负担，应去除 WebRTC 回传 |

### 三个核心目标的拆解

- **多源**：N 路 RTSP/IP 摄像头并行接入，统一抽象，独立生命周期管理。
- **稳定传输**：去掉局域网下无意义的 NAT 穿透；每路独立重连；采集与分发解耦；后端 VLM 限流防雪崩。
- **实时更新**：监控墙级实时（百毫秒~秒级即可，无需 WebRTC 的亚秒延迟）；预览流与分析流分离以控制带宽/CPU。

---

## 2. 现状分析

### 2.1 当前协议栈

| 链路 | 协议 | 实现位置 |
|------|------|----------|
| IP 摄像头 → 服务器 | RTSP（PyAV/FFmpeg） | `rtsp_track.py` `RTSPVideoTrack` |
| 浏览器摄像头 → 服务器 | WebRTC 上行（aiortc） | `server.py` `offer()` |
| 服务器 → 浏览器（视频画面） | WebRTC track 回传 | `server.py` + `video_processor.py` |
| 服务器 → 浏览器（文字/GPU） | WebSocket | `server.py` `websocket_handler` |

### 2.2 关键发现：WebRTC 回传是不稳定的根因，且收益为零

`VideoProcessorTrack.recv()`（`video_processor.py:80`）实际是**零拷贝透传**——原帧原样返回，未叠加任何内容（VLM 结果走 WebSocket 文字推送）。因此 WebRTC 的全部复杂度（ICE/STUN/TURN/NAT 穿透）几乎没有换来对应价值，却带来了：

- 硬编码 TURN 服务器 IP（`server.py` `offer()` + `static/index.html`），每次 `wsl --shutdown` 后失效；
- 局域网环境下完全不需要的 P2P 打洞逻辑；
- 「一路视频 = 一条 PeerConnection」，在多路同屏场景下复杂度与故障面随摄像头数量**线性增长**。

详见 `../../SETUP_NOTES.md`。

### 2.3 当前单源假设的瓶颈

- `sessions[session_id]` 持有**单个** `VLMService`；`rtsp_tracks[session_id]` 一个会话一路流。
- `broadcast_*` 函数向**所有**客户端广播（非会话隔离），仅 `send_to_session()` 是隔离的。
- VLM 并发控制是「单实例忙则丢帧」（`VLMService._processing_lock`），没有跨路/跨会话的全局限流。
- 多个 `VideoProcessorTrack` 共享类变量（`process_every_n_frames` 等），无法按路独立配置。

---

## 3. 协议决策

| 链路 | 现状 | 决策 | 理由 |
|------|------|------|------|
| 摄像头 → 服务器 | RTSP | ✅ **保留** | IP 摄像头事实标准；改为「多路管理」 |
| 服务器 → 浏览器（视频） | WebRTC | 🔄 **换为 MJPEG / WebSocket-JPEG** | 局域网无需 NAT 穿透；多路同屏下 WebRTC 复杂度爆炸；监控墙无需亚秒延迟 |
| 服务器 → 浏览器（结果） | WebSocket | ✅ **保留**（扩为多路复用） | 够用；可统一承载视频帧+结果+控制 |

### 3.1 视频回传方案选型（替换 WebRTC）

| 方案 | 前端复杂度 | 多路友好度 | 双向控制 | 取舍 |
|------|-----------|-----------|----------|------|
| **HTTP multipart**（`multipart/x-mixed-replace`） | 极低（`<img src>`） | 中（每路一条长连接） | 无 | 出原型最快，控制弱 |
| **WebSocket-JPEG 多路复用** ⭐ | 中（canvas 解码） | 高（一条连接多路） | 有（暂停/切流/订阅） | 目标态，视频与结果天然同步 |
| HLS / LL-HLS | 中 | 高 | 无 | 延迟秒级，**不适合监控**，排除 |
| WebRTC（保留） | 高 | 低 | 有 | 局域网下复杂度不划算，排除 |

**推荐路径**：Phase 1 先用 **HTTP multipart MJPEG** 快速验证并拆掉 WebRTC；目标态收敛到 **WebSocket-JPEG 多路复用**（一条 WS 连接承载多路视频帧 + 各路分析结果 + 控制指令，按 `camera_id` 路由），实现视频与文字结果的天然同步和按路控制。

---

## 4. 目标架构

```
┌─────────────────── 采集层（CameraManager） ───────────────────┐
│  CameraSource (camera_id)  —— 每路独立任务 / 重连退避 / 最新帧缓冲  │
│    ├─ RTSPSource    (PyAV, 多路并行)                            │
│    ├─ LocalSource   (V4L2 / OpenCV)                            │
│    └─ [BrowserSource] (可选: getUserMedia 上行, WS 上传 JPEG)    │
│         每路: LatestFrameBuffer（采集线程只写最新帧）             │
└───────────────────────────┬───────────────────────────────────┘
                            │  采集 / 分发 解耦
            ┌───────────────┴───────────────┐
            ▼                               ▼
┌──────── 分析层（InferencePool） ────┐   ┌──────── 分发层 ────────┐
│  全局并发信号量（限流，防后端雪崩）    │   │ 预览流: 低分辨率/低帧率 JPEG │
│  每路独立 prompt / interval         │   │ 结果流: WebSocket 多路复用   │
│  忙则丢帧（背压，复用现机制并扩展）    │   │   消息按 camera_id/session 路由│
└───────────────┬────────────────────┘   └─────────────┬──────────┘
               └────── 结果(camera_id) ──→ WebSocket ──→ 浏览器网格渲染
```

### 4.1 新增/重构的核心抽象

```python
# camera_source.py（新增）
class CameraSource(ABC):
    camera_id: str
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest_frame(self) -> Optional[Frame]: ...   # 永远返回最新帧，非阻塞
    def stats(self) -> dict: ...                       # connected/fps/codec/重连次数
    # 内部：独立采集任务 + 指数退避重连 + 线程锁（保留防 segfault）

class RTSPSource(CameraSource):  ...   # 由现 RTSPVideoTrack 抽离
class LocalSource(CameraSource): ...   # 由现 LocalCameraTrack 抽离

# camera_manager.py（新增）
class CameraManager:
    def add(self, camera_id, source_cfg) -> CameraSource: ...
    def remove(self, camera_id) -> None: ...
    def get(self, camera_id) -> CameraSource: ...
    def list(self) -> list[dict]: ...
    def health_check(self) -> None: ...    # 周期巡检，触发重连

# inference_pool.py（新增）
class InferencePool:
    def __init__(self, max_concurrency: int): ...  # 全局信号量
    async def submit(self, camera_id, frame, vlm_cfg) -> None: ...
    # 满载则丢弃该路新帧（背压）；每路独立 prompt/model/interval
```

### 4.2 最新帧缓冲（LatestFrameBuffer）

采集与分发解耦的关键：采集任务只把「最新一帧」写入缓冲（覆盖旧帧），预览和分析都从缓冲**读最新**。慢消费者（卡顿浏览器、慢 VLM）永不阻塞采集，也永不消费到积压的旧帧。

```python
class LatestFrameBuffer:
    def set(self, frame): ...      # 采集线程写，覆盖
    def get(self) -> Frame | None: ...  # 多消费者读，非阻塞
```

---

## 5. 关键技术设计

### 5.1 VLM 推理池与限流 ⭐（稳定性第一要务）

**问题**：N 路摄像头打一个后端 VLM（Ollama/vLLM 并发能力有限）。若 N 路同时发起推理，后端过载 → 全部路一起超时 → 雪崩。

**设计**：
- 全局 `asyncio.Semaphore(max_concurrency)` 控制并发推理数（`max_concurrency` 可配，默认按后端能力设，如 2~4）。
- 每路保留「忙则丢帧」语义：若该路上一帧仍在推理、或信号量已满，则**丢弃新帧**而非排队，保证永远分析最新画面。
- 每路独立配置：`prompt` / `model` / `process_every_n_frames` / `max_tokens`。
- 可选：推理队列长度上限 + 超时熔断，单路连续失败时降频或暂停。

### 5.2 预览流 ≠ 分析流（控制带宽/CPU）

监控墙 N 路 JPEG 编码是 CPU 大头，全分辨率多路同屏也吃带宽。**两流分离**：

| 流 | 分辨率 | 帧率 | 用途 |
|----|--------|------|------|
| 预览流 | 缩略图（如 480p/320p） | 5~10 fps | 浏览器网格显示 |
| 分析流 | 全分辨率 | 每 N 帧（interval） | 送 VLM |

两者从同一 `LatestFrameBuffer` 取帧后分别缩放编码。预览 JPEG 质量可调低（如 quality=60）。

### 5.3 WebSocket 多路复用消息协议（目标态）

一条 WS 连接承载所有内容，消息带路由字段。建议 schema：

```jsonc
// 服务器 → 浏览器
{ "type": "frame",   "camera_id": "cam1", "ts": 1234567890, "format": "jpeg", "data": "<base64>" }
{ "type": "vlm_response", "camera_id": "cam1", "text": "...", "metrics": {...} }
{ "type": "camera_status", "camera_id": "cam1", "connected": true, "fps": 8, "reconnects": 2 }
{ "type": "gpu_stats", "stats": {...} }

// 浏览器 → 服务器
{ "type": "subscribe",   "camera_ids": ["cam1","cam2"] }     // 只订阅可见的路，省带宽
{ "type": "add_camera",  "rtsp_url": "rtsp://...", "prompt": "..." }
{ "type": "remove_camera", "camera_id": "cam1" }
{ "type": "update_camera", "camera_id": "cam1", "prompt": "...", "process_every": 30 }
```

> **带宽优化**：浏览器只 `subscribe` 当前可见/展开的摄像头，未订阅的路不推预览帧。

> 二进制优化（后续）：视频帧可用 WS binary frame 传裸 JPEG 字节，避免 base64 的 ~33% 膨胀；用一个小的二进制头部携带 `camera_id`。

### 5.4 会话隔离

- 彻底以 `send_to_session()` / 按 `camera_id` 路由替换 `broadcast_*`（现 `broadcast_text_update` / `broadcast_gpu_stats` 是发给所有人的，多用户下会串话）。
- `CameraManager` 可全局共享（多用户看同一批摄像头）或按会话隔离（各自的摄像头），取决于产品定位——**待决策点 D-1**。

### 5.5 多路 RTSP 稳定性细节

- 每路独立的断线重连 + 指数退避（如 1s→2s→4s→…→上限 30s）。
- 保留 `rtsp_track.py` 中防 segfault 的 `threading.Lock`（`stop()` 与读取线程竞争 use-after-free），多路下竞争更频繁，需复审锁粒度。
- 单路故障隔离：一路挂掉/重连不影响其余路与整体服务。
- 健康指标上报：每路 `connected / fps / 重连次数 / 最后一帧时间`，前端网格上可视化（红/绿状态）。

---

## 6. 分期路线图

| 阶段 | 目标 | 关键产出 | 风险 |
|------|------|----------|------|
| **Phase 1 拆 WebRTC 回传** | 单路验证 MJPEG 替代 WebRTC | `/stream/{camera_id}` MJPEG 端点；删除硬编码 TURN；前端 `<img>` 显示 | 低；**收益最高（解决 SETUP_NOTES 80% 痛点）** |
| **Phase 2 多路采集** | N 路 RTSP 并行 | `CameraSource` / `CameraManager` / `LatestFrameBuffer`；独立重连 | 中；并发与资源管理 |
| **Phase 3 分析池限流** | 防后端雪崩 | `InferencePool` + 全局信号量 + 每路背压 | 中；需压测后端并发上限 |
| **Phase 4 同屏前端 + WS 多路复用** | 监控墙 UI | 网格布局；一条 WS 承载多路视频+结果+控制；订阅机制 | 中；前端 canvas 渲染 N 路性能 |
| **Phase 5（可选）优化** | 带宽/CPU | WS 二进制帧、硬件 JPEG 编码、按需订阅、动态降频 | 低；增量优化 |

> **建议先做 Phase 1**：风险最低、收益最快，且立即移除当前最大的不稳定源（WebRTC + 硬编码 TURN）。

---

## 7. 风险与权衡

- **CPU 瓶颈**：N 路 JPEG 编码在大 N 时吃 CPU。缓解：预览降分辨率/降帧率、降 JPEG 质量、必要时硬件编码（Jetson NVENC）。
- **后端 VLM 并发上限**：是整个系统的真实吞吐瓶颈，需对目标后端（Ollama/vLLM）压测确定 `max_concurrency`。
- **延迟权衡**：MJPEG 比 WebRTC 延迟略高（百毫秒级），但对 VLM 秒级分析场景无影响——这是有意识的取舍。
- **带宽**：多路全分辨率同屏会超局域网舒适区；靠预览/分析分离 + 按需订阅控制。

---

## 8. 待决策点

| 编号 | 决策 | 影响 |
|------|------|------|
| **D-1** | `CameraManager` 全局共享 vs 按会话隔离 | 多用户是看同一批摄像头，还是各自管理各自的源 |
| **D-2** | 视频回传目标态：HTTP-MJPEG 还是 WS-JPEG 多路复用 | 前端复杂度 vs 控制能力（建议 WS） |
| **D-3** | `max_concurrency` 取值 | 取决于后端 VLM 压测结果 |
| **D-4** | 浏览器摄像头源是否仍需支持 | 若需要，保留 WebRTC 上行或改 WS 上传 JPEG |

---

## 9. 验收标准（建议）

- [ ] N 路（≥4）RTSP 摄像头同屏稳定运行 ≥30 分钟无崩溃。
- [ ] 任意单路断线后能自动重连，不影响其余路。
- [ ] 局域网内无需任何 TURN/STUN 配置即可显示视频。
- [ ] 多用户/多会话并发访问，结果不串话。
- [ ] 后端 VLM 过载时系统降级（丢帧/降频）而非雪崩。
- [ ] `git grep -i "turn:" src/` 无残留硬编码 TURN（Phase 1 完成标志之一）。
