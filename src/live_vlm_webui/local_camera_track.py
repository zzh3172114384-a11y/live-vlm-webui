"""
Local Camera Video Track using PyAV (ffmpeg V4L2 backend).

Provides VideoStreamTrack that reads from /dev/video* devices.

SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
"""

import asyncio
import logging
import time
from typing import Optional

import av
from aiortc import VideoStreamTrack
from av import VideoFrame
from av.container import InputContainer
from av.video import VideoStream

logger = logging.getLogger(__name__)


class LocalCameraTrack(VideoStreamTrack):
    """
    Video track that reads from a local camera device using PyAV (ffmpeg).

    Example:
        track = LocalCameraTrack("/dev/video0")
        frame = await track.recv()
    """

    def __init__(
        self,
        device: str = "/dev/video0",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        input_format: str = "mjpeg",
    ):
        super().__init__()
        self.device = device
        self._stopped = False
        self._frame_count = 0
        self.container: Optional[InputContainer] = None
        self.stream: Optional[VideoStream] = None

        logger.info(f"Opening camera: {device} ({input_format}, {width}x{height}@{fps}fps)")

        options = {
            "video_size": f"{width}x{height}",
            "framerate": str(fps),
        }
        if input_format:
            options["input_format"] = input_format

        self.container = av.open(device, "r", format="v4l2", options=options)

        if not self.container.streams.video:
            raise RuntimeError(f"No video stream found on device: {device}")

        self.stream = self.container.streams.video[0]

        codec = self.stream.codec_context.name
        actual_width = self.stream.width or width
        actual_height = self.stream.height or height
        actual_fps = self.stream.average_rate or fps

        logger.info(
            f"Camera opened: {codec} {actual_width}x{actual_height} @ {actual_fps}fps"
        )

    async def recv(self) -> VideoFrame:
        if self._stopped:
            raise StopAsyncIteration

        try:
            loop = asyncio.get_event_loop()
            frame = await loop.run_in_executor(None, self._read_frame)

            if frame is None:
                raise StopAsyncIteration

            self._frame_count += 1

            if self._frame_count % 300 == 0:
                logger.debug(f"Local camera: Received {self._frame_count} frames")

            return frame

        except StopAsyncIteration:
            raise
        except Exception as e:
            logger.error(f"Error reading camera frame: {e}", exc_info=True)
            raise StopAsyncIteration

    def _read_frame(self) -> Optional[VideoFrame]:
        if self._stopped or not self.container or not self.stream:
            return None

        try:
            for packet in self.container.demux(self.stream):
                for frame in packet.decode():
                    if isinstance(frame, VideoFrame):
                        return frame
            return None

        except Exception as e:
            logger.warning(f"Error demuxing camera frame: {e}")
            return None

    def stop(self):
        self._stopped = True

        if self.container:
            try:
                self.container.close()
                logger.info(f"Camera released: {self._frame_count} frames received")
            except Exception as e:
                logger.warning(f"Error closing camera: {e}")
            finally:
                self.container = None
                self.stream = None

        super().stop()

    @property
    def is_connected(self) -> bool:
        return self.container is not None and not self._stopped

    def get_stats(self) -> dict:
        stats = {
            "device": self.device,
            "connected": self.is_connected,
            "frames_received": self._frame_count,
            "stopped": self._stopped,
        }
        if self.stream:
            stats.update({
                "codec": self.stream.codec_context.name,
                "width": self.stream.width,
                "height": self.stream.height,
                "fps": float(self.stream.average_rate) if self.stream.average_rate else None,
            })
        return stats
