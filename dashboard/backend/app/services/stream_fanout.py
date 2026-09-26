"""Share one resilient MJPEG connection between dashboard viewers.

The Pi exposes one MJPEG endpoint per camera.  Opening that endpoint once per
browser card wastes Pi connections and makes a temporary disconnect affect
every card independently.  This service keeps one upstream connection per
camera and fans complete JPEG frames out to all subscribed browsers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

STREAM_CONTENT_TYPE = "multipart/x-mixed-replace; boundary=frame"
_QUEUE_SIZE = 3
_RECONNECT_MIN_SEC = 0.5
_RECONNECT_MAX_SEC = 5.0


class _JpegFrameParser:
    """Extract complete JPEG images from arbitrary HTTP chunk boundaries."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        frames: list[bytes] = []

        while True:
            start = self._buffer.find(b"\xff\xd8")
            if start < 0:
                # Keep a possible partial JPEG marker and discard old headers.
                if len(self._buffer) > 1:
                    del self._buffer[:-1]
                break

            if start:
                del self._buffer[:start]
            end = self._buffer.find(b"\xff\xd9", 2)
            if end < 0:
                # A broken upstream must not grow this buffer forever.
                if len(self._buffer) > 4 * 1024 * 1024:
                    del self._buffer[:-2]
                break

            end += 2
            frames.append(bytes(self._buffer[:end]))
            del self._buffer[:end]

        return frames


@dataclass
class _Channel:
    target: str
    subscribers: set[asyncio.Queue[bytes]] = field(default_factory=set)
    latest_frame: bytes | None = None
    task: asyncio.Task[None] | None = None


class MjpegStreamFanout:
    """Maintain one reconnecting Pi stream for each camera target."""

    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, target: str) -> tuple[_Channel, asyncio.Queue[bytes]]:
        async with self._lock:
            channel = self._channels.get(target)
            if channel is None:
                channel = _Channel(target=target)
                self._channels[target] = channel

            queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_QUEUE_SIZE)
            channel.subscribers.add(queue)
            if channel.latest_frame is not None:
                queue.put_nowait(channel.latest_frame)
            if channel.task is None or channel.task.done():
                channel.task = asyncio.create_task(
                    self._run(channel),
                    name=f"mjpeg:{target}",
                )
            return channel, queue

    async def unsubscribe(self, channel: _Channel, queue: asyncio.Queue[bytes]) -> None:
        task: asyncio.Task[None] | None = None
        async with self._lock:
            channel.subscribers.discard(queue)
            if channel.subscribers:
                return
            if self._channels.get(channel.target) is channel:
                self._channels.pop(channel.target, None)
            task = channel.task

        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        async with self._lock:
            channels = list(self._channels.values())
            self._channels.clear()
            tasks = [channel.task for channel in channels if channel.task is not None]
            for channel in channels:
                channel.subscribers.clear()

        for task in tasks:
            if task is not None:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _frame_packet(jpeg: bytes) -> bytes:
        return (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
            + jpeg
            + b"\r\n"
        )

    @staticmethod
    def _publish(channel: _Channel, packet: bytes) -> None:
        channel.latest_frame = packet
        for queue in tuple(channel.subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                # A slow browser must never block the Pi reader or other viewers.
                pass

    async def _run(self, channel: _Channel) -> None:
        timeout = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
        backoff = _RECONNECT_MIN_SEC
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            while channel.subscribers:
                parser = _JpegFrameParser()
                try:
                    async with client.stream("GET", channel.target) as response:
                        if response.status_code != 200:
                            raise httpx.HTTPStatusError(
                                f"upstream returned {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                        backoff = _RECONNECT_MIN_SEC
                        async for chunk in response.aiter_bytes():
                            for jpeg in parser.feed(chunk):
                                self._publish(channel, self._frame_packet(jpeg))
                        if channel.subscribers:
                            logger.info("MJPEG upstream closed, reconnecting: %s", channel.target)
                except asyncio.CancelledError:
                    raise
                except (httpx.HTTPError, OSError) as exc:
                    if channel.subscribers:
                        logger.info("MJPEG upstream unavailable (%s): %s", channel.target, exc)

                if channel.subscribers:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _RECONNECT_MAX_SEC)


stream_fanout = MjpegStreamFanout()

