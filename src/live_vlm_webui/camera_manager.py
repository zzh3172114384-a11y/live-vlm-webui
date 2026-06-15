# SPDX-FileCopyrightText: Copyright (c) 2026 OmniSight Contributors.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Camera manager: a global registry of shared camera sources for the monitoring wall.

Each camera opens its source ONCE and runs the VLM ONCE, regardless of how many
browsers view it (decision D-1: cameras are global / shared by camera_id). Viewers
attach via /stream?camera_id=... and read the camera's latest JPEG frame, so a slow
viewer never blocks the reader and never sees a backlog of stale frames.

Each camera has its own reader task with exponential-backoff reconnect, so one camera
dropping or reconnecting never affects the others.
"""

import asyncio
import io
import logging
import time

from .rtsp_track import RTSPVideoTrack
from .local_camera_track import LocalCameraTrack
from .video_processor import VideoProcessorTrack

logger = logging.getLogger(__name__)


class Camera:
    """A single shared camera: one source + one VLM stream + a latest-frame buffer."""

    JPEG_QUALITY = 80

    def __init__(self, camera_id: str, url: str, vlm_service, text_callback=None):
        self.camera_id = camera_id
        self.url = url
        self.vlm_service = vlm_service
        self.text_callback = text_callback

        # Latest-frame buffer (covers slow/late viewers without blocking the reader)
        self.latest_jpeg = None
        self.frame_id = 0

        # Health / stats
        self.connected = False
        self.reconnects = 0
        self.last_frame_time = 0.0
        self.error = None

        self._source = None
        self._stopped = False
        self._task = None

    def _open_source(self):
        if self.url.startswith("local://"):
            return LocalCameraTrack(self.url[len("local://") :])
        return RTSPVideoTrack(self.url)

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self):
        """Read frames forever, with exponential-backoff reconnect on failure."""
        backoff = 1.0
        while not self._stopped:
            try:
                self._source = self._open_source()
                # Route frames through the processor so the VLM runs once per camera
                processor = VideoProcessorTrack(
                    self._source, self.vlm_service, self.text_callback
                )
                self.connected = True
                self.error = None
                backoff = 1.0
                logger.info(f"[cam:{self.camera_id}] source opened: {self.url}")

                while not self._stopped:
                    frame = await processor.recv()
                    buf = io.BytesIO()
                    frame.to_image().save(buf, format="JPEG", quality=self.JPEG_QUALITY)
                    self.latest_jpeg = buf.getvalue()
                    self.frame_id += 1
                    self.last_frame_time = time.time()

            except StopAsyncIteration:
                logger.info(f"[cam:{self.camera_id}] source ended")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error = str(e)
                logger.warning(f"[cam:{self.camera_id}] reader error: {e}")

            self.connected = False
            try:
                if self._source:
                    self._source.stop()
            except Exception:
                pass
            self._source = None

            if self._stopped:
                break
            self.reconnects += 1
            logger.info(
                f"[cam:{self.camera_id}] reconnecting in {backoff:.0f}s "
                f"(attempt {self.reconnects})"
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * 2, 30.0)

        try:
            if self._source:
                self._source.stop()
        except Exception:
            pass
        logger.info(f"[cam:{self.camera_id}] reader stopped")

    def stop(self):
        self._stopped = True
        if self._task:
            self._task.cancel()

    def stats(self) -> dict:
        age = (time.time() - self.last_frame_time) if self.last_frame_time else None
        return {
            "camera_id": self.camera_id,
            "url": self.url,
            "connected": self.connected,
            "frame_id": self.frame_id,
            "reconnects": self.reconnects,
            "last_frame_age": round(age, 2) if age is not None else None,
            "error": self.error,
        }


class CameraManager:
    """Global registry of shared cameras, keyed by camera_id."""

    def __init__(self):
        self._cameras = {}

    def get(self, camera_id: str):
        return self._cameras.get(camera_id)

    def list(self) -> list:
        return [c.stats() for c in self._cameras.values()]

    def add(self, camera_id: str, url: str, vlm_service, text_callback=None) -> Camera:
        """Add a camera (idempotent: returns the existing one if camera_id is taken)."""
        existing = self._cameras.get(camera_id)
        if existing:
            return existing
        cam = Camera(camera_id, url, vlm_service, text_callback)
        self._cameras[camera_id] = cam
        return cam

    async def remove(self, camera_id: str) -> bool:
        cam = self._cameras.pop(camera_id, None)
        if cam:
            cam.stop()
        return cam is not None

    async def stop_all(self):
        for cam in list(self._cameras.values()):
            cam.stop()
        self._cameras.clear()
