"""Live RTC audio streaming from the Clip over BLE.

An RTC session (`AT+START=rtc`) runs the microphone pipeline without writing
to SD; `AT+DOWNLOAD=<session>` then flushes a small pre-buffer and streams
live Opus frames as STREAM_START/STREAM_DATA/STREAM_END notifications on the
file-data characteristic. Frames lost over the air appear as sequence gaps;
the receiver never blocks the device, which drops frames under backpressure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from .exceptions import ProtocolError, TransferError, TransferTimeoutError
from .protocol import (
    StreamDataFrame,
    StreamEndFrame,
    StreamStartFrame,
    decode_file_frame,
)
from .validation import session_id as _session_id

if TYPE_CHECKING:
    from .client import ClipClient

STREAM_END_STOPPED = 0
STREAM_END_TIMEOUT = 1
STREAM_END_DISCONNECT = 2

FrameCallback = Callable[[bytes], None]


class StreamReceiver:
    """Consumes STREAM_* frames from the file-data notification path.

    Unlike FileReceiver there is no persistence here: every STREAM_DATA
    payload is handed to the frame callback as soon as it arrives. The
    receiver only tracks ordering so callers can report lost frames.
    """

    def __init__(self, on_frame: FrameCallback | None = None) -> None:
        self.on_frame = on_frame
        self.started = asyncio.Event()
        self.ended = asyncio.Event()
        self.error: Exception | None = None
        self.session_id: str | None = None
        self.end_reason: int | None = None
        self.frames_received = 0
        self.bytes_received = 0
        self.sequence_gaps = 0
        self._expected_sequence: int | None = None

    def feed(self, raw_frame: bytes) -> None:
        if self.error is not None or self.ended.is_set():
            return
        try:
            frame = decode_file_frame(raw_frame)
            if isinstance(frame, StreamStartFrame):
                self._on_start(frame)
            elif isinstance(frame, StreamDataFrame):
                self._on_data(frame)
            elif isinstance(frame, StreamEndFrame):
                self._on_end(frame)
            else:
                raise TransferError(
                    f"unexpected {type(frame).__name__} during RTC stream"
                )
        except ProtocolError as exc:
            self._fail(TransferError(str(exc)))
        except Exception as exc:
            self._fail(exc if isinstance(exc, TransferError) else TransferError(str(exc)))

    async def wait_start(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.started.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TransferTimeoutError("RTC stream did not start in time") from exc
        if self.error is not None:
            raise self.error

    async def wait_end(self, timeout: float | None = None) -> None:
        try:
            if timeout is None:
                await self.ended.wait()
            else:
                await asyncio.wait_for(self.ended.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TransferTimeoutError("RTC stream did not end in time") from exc
        if self.error is not None:
            raise self.error

    def _on_start(self, frame: StreamStartFrame) -> None:
        if self.started.is_set():
            raise TransferError("received STREAM_START twice")
        self.session_id = frame.session_id
        self.started.set()

    def _on_data(self, frame: StreamDataFrame) -> None:
        if not self.started.is_set():
            raise TransferError("received STREAM_DATA before STREAM_START")
        if self._expected_sequence is not None and frame.sequence != self._expected_sequence:
            self.sequence_gaps += 1
        self._expected_sequence = (frame.sequence + 1) & 0xFFFF
        self.frames_received += 1
        self.bytes_received += len(frame.payload)
        if self.on_frame is not None:
            self.on_frame(frame.payload)

    def _on_end(self, frame: StreamEndFrame) -> None:
        if not self.started.is_set():
            raise TransferError("received STREAM_END before STREAM_START")
        self.end_reason = frame.reason
        self.ended.set()

    def _fail(self, error: Exception) -> None:
        if self.error is None:
            self.error = error
        self.ended.set()


async def stream_session(client: "ClipClient", session: str, receiver: StreamReceiver) -> None:
    """Attach the receiver and start the RTC stream with AT+DOWNLOAD.

    Returns once the device acknowledges the command; frames keep arriving
    until STREAM_END. The caller owns stopping the stream (AT+STOP) and
    detaching the handler via transport.set_file_frame_handler(None).
    """
    sid = _session_id(session)
    client.transport.set_file_frame_handler(receiver.feed)
    try:
        await client.start_download(sid)
    except Exception:
        client.transport.set_file_frame_handler(None)
        raise
