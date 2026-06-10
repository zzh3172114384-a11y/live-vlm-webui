# Project Setup & Fixes Summary
# Live VLM WebUI - WSL2 Environment

## Environment

- **OS**: Windows 11 + WSL2 (Ubuntu 24.04), default NAT mode
- **Project path**: ~/live-vlm-webui (venv: .venv/)
- **Python**: 3.12 (in WSL venv)
- **Browser**: Edge/Chrome on Windows, accessing https://localhost:8091
- **RTSP camera**: http://192.168.66.77:8088/camera (MJPEG, 1920x1080)
- **VLM API**: NVIDIA API Catalog (integrate.api.nvidia.com/v1)
- **API Key**: nvapi-YOUR_API_KEY_HERE
- **GPU**: NVIDIA GeForce GTX 1650 Ti

## Startup command

```bash
cd ~/live-vlm-webui
source .venv/bin/activate
live-vlm-webui --host 0.0.0.0 --port 8091 \
  --api-base https://integrate.api.nvidia.com/v1 \
  --api-key nvapi-YOUR_API_KEY_HERE \
  --model meta/llama-3.2-90b-vision-instruct
```

---

## Fix 1: ICE Connection Failed (0 frames received)

### Symptom
```
ICE connection state: failed
ICE connection failed - check firewall/NAT settings
RTSP stream closed: 0 frames received
```

### Root cause (3 layers):

**Layer 1 - No TURN server**: STUN-only ICE config can not punch through
WSL2 NAT. The browser (Windows) and Python server (WSL) are in separate
network namespaces.

**Layer 2 - mirrored networking made it worse**: .wslconfig had
networkingMode=mirrored. This caused WSL to share Windows host IP
(192.168.66.66). WSL sending to this shared IP requires router hairpin NAT,
which the router does not support.

**Layer 3 - Windows Firewall**: blocks inbound UDP on random ports used
by WebRTC.

### Fix:

1. **Remove networkingMode=mirrored** from %USERPROFILE%/.wslconfig:
   ```ini
   [wsl2]
   firewall=false
   ```

2. **wsl --shutdown** to restart WSL

3. **Install coturn** (TURN server, ~360KB):
   ```bash
   sudo apt install -y coturn
   ```

4. **Configure coturn** (/etc/turnserver.conf):
   ```
   listening-port=3478
   fingerprint
   lt-cred-mech
   user=admin:livevlm2024
   realm=localhost
   verbose
   ```
   No external-ip - let coturn auto-detect WSL IP.

5. **Start coturn**: sudo systemctl restart coturn

6. **Add TURN to ICE config** in two files:
   - server.py: RTCIceServer(urls=["turn:<WSL_IP>:3478"], ...)
   - index.html: { urls: "turn:<WSL_IP>:3478", ... }

7. **Turn off Windows Firewall** (or add UDP inbound rule)

8. **WSL IP changes** after every wsl --shutdown. Check with hostname -I
   and update TURN URLs accordingly.

---

## Fix 2: Segmentation Fault on Second VLM Analysis

### Symptom
```
Frame 150: Sending to VLM (interval=30)
ICE connection state: closed
RTSP stream closed: 157 frames received
Segmentation fault (core dumped)
```
Always happens on the SECOND VLM analysis. First one works fine.

### Root cause
Race condition in rtsp_track.py. stop() closes the PyAV container while
_read_frame() is still reading from it in the executor thread ->
use-after-free -> segfault.

### Fix
Added threading.Lock to RTSPVideoTrack in rtsp_track.py:
- import threading
- self._lock = threading.Lock() in __init__
- with self._lock: wraps container access in _read_frame(), stop(), _reconnect()

---

## Key files modified

| File | Change |
|------|--------|
| %USERPROFILE%/.wslconfig | Removed networkingMode=mirrored |
| /etc/turnserver.conf | TURN server config |
| src/live_vlm_webui/server.py | Added TURN to ICE servers |
| src/live_vlm_webui/static/index.html | Added TURN to browser ICE config |
| src/live_vlm_webui/rtsp_track.py | threading.Lock for segfault fix |

## Note for future sessions

- After wsl --shutdown: check WSL IP, update TURN URLs if changed
- coturn is managed by systemd (sudo systemctl restart coturn)
- Windows Firewall must be OFF or allow UDP inbound
- Keep networkingMode OUT of .wslconfig
