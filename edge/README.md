# 边缘设备代码（edge）

这里放**运行在边缘设备（如 Jetson）上的程序**，不属于 OmniSight 服务本体（服务代码在 `src/live_vlm_webui/`）。部署时把对应脚本拷到设备上运行。

## `astra_camera_streamer.py`

Orbbec ASTRA S 摄像头的 MJPEG 串流 + YOLO 检测脚本（OpenNI2 取流 + ultralytics YOLO），在设备上跑，对外提供三个 HTTP 端点（默认 `:8088`）：

| 端点 | 内容 | OmniSight 用途 |
|------|------|----------------|
| `/camera` | 原始 MJPEG（无框，干净画面） | **Mode A**：作为视频源（框由浏览器叠加） |
| `/yolo`   | YOLO 检测框**烧进画面**的 MJPEG | Mode 0：直接显示（框已在像素里、会抖、不可控） |
| `/boxes`  | 最新检测框的**归一化坐标 JSON** | **Mode A**：浏览器据此用 canvas 叠框（可控、可平滑） |

> 推荐 **Mode A**：在 OmniSight 里源填 `http://<设备IP>:8088/camera`，框走同主机 `/boxes` 自动叠加。

### 运行（在设备上）

```bash
# 依赖：openni（OpenNI2 + 绑定）、ultralytics、opencv-python、numpy
python astra_camera_streamer.py
# 访问：http://<设备IP>:8088/camera  |  /yolo  |  /boxes
```

脚本顶部的 `OPENNI2_REDIST`、`YOLO_MODEL_PATH` 等路径按你的设备环境调整。

> 文件内 OmniSight 相关的新增/改动均用 `# OmniSight 新增` / `# [OmniSight 改]` 注释标记，便于区分原始脚本与适配改动。
