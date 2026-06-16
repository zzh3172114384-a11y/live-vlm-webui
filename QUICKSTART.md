# OmniSight 快速开始

OmniSight（全视）——局域网多路视觉智能监控墙。视频经 **MJPEG** 回传（无 WebRTC），
检测帧送 OpenAI 兼容 VLM 分析，结果与 GPU/系统状态经 WebSocket 实时回推。

> 当前开发/运行环境：**原生 Windows + PowerShell + Python 3.12**。Linux/Mac 步骤类似。

---

## 1. 安装

```powershell
# 在项目根目录
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
# 国内网络慢可加清华镜像：pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> Python 用 **3.12**（3.14 暂无 av/opencv 等轮子）。

## 2. 配置 `.env`

```powershell
Copy-Item .env.example .env     # 复制模板
notepad .env                     # 按需修改
```

至少要改的：

| 配置项 | 说明 |
|--------|------|
| `LIVE_VLM_API_KEY` | VLM 后端密钥。NVIDIA 云端：在 https://build.nvidia.com/settings/api-keys 生成;本地后端(Ollama 等)填 `EMPTY` |
| `LIVE_VLM_API_BASE` / `LIVE_VLM_DEFAULT_MODEL` | 后端地址与模型（云端默认 NVIDIA;本地改成 `http://localhost:11434/v1` + `llava:7b` 等） |
| `LIVE_VLM_PORT` | 监听端口（默认 8091） |
| `NO_PROXY` / `no_proxy` | **若本机开了 Clash 等系统代理**：把局域网摄像头/边缘设备 IP 加进去（只认具体 IP，不支持网段），否则视频连不上;无代理可删这两行 |
| `LIVE_VLM_MAX_CONCURRENCY` | 全局并发推理上限（多路防雪崩，默认 2） |

完整字段说明见 `.env.example` 内注释。各项也可用命令行参数覆盖（优先级：命令行 > `.env` > 默认）。

## 3. 启动

```powershell
live-vlm-webui            # 读取 .env，不带参数即可
```

启动后访问（自签证书会有安全提示，点"继续"）：
- **单路视图**：`https://localhost:8091/`
- **监控墙（多路同屏）**：`https://localhost:8091/wall`（或单路视图右上角"监控墙"）

> 无 SSL 证书时会自动回退到 `http://`（局域网够用）。
> 右上角可切 **中文/English** 与 **深色/浅色/自动**主题（两页共享、自动记忆）。

## 4. 用法

### 单路视图 `/`
左侧「视频源」填地址 → 点开始。支持：
- RTSP：`rtsp://user:pass@ip:554/stream`
- HTTP-MJPEG / 边缘设备：`http://<ip>:8088/camera`
- 服务端本地摄像头：`local://<device>`

### 监控墙 `/wall`
顶部「添加摄像头」：填 **ID**（如 `cam1`）+ **源地址** → 添加。可加多路同屏。
- 多用户看同一路 = **只开一个源、VLM 只跑一份**（共享）。
- 离屏的格子自动暂停拉流;某路掉线约 5 秒后该格变灰标"离线"。

### 检测框（可选，Mode A）
边缘设备若同时提供 `/camera`（干净画面）和 `/boxes`（框坐标 JSON），
源填 `http://<ip>:8088/camera`，OmniSight 会用浏览器 canvas 叠加检测框（可控、清晰）。
> 若源用 `…/yolo`（框已烧进画面），框是设备画死的、会抖，且关不掉（详见方案 §11.3）。
> 边缘设备示例脚本见 `astra_camera_streamer.py`（部署到设备端运行，不属于本服务）。

## 5. 停止

`Ctrl+C`，或另开终端运行 `live-vlm-webui-stop`。

---

## 常见问题

- **`https://localhost` 连不上 / curl 报错**：系统代理（Clash）拦截了 localhost。浏览器一般正常;命令行用 `curl -k --noproxy "*" https://localhost:8091/`。
- **视频连不上（RTSP/MJPEG 超时）**：①设备是否在线、地址对不对;②开了代理的话，设备 IP 是否加进 `.env` 的 `NO_PROXY`。
- **VLM 报 404 / 401**：检查 `LIVE_VLM_API_KEY`、`LIVE_VLM_DEFAULT_MODEL` 是否有效。
- 架构与设计细节见 `MULTI_SOURCE_STREAMING_PLAN.md` 与 `CLAUDE.md`。
