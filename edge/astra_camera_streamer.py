"""
MJPEG HTTP streamer for Orbbec ASTRA S camera with optional YOLO detection.
- /camera — raw MJPEG stream (OpenNI2 → JPEG encode)
- /yolo   — YOLO11n detection overlaid

URL: http://0.0.0.0:8088/camera  or  http://0.0.0.0:8088/yolo
"""
import http.server
import socketserver
import threading
import sys
import os
import time
import json  # [OmniSight 新增] /boxes 端点输出 JSON 用

import numpy as np
import cv2

os.environ['OPENNI2_REDIST'] = "/home/wheeltec/wheeltec_ros2/install/astra_camera/lib"

from openni import openni2
from ultralytics import YOLO

PORT = 8088
YOLO_MODEL_PATH = "/home/wheeltec/zzh_space/project/yolo11n.pt"
WIDTH, HEIGHT = 640, 480          # ASTRA S color default: VGA
FPS = 30
RAW_SKIP_FRAMES = 4               # raw: every 5th frame (~6 fps)
YOLO_SKIP_FRAMES = 2              # yolo: every 3rd frame (~10 fps)
JPEG_QUALITY = 80

# Shared state
_raw_jpeg = b""
_yolo_jpeg = b""
# ===== OmniSight 新增（Mode A）：最新 YOLO 框坐标(归一化 0~1)，供 /boxes 输出 =====
_latest_boxes = {"frame_id": 0, "boxes": []}
# ===== /OmniSight 新增 =====
_lock = threading.Lock()
_running = True

# YOLO model (lazy init)
_yolo_model = None
_yolo_lock = threading.Lock()


def get_yolo_model():
    global _yolo_model
    if _yolo_model is None:
        with _yolo_lock:
            if _yolo_model is None:
                print("Loading YOLO11n...", flush=True)
                _yolo_model = YOLO(YOLO_MODEL_PATH)
                print("YOLO11n loaded.", flush=True)
    return _yolo_model


def capture_loop():
    global _raw_jpeg, _yolo_jpeg, _latest_boxes, _running  # [OmniSight 新增] _latest_boxes

    print("Initialising OpenNI2...", flush=True)
    openni2.initialize()
    print("Opening ASTRA S camera...", flush=True)
    dev = openni2.Device.open_any()
    info = dev.get_device_info()
    print(f"Device: {info.name.decode()}  URI: {info.uri.decode()}  "
          f"Vendor: {info.vendor.decode()}", flush=True)

    # Create colour stream
    color = dev.create_color_stream()
    sensor_info = dev.get_sensor_info(openni2.SENSOR_COLOR)
    vmodes = sensor_info.videoModes
    # Try to find 640x480@30 or closest
    target_w, target_h = WIDTH, HEIGHT
    best = vmodes[0]
    for vm in vmodes:
        if vm.resolutionX == target_w and vm.resolutionY == target_h and vm.fps == 30:
            best = vm
            break
        elif vm.resolutionX == target_w and vm.resolutionY == target_h:
            best = vm
    W, H = best.resolutionX, best.resolutionY
    print(f"Colour stream: {W}x{H} @ {best.fps}fps  "
          f"({best.pixelFormat})", flush=True)
    color.set_video_mode(best)
    color.start()

    frame_idx = 0
    try:
        while _running:
            frame = color.read_frame()
            if frame is None:
                continue
            raw = frame.get_buffer_as_uint8()
            rgb = np.ctypeslib.as_array(raw).reshape((H, W, 3))
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            frame.close()

            frame_idx += 1

            # ── RAW stream (JPEG encode) ──
            if RAW_SKIP_FRAMES <= 0 or frame_idx % (RAW_SKIP_FRAMES + 1) == 0:
                _, enc = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                with _lock:
                    _raw_jpeg = enc.tobytes()

            # ── YOLO: extract boxes (overlay mode) + burned-in stream (legacy) ──
            if YOLO_SKIP_FRAMES <= 0 or frame_idx % (YOLO_SKIP_FRAMES + 1) == 0:
                try:
                    model = get_yolo_model()
                    results = model(bgr, verbose=False)
                    r = results[0]  # [OmniSight 改] 原为 annotated = results[0].plot()

                    # ===== OmniSight 新增（Mode A 开始）：提取框坐标数据，供 /boxes =====
                    # Box coords as NORMALIZED (0~1) data so the browser can scale to any
                    # display size. This is the data the overlay (Mode A) consumes via /boxes.
                    h, w = bgr.shape[:2]
                    boxes = []
                    for b in r.boxes:
                        x1, y1, x2, y2 = b.xyxy[0].tolist()
                        boxes.append({
                            "x": x1 / w, "y": y1 / h,
                            "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                            "label": r.names[int(b.cls[0])],
                            "conf": float(b.conf[0]),
                        })
                    with _lock:
                        _latest_boxes = {"frame_id": frame_idx, "boxes": boxes}
                    # ===== /OmniSight 新增（Mode A 结束）=====

                    # Burned-in /yolo stream kept for backward compatibility / quick viewing.
                    annotated = r.plot()
                    _, enc = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    with _lock:
                        _yolo_jpeg = enc.tobytes()
                except Exception as e:
                    print(f"YOLO error: {e}", flush=True)

    except Exception as e:
        print(f"Capture error: {e}", flush=True)
    finally:
        color.stop()
        dev.close()
        openni2.unload()
        print("Camera closed.", flush=True)


class CamHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/yolo"):
            self._serve_yolo()
        elif self.path.startswith("/boxes"):   # [OmniSight 新增] Mode A 框数据路由
            self._serve_boxes()
        else:
            self._serve_raw()

    # ===== OmniSight 新增（Mode A）：/boxes 端点 —— 输出最新框坐标 JSON =====
    def _serve_boxes(self):
        """Return the latest YOLO detections as JSON (normalized coords) for overlay mode."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with _lock:
            data = json.dumps(_latest_boxes)
        self.wfile.write(data.encode())
    # ===== /OmniSight 新增 =====

    def _serve_raw(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        last_sent = b""
        try:
            while _running:
                with _lock:
                    jpeg = _raw_jpeg
                if jpeg and jpeg != last_sent:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    last_sent = jpeg
                threading.Event().wait(0.05)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _serve_yolo(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        get_yolo_model()
        last_sent = b""
        try:
            while _running:
                with _lock:
                    jpeg = _yolo_jpeg
                if jpeg and jpeg != last_sent:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    last_sent = jpeg
                threading.Event().wait(0.05)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"MJPEG: http://0.0.0.0:{PORT}/camera  (raw)", flush=True)
    print(f"YOLO:  http://0.0.0.0:{PORT}/yolo   (detections)", flush=True)

    cap_thread = threading.Thread(target=capture_loop, daemon=True)
    cap_thread.start()

    # Wait for first frame
    for _ in range(50):
        with _lock:
            if _raw_jpeg:
                print(f"First raw frame: {len(_raw_jpeg)} bytes", flush=True)
                break
        threading.Event().wait(0.1)

    with ThreadedServer(("0.0.0.0", PORT), CamHandler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
            _running = False
