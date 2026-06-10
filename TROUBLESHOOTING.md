# ICE Connection Troubleshooting

## Symptom

    aioice.ice - INFO - Connection(0) ICE failed
    live_vlm_webui.server - ERROR - ICE connection failed
    RTSP stream closed: 0 frames received

All ICE candidate pairs (host, srflx, relay) fail.

## Root Cause

 had .
With mirrored mode, WSL shares the Windows host IP (192.168.66.66).
WSL sends packets to this shared IP, the router needs hairpin NAT.
Router does not support it -> all packets dropped -> ICE fails.

## Fix

1. Remove  from .wslconfig
2.  to restart WSL
3. Check WSL IP: 172.18.247.175 
4. Update TURN URLs in server.py and index.html to use WSL IP
5. TURN server config (/etc/turnserver.conf): no external-ip setting

## Why default NAT works

| Mode | WSL IP | Windows access |
|------|--------|---------------|
| mirrored | same as host (192.168.66.66) | needs hairpin NAT -> FAIL |
| default NAT | separate (172.18.247.175) | via virtual switch -> OK |

With default NAT, Windows reaches WSL via virtual switch directly,
no router involved.

## Note

- WSL IP changes after wsl --shutdown, update TURN URLs
- Keep Windows Firewall off or allow UDP inbound
- Tested: WSL2, Ubuntu 24.04, Windows 11, live-vlm-webui 0.4.0

## Segfault on second VLM analysis

**Symptom**: Segmentation fault (core dumped) after second VLM response,
always at the same point.

**Root cause**: Race condition in RTSPVideoTrack. stop() closes the PyAV
container while _read_frame() is still reading from it in the executor
thread -> use-after-free -> segfault.

**Fix**: Added threading.Lock to rtsp_track.py protecting container access
in _read_frame(), stop(), and _reconnect().

Modified: src/live_vlm_webui/rtsp_track.py
- Added import threading
- Added self._lock = threading.Lock() in __init__
- Wrapped container access in _read_frame(), stop(), _reconnect()
