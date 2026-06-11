# 多源 · 稳定传输 · 实时更新 · 检测框叠加 改造方案

> **适用版本**：基于 `live-vlm-webui` 0.4.0
> **更新**：2026-06-11 —— 补入"已落地的运行基线"与"现有边缘设备实测"，据实修订检测框落地策略（新增「框已烧进像素」现实模式），并新增 §5.8 视频编码/压缩选型（结论：当前 MJPEG 即可，Orin NX 上换 H.264 不划算）。

---

## 0. 当前运行基线（已落地，单源跑通）

在动多源改造之前，单源链路已在 **Windows 原生 + PowerShell** 环境跑通，作为本方案的起点与回归基准：

| 项 | 现状 |
|----|------|
| 启动方式 | `live-vlm-webui` 不带参数，配置全部从项目根 `.env` 读取（`load_dotenv(find_dotenv(usecwd=True))`）；前端 VLM 配置面板已隐藏，用户零手动配置。 |
| VLM 后端 | NVIDIA API Catalog（`meta/llama-3.2-90b-vision-instruct`），公网走 Clash 代理。 |
| 视频源 | **现有一台边缘设备 `http://<ip>:8088/yolo`**，MJPEG 流，**检测框已由设备烧进画面像素**（见 §2.5）。 |
| 代理隔离 | `.env` 设 `NO_PROXY`/`no_proxy` = LAN 设备 IP + `127.0.0.1,localhost`；公网 NVIDIA 仍走代理。FFmpeg 只认具体 IP，不支持网段。 |
| 已修缺陷 | ①Windows `gbk` 读 `index.html` → 强制 `utf-8`；②`/models` 带 `api_base` 时 `current` 恒为 `False`，导致前端自动选中首个（文本）模型 `01-ai/yi-large` 经 `update_model` 覆盖 .env 模型 → 404 "function not found"，已让 `/models` 正确标记 `current` 修复。 |

> 这台 `/yolo` 设备的"框烧进像素"特性，直接影响检测框方案的落地次序（§5.4 / Phase 5）——它是**今天就能上屏**的最简模式，但与本方案"前端 canvas 叠框"的目标态不同。

## 1. 背景与目标

当前 `live-vlm-webui` 是「单路视频源 → 单路 VLM 分析 → 单浏览器显示」的实时视觉分析工具。现要将其改造为一个**局域网内的多摄像头 VLM 监控墙**，并支持**叠加来自智能边缘设备的检测框**。

### 目标场景（已与需求方确认）

| 维度 | 确认结论 | 对设计的影响 |
|------|----------|--------------|
| 浏览器显示形态 | **多路画面同屏**（监控墙式网格） | 需要服务端→浏览器的多路视频分发 |
| 「多源」含义 | **多个 IP/RTSP 摄像头** + **多用户/多会话** | 采集层需多路并行；分发层需会话隔离 |
| 部署/网络环境 | **本地 / 局域网** | NAT 穿透是纯负担，应去除 WebRTC 回传 |
| **检测框来源** | **智能摄像头/边缘设备自带**（输出格式可控） | 视频之外需并行一条元数据通道 |
| **检测框内容** | **现成框坐标 + 标签**（无需服务端再加工） | 服务端只转发对齐，不做检测 |
| **检测框绘制** | **浏览器前端 canvas 叠加**（不烧进像素） | 服务端**不重编码**视频，最省 CPU |

### 四个核心目标的拆解

- **多源**：N 路 RTSP/IP 摄像头并行接入，统一抽象，独立生命周期管理。
- **稳定传输**：去掉局域网下无意义的 NAT 穿透；每路独立重连；采集与分发解耦；后端 VLM 限流防雪崩。
- **实时更新**：监控墙级实时（百毫秒~秒级即可，无需 WebRTC 的亚秒延迟）；预览流与分析流分离以控制带宽/CPU。
- **检测框叠加**：边缘设备产出「视频帧 + 该帧框坐标」，二者用同一 `frame_id` 绑定；浏览器按 `frame_id` 精确配对，在透明 canvas 层叠框，**框不飘**。

### 范围裁剪（决策已定）

- **不再支持浏览器摄像头源（D-4）**：视频源收敛为 **RTSP / 本地 / 边缘设备**三类，全部不依赖 WebRTC → **aiortc/WebRTC 整体退场**（上行 + 回传一起删）。
- **多用户看同一批摄像头（D-1）**：`CameraManager` 全局单例，VLM 结果按 `camera_id` 共享，不按会话重复分析。

---

## 2. 现状分析

### 2.1 当前协议栈

| 链路 | 协议 | 实现位置 |
|------|------|----------|
| IP 摄像头 → 服务器 | RTSP（PyAV/FFmpeg） | `rtsp_track.py` `RTSPVideoTrack` |
| 浏览器摄像头 → 服务器 | WebRTC 上行（aiortc） | `server.py` `offer()` |
| 服务器 → 浏览器（视频画面） | WebRTC track 回传 | `server.py` + `video_processor.py` |
| 服务器 → 浏览器（文字/GPU） | WebSocket | `server.py` `websocket_handler` |

→ **现状所有源都只传视频，没有任何"摄像头侧文本/元数据"通道**——这是本次新需求必须从零新增的。

### 2.2 关键发现：WebRTC 回传是不稳定的根因，且收益为零

`VideoProcessorTrack.recv()`（`video_processor.py:80`）实际是**零拷贝透传**——原帧原样返回，未叠加任何内容（VLM 结果走 WebSocket 文字推送）。因此 WebRTC 的全部复杂度（ICE/STUN/TURN/NAT 穿透）几乎没有换来对应价值，却带来了：

- 硬编码 TURN 服务器 IP（`server.py` `offer()` + `static/index.html`），每次 `wsl --shutdown` 后失效；
- 局域网环境下完全不需要的 P2P 打洞逻辑；
- 「一路视频 = 一条 PeerConnection」，在多路同屏场景下复杂度与故障面随摄像头数量**线性增长**。

详见 `SETUP_NOTES.md`。

### 2.3 当前单源假设的瓶颈

- `sessions[session_id]` 持有**单个** `VLMService`；`rtsp_tracks[session_id]` 一个会话一路流。
- `broadcast_*` 函数向**所有**客户端广播（非会话隔离），仅 `send_to_session()` 是隔离的。
- VLM 并发控制是「单实例忙则丢帧」（`VLMService._processing_lock`），没有跨路/跨会话的全局限流。
- 多个 `VideoProcessorTrack` 共享类变量（`process_every_n_frames` 等），无法按路独立配置。

### 2.4 "绘制框"目前是死代码，不可复用激活

`video_processor.py` 看似有画框能力，实则**从未执行**：

- `_draw_yolo_boxes()`（L223）、`_add_text_overlay()`（L245）**定义了但全项目无任何调用点**。
- `recv()` 即使开 `--yolo` 也只把结果存进 `self._yolo_results`，随后 `return frame` 透传原帧；`_yolo_results` **只写不读**（无消费者）。
- `--yolo` 的帮助文本 *"overlay on video frames"*（`server.py:1085`）**名不副实**。
- 前端 `index.html` 的 `canvas` 仅用于 GPU/CPU **sparkline**，**无任何检测框绘制层**。

→ 结论：检测框叠加**从零实现**。死代码方向（服务端 cv2 烧框）与本方案（边缘设备给框 + 前端 canvas 叠框）不同，参考价值仅限坐标/标签格式。

### 2.5 现有边缘设备实测（`/yolo` MJPEG，框已烧进像素）

手上这台边缘设备的实际形态，与方案最初设想的"设备只给框坐标"**并不一致**，需据实记录：

| 项 | 实测结论 |
|----|----------|
| 传输 | `http://<ip>:8088/yolo`，**HTTP MJPEG**（`multipart/x-mixed-replace`），由 `rtsp_track.py` 以 `format="mpjpeg"` 直接打开。 |
| 分辨率/编码 | 640×480 MJPEG，PyAV 可直接读出。 |
| **检测框** | **设备已把 YOLO 框 + 标签 + 置信度直接画进 JPEG 像素**（如 `person 0.41`、`chair 0.52`），到达浏览器即"自带框"。 |
| 元数据通道 | **无**。设备当前不单独输出 `{frame_id, boxes}`，框只存在于像素里。 |
| 稳定性 | 设备会离线（曾出现 TCP 拒绝 / `-138` 超时）；恢复后即正常。重连/离线标记仍是多源阶段的硬需求。 |

**对方案的影响**：这等于检测框落地的 **Mode 0（烧进像素）**——零对齐、零前端改动，**今天就能显示**，但框不可交互/不可关/被设备二次压缩、样式不可控。它是 Phase 5 的"最简可用起点"，而非终点（见 §5.4 新增行与 Phase 5 拆分）。若要"可开关/可交互/不重压缩"的目标态，仍需推动设备**额外**旁路一条 `{frame_id, boxes}` 元数据通道（D-5 选项 A）。

> 设备可控（需求方可改其输出格式），因此"让设备在烧框 MJPEG 之外、再旁路一条纯框 JSON"是可行的演进路径，无需推翻现有 `/yolo`。

---

## 3. 协议决策

| 链路 | 现状 | 决策 | 理由 |
|------|------|------|------|
| 摄像头 → 服务器（视频） | RTSP | ✅ **保留**（可控设备建议 WS-JPEG 带 `frame_id`） | IP 摄像头事实标准；改为「多路管理」 |
| 浏览器摄像头 → 服务器（上行） | WebRTC 上行 | ❌ **移除**（D-4 不再支持） | 连同回传一起，aiortc/WebRTC 整体退场 |
| **边缘设备 → 服务器（元数据）** | 无 | ➕ **新增**：WS 推 `{frame_id, boxes}`（D-5 分开传输） | 与视频并行，用 `frame_id` 对齐 |
| 服务器 → 浏览器（视频） | WebRTC | 🔄 **换为 WebSocket-JPEG** | 局域网无需 NAT；多路同屏 WebRTC 复杂度爆炸；监控墙无需亚秒延迟 |
| **服务器 → 浏览器（检测框）** | 无 | ➕ **新增**：与视频共一条 WS，带 `frame_id` | 同源 `frame_id` 精确对齐，前端 canvas 叠加 |
| 服务器 → 浏览器（结果） | WebSocket | ✅ **保留**（扩为多路复用） | 够用；可统一承载视频帧+框+结果+控制 |

### 3.1 视频回传方案选型（替换 WebRTC）

| 方案 | 前端复杂度 | 多路友好度 | 双向控制 | 取舍 |
|------|-----------|-----------|----------|------|
| HTTP multipart（`multipart/x-mixed-replace`） | 极低（`<img src>`） | 中（每路一条长连接） | 无 | 出原型最快，但**帧里塞不进 `frame_id`**，对齐只能靠时间戳近似 |
| **WebSocket-JPEG 多路复用** ⭐ | 中（canvas 解码） | 高（一条连接多路） | 有（暂停/切流/订阅） | 目标态：**每帧消息天然可带 `frame_id`**，与检测框完美对齐 |
| HLS / LL-HLS | 中 | 高 | 无 | 延迟秒级，**不适合监控**，排除 |
| WebRTC（保留） | 高 | 低 | 有 | 局域网下复杂度不划算，排除 |

**推荐路径**：因为有「检测框按 `frame_id` 对齐」的硬需求，**直接走 WebSocket-JPEG 多路复用**（一条 WS 承载多路视频帧 + 检测框 + 分析结果 + 控制，按 `camera_id`/`frame_id` 路由）。HTTP-MJPEG 仅作 Phase 1 拆 WebRTC 的快速验证手段（此阶段框可先用时间戳近似对齐）。

---

## 4. 目标架构

```
┌──────────────── 采集层（CameraManager） ────────────────┐
│  CameraSource (camera_id) ── 独立任务/重连退避/最新帧缓冲    │
│    ├─ RTSPSource    (PyAV, 多路并行)                      │
│    ├─ LocalSource   (V4L2 / OpenCV)                      │
│    └─ EdgeSource    (智能设备: 视频帧 + 检测框, 同源 frame_id) │ ← 新增
│      （浏览器摄像头源已按 D-4 移除；CameraManager 为全局单例）  │
│  MetadataChannel (camera_id) ── 收 {frame_id, boxes}      │ ← 新增
└──────────────────────────┬───────────────────────────────┘
                          │  采集 / 分发 解耦
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌──── 分析层 InferencePool ────┐   ┌──────── 分发层（WS 多路复用）────────┐
│ 全局信号量限流（防雪崩）       │   │ 视频帧:  {camera_id, frame_id, jpeg} │
│ 每路独立 prompt / interval   │   │ 检测框:  {camera_id, frame_id, boxes}│ ← 新增
│ 忙则丢帧（背压）             │   │ VLM结果 / GPU指标 / 摄像头状态        │
└──────────────┬──────────────┘   └────────────────┬─────────────────────┘
              └──── 结果(camera_id) ────────────────┘
                                                    ▼
                  浏览器网格: [视频层 <img>/canvas] + [透明 canvas 叠框层]
                             按 frame_id 配对刷新（框不飘）
```

### 4.1 新增/重构的核心抽象

```python
# camera_source.py（新增）
class CameraSource(ABC):
    camera_id: str
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest_frame(self) -> Optional[Frame]: ...   # 永远返回最新帧(含 frame_id)，非阻塞
    def stats(self) -> dict: ...                       # connected/fps/codec/重连次数
    # 内部：独立采集任务 + 指数退避重连 + 线程锁（保留防 segfault）

class RTSPSource(CameraSource):  ...   # 由现 RTSPVideoTrack 抽离
class LocalSource(CameraSource): ...   # 由现 LocalCameraTrack 抽离
class EdgeSource(CameraSource): ...    # 智能设备：视频帧 + 同源 frame_id；伴随 MetadataChannel

# metadata_channel.py（新增）—— 接收边缘设备的检测框
class MetadataChannel:
    camera_id: str
    async def on_detections(self, frame_id: int, boxes: list) -> None: ...  # 入站
    def latest(self) -> tuple[int, list]: ...   # (frame_id, boxes) 供分发层读取
    # 入站可来自 WS / MQTT / TCP；统一归一化为内部 boxes 结构

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

采集与分发解耦的关键：采集任务只把「最新一帧」（连同其 `frame_id`）写入缓冲（覆盖旧帧），预览和分析都从缓冲**读最新**。慢消费者（卡顿浏览器、慢 VLM）永不阻塞采集，也永不消费到积压的旧帧。

```python
class LatestFrameBuffer:
    def set(self, frame_id: int, frame): ...      # 采集线程写，覆盖
    def get(self) -> tuple[int, Frame] | None: ...  # 多消费者读，非阻塞
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

> ⚠️ **检测框坐标须归一化（0~1）**，否则预览缩放后框会错位。归一化后浏览器只需乘以显示宽高，与预览分辨率无关。

### 5.3 WebSocket 多路复用消息协议（目标态）

一条 WS 连接承载所有内容，消息带路由字段（`camera_id`）与对齐字段（`frame_id`）。建议 schema：

```jsonc
// 服务器 → 浏览器
{ "type": "frame",      "camera_id": "cam1", "frame_id": 12345, "format": "jpeg", "data": "<base64 或 WS binary>" }
{ "type": "detections", "camera_id": "cam1", "frame_id": 12345,
  "boxes": [ {"x":0.12,"y":0.34,"w":0.20,"h":0.15,"label":"person","conf":0.92} ] }   // 归一化坐标
{ "type": "vlm_response","camera_id": "cam1", "text": "...", "metrics": {...} }
{ "type": "camera_status","camera_id": "cam1", "connected": true, "fps": 8, "reconnects": 2 }
{ "type": "gpu_stats", "stats": {...} }

// 浏览器 → 服务器
{ "type": "subscribe",    "camera_ids": ["cam1","cam2"] }     // 只订阅可见的路，省带宽
{ "type": "add_camera",   "rtsp_url": "rtsp://...", "prompt": "..." }
{ "type": "remove_camera","camera_id": "cam1" }
{ "type": "update_camera","camera_id": "cam1", "prompt": "...", "process_every": 30 }
```

> **对齐关键**：`frame` 与 `detections` 共享同一 `frame_id`。浏览器维护一个小的「框缓冲」`{frame_id → boxes}`，渲染某帧视频时取对应 `frame_id` 的框叠加；未命中则沿用上一帧或留空（可配）。

> **带宽优化**：浏览器只 `subscribe` 当前可见/展开的摄像头，未订阅的路不推帧/框。

> **二进制优化（后续）**：视频帧用 WS binary 传裸 JPEG，避免 base64 ~33% 膨胀；二进制头部携带 `camera_id` + `frame_id`，`detections` 仍走 JSON 文本帧。

### 5.4 边缘设备元数据通道与 `frame_id` 对齐 ⭐（本次新增核心）

**`frame_id` 是整个对齐机制的灵魂**，由**边缘设备生成**（设备同时做编码与检测，天然知道某帧的 `frame_id`），保证「视频帧」与「该帧检测框」携带**同一 `frame_id`**。

落地四选项（**Mode 0 是现有设备的现状、今天即可上屏**；A 为目标态）：

| 选项 | 视频通道 | 元数据通道 | `frame_id` 传递 | 对齐精度 | 备注 |
|------|----------|-----------|----------------|----------|------|
| **Mode 0 现状** | 边缘设备 **MJPEG**，框**已烧进像素**（`/yolo`） | 无 | 不需要 | 像素级（框即画面） | 零改动可用；但框不可关/不可交互、被二次压缩、样式不可控 |
| **A 目标推荐** | 边缘设备 **WS-JPEG**：`{frame_id, jpeg}`（裸帧不烧框） | WS/MQTT：`{frame_id, boxes}` | 显式、同源 | **帧级精确** | 可开关/可交互/不重压缩；需设备额外旁路框 JSON |
| B | 边缘设备 **RTSP** | WS/MQTT：`{ts, boxes}` | 靠 PTS/时间戳近似 | 近似（运动时滞后） | 设备不便打 `frame_id` 时的退化 |
| C | 边缘设备**合并发送**：`{frame_id, jpeg, boxes}` 一条消息 | （同上合并） | 一起到达 | 帧级、零对齐逻辑，但耦合高 | 一条消息搞定，但视频/框无法分别订阅 |

> **演进关系**：Mode 0 → A。先用 Mode 0 把现有 `/yolo` 设备直接接入网格（当作普通 MJPEG 源，框天然在画面里），再推动设备旁路 `{frame_id, boxes}` 升级到 A，换取"框可开关、不重压缩、样式统一、可二次交互"。两者**前端结构不同**：Mode 0 只有视频层、无叠框层；A 需 §5.5 的透明 canvas 叠框层。

**服务端职责（极薄）**：接收 `{frame_id, jpeg}` 与 `{frame_id, boxes}`，按 `camera_id` 路由给订阅的浏览器，**不解码、不画框、不重编码**。可选：缓存最近 K 个 `frame_id` 的框，用于补发/容错。

**坐标系**：边缘设备直接输出**归一化 0~1** 坐标（推荐），免去分辨率/缩放换算；若设备只能给像素坐标，则附带 `source_w/source_h`，由前端换算。

### 5.5 前端 canvas 叠框层（本次新增）

每路网格单元是**两层叠放**：

- **视频层**：`<img>`（HTTP-MJPEG）或 `<canvas>`（WS-JPEG 解码 `drawImage`），显示画面。
- **叠框层**：上方一个**透明 `<canvas>`**，`absolute` 定位完全覆盖视频层，只画框/标签。

渲染循环：拿到某 `frame_id` 的视频帧 → 显示 → 从框缓冲取同 `frame_id` 的 `boxes` → `clearRect` 后 `strokeRect` + `fillText`（坐标 × 显示宽高）。视频层与叠框层**同节奏刷新**，框与画面锁帧。

> 性能：N 路同屏时，叠框层仅在该路有新 `detections` 时重绘；空闲路不刷，控制前端 CPU。

### 5.6 会话模型：全局共享摄像头 + 订阅级隔离（D-1 已定）

多用户看**同一批**摄像头，故：

- `CameraManager` 是**全局单例**，所有会话共享同一批 `CameraSource`；摄像头增删是全局操作，不属于某个会话。
- 分析结果按 **`camera_id`** 而非 `session_id` 组织：`VLMService`/`InferencePool` **每路一份**（不再「每会话一个」），同一摄像头的 VLM 结果**所有订阅者共享**，避免 N 个用户把同一路打 N 遍。
- 会话只承担**连接 + 订阅**职责：每个 WS 连接记录它 `subscribe` 了哪些 `camera_id`，分发层只把对应路的 `frame`/`detections`/`vlm_response` 推给订阅者。
- `broadcast_*`（发给所有人）退场，统一按"订阅了该 `camera_id` 的连接集合"路由。

### 5.7 多路 RTSP 稳定性细节

- 每路独立的断线重连 + 指数退避（如 1s→2s→4s→…→上限 30s）。
- 保留 `rtsp_track.py` 中防 segfault 的 `threading.Lock`（`stop()` 与读取线程竞争 use-after-free），多路下竞争更频繁，需复审锁粒度。
- 单路故障隔离：一路挂掉/重连不影响其余路与整体服务。
- 健康指标上报：每路 `connected / fps / 重连次数 / 最后一帧时间`，前端网格上可视化（红/绿状态）。
- **元数据通道亦需独立重连与心跳**：视频在线但元数据断时，前端应停止叠旧框（避免框冻结在错误位置），并标记该路「检测离线」。

### 5.8 视频编码 / 压缩选型（结论：当前 MJPEG 即可，不必换）

**视频数据已经在压缩了**——边缘设备端 `cv2.imencode(".jpg", q80)` 即 JPEG 压缩，640×480 单帧由原始 ~900 KB 压到 ~30–50 KB（约 20–25×）。不压缩则单路单用户就要 ~72 Mbps，多用户根本不可行。**整条链路的压缩只在设备上发生一次，服务端在 WS-JPEG 扇出模式下只转发字节、不重编码。**

是否值得换成更高压缩比的视频编码（H.264/H.265），取决于"花什么资源、省什么资源"：

| | MJPEG（现状） | 换 H.264/H.265 |
|---|---|---|
| 编码开销 | JPEG 编码很轻（几 ms/帧） | **Orin NX 上 = CPU 软编**（见下），持续占核 |
| 体积/质量 | 帧内压缩，效率一般 | 同画质省 **3–10×**，又好又小 |
| 延迟 | 逐帧、近零 | GOP/编码管线，增加延迟 |
| `frame_id` 对齐 | 每帧独立，**天然好对齐**（契合 §5.4 叠框） | 帧间依赖，对齐变难（需 SEI/RTP 时间戳） |
| 浏览器 | `<img>`/canvas 直接解 | 需 H.264 解码管线 |

> ⚠️ **硬件编码器现实（Orin NX 16GB / JetPack 6.2）**：Orin **Nano** 彻底无 NVENC；Orin **NX** 的 NVENC 在标准工具链里基本不可用——官方 ffmpeg 只开放硬件**解码**，`/dev/v4l2-nvenc`、`nvv4l2h264enc` 常缺失，硬件编码要走底层 `jetson_multimedia_api`。**所以在本项目这台设备上，换 H.264 现实里 = CPU 软编（x264）。** 设备端自查：`gst-inspect-1.0 | grep nvv4l2` / `ls /dev/nvhost-msenc`。

**结论**：在"1 路 × 10 用户 × 局域网 + 设备内存已吃紧（swap 打满）"这个具体场景下，**MJPEG 是当前最划算的选择，而非凑合**——它花的是富余的 LAN 带宽（~32 Mbps，随便扛），省的是紧缺的设备 CPU/内存、延迟和对齐复杂度。换 H.264 会把这笔账反过来做（省不缺的带宽、烧最缺的算力），故**暂不换**。

**现在值得做的零成本微调**（不增 CPU、不改架构）：

```python
cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75, cv2.IMWRITE_JPEG_OPTIMIZE, 1])
```

- `IMWRITE_JPEG_OPTIMIZE`：优化 Huffman 表，同画质再小 ~5–8%。
- 质量 q80→q75：观感几乎无损，再省 ~20%。
- 帧率 ~10fps→~8fps：带宽近线性下降，监控观感无损。
- 设备代码已有的 `jpeg != last_sent` 去重（静止画面不重发）保留。

**何时才回头换编码**：摄像头**路数变多** / 跨**公网** / 上 **1080p+** / 用户**数十上百**——届时带宽成为真瓶颈，且应同时把编码**挪到有 NVENC 的设备**（如 AGX Orin）或在服务端转码，而非压在这台 Orin NX 上软编。

---

## 6. 分期路线图

| 阶段 | 目标 | 关键产出 | 风险 |
|------|------|----------|------|
| **Phase 1 移除 WebRTC** | MJPEG 替代回传 + 删除上行 | `/stream/{camera_id}` MJPEG；删硬编码 TURN；移除 `offer()`/aiortc 依赖/前端 WebRTC JS（D-4） | 低；**收益最高（解决 SETUP_NOTES 80% 痛点）** |
| **Phase 2 多路采集** | N 路 RTSP 并行 | `CameraSource` / `CameraManager` / `LatestFrameBuffer`；独立重连 | 中；并发与资源管理 |
| **Phase 3 分析池限流** | 防后端雪崩 | `InferencePool` + 全局信号量 + 每路背压 | 中；需压测后端并发上限 |
| **Phase 4 WS 多路复用 + 同屏前端** | 监控墙 UI | 网格布局；一条 WS 承载多路视频+结果+控制；`frame_id` 入帧；订阅机制 | 中；前端 canvas 渲染 N 路性能 |
| **Phase 5a 烧框源直接上屏** | 现有 `/yolo` 设备进网格 | 把烧框 MJPEG 当普通 MJPEG 源接入（Mode 0）；框天然在画面、无叠框层、无对齐 | **低；今天即可，验证端到端** |
| **Phase 5b 检测框分离叠加** | 框可交互的目标态 | 推动设备旁路 `{frame_id, boxes}`；`EdgeSource` + `MetadataChannel`；`detections` 消息；前端透明 canvas 叠框层；`frame_id` 帧级对齐 | 中；依赖 Phase 4 的 `frame_id` 通道 + 设备配合 |
| **Phase 6（可选）优化** | 带宽/CPU | WS 二进制帧、硬件 JPEG 编码、按需订阅、动态降频 | 低；增量优化 |

> **建议先做 Phase 1**：风险最低、收益最快，且立即移除当前最大的不稳定源（WebRTC + 硬编码 TURN）。
> **Phase 5a（烧框源直接上屏）不依赖任何对齐通道**，现有 `/yolo` 设备在 Phase 2 多路采集打通后即可显示，可作为最早的端到端验证。
> **Phase 5b（框分离叠加）依赖 Phase 4 打通的 `frame_id` 入帧通道**——所以视频回传务必直接走带 `frame_id` 的 WS-JPEG，而非 HTTP-MJPEG，否则对齐要返工。

---

## 7. 风险与权衡

- **CPU 瓶颈**：N 路 JPEG 编码在大 N 时吃 CPU。缓解：预览降分辨率/降帧率、降 JPEG 质量、`JPEG_OPTIMIZE`。⚠️ **不要想当然用 "Jetson NVENC"**——Orin NX/Nano 的硬件编码器在标准工具链里基本不可用（见 §5.8），换 H.264 在本机 = CPU 软编，反而加重负担；要硬件编码须换有可用 NVENC 的设备（如 AGX Orin）或服务端转码。
- **后端 VLM 并发上限**：是整个系统的真实吞吐瓶颈，需对目标后端（Ollama/vLLM）压测确定 `max_concurrency`。
- **延迟权衡**：MJPEG 比 WebRTC 延迟略高（百毫秒级），但对 VLM 秒级分析场景无影响——这是有意识的取舍。
- **带宽**：多路全分辨率同屏会超局域网舒适区；靠预览/分析分离 + 按需订阅控制。
- **对齐 vs 简单**：帧级 `frame_id` 对齐最准但要求设备配合打 `frame_id`；若设备只能给时间戳，退化为近似对齐，快速运动时框略滞后——需在设备侧能力与对齐精度间权衡。
- **元数据/视频不同步断流**：视频在、框断（或反之）时，前端须有降级策略（停叠旧框、标记检测离线），否则框会冻结在错误位置误导用户。

---

## 8. 待决策点

| 编号 | 决策 | 结论 |
|------|------|------|
| ~~D-1~~ | `CameraManager` 全局共享 vs 按会话隔离 | ✅ **全局共享**：多用户看同一批摄像头，VLM 结果按 `camera_id` 共享 |
| ~~D-2~~ | 视频回传 HTTP-MJPEG vs WS-JPEG | ✅ **WS-JPEG**：检测框对齐硬需求 |
| **D-3** | `max_concurrency` 取值 | ⏳ **待定**：需对后端 VLM 压测确定并发上限 |
| ~~D-4~~ | 浏览器摄像头源是否仍需支持 | ✅ **不支持**：视频源收敛为 RTSP/本地/边缘，aiortc/WebRTC 整体移除 |
| ~~D-5~~ | 边缘设备元数据通道形态 | ✅ **目标态分开传输**（选项 A）：视频帧与检测框两条消息，同源 `frame_id`。**现状**：手上 `/yolo` 设备是 Mode 0（框已烧进像素、无元数据通道），先以 Mode 0 上屏，再演进到 A（§2.5 / §5.4） |
| ~~D-6~~ | 检测离线/框丢失时前端策略 | ✅ **留空 + 标记离线**：不冻结错误框 |

> 仅剩 **D-3（`max_concurrency`）** 待定，且依赖实际后端压测，可在 Phase 3 实现时确定。

---

## 9. 验收标准（建议）

- [ ] N 路（≥4）RTSP 摄像头同屏稳定运行 ≥30 分钟无崩溃。
- [ ] 任意单路断线后能自动重连，不影响其余路。
- [ ] 局域网内无需任何 TURN/STUN 配置即可显示视频。
- [ ] 多用户/多会话并发访问，结果不串话。
- [ ] 后端 VLM 过载时系统降级（丢帧/降频）而非雪崩。
- [ ] `git grep -i "turn:" src/` 无残留硬编码 TURN（Phase 1 完成标志之一）。
- [ ] **检测框与画面帧级对齐**：画面中运动目标的框无明显滞后/漂移（同 `frame_id` 配对生效）。
- [ ] **框坐标归一化**：浏览器窗口缩放、预览分辨率变化时框位置仍准确。
- [ ] **检测通道断流降级**：元数据断开时前端停止叠旧框并标记「检测离线」，不冻结错误框。
