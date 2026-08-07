from __future__ import annotations

import struct

import pytest

from clip import ClipClient
from clip.exceptions import TransferError, TransferTimeoutError
from clip.stream import STREAM_END_STOPPED, StreamReceiver, stream_session

from conftest import FakeTransport

SESSION = "20260807120000"


def start_frame(session: str = SESSION) -> bytes:
    encoded = session.encode("ascii")
    return b"\x13" + bytes((len(encoded),)) + encoded


def data_frame(sequence: int, payload: bytes = b"opus") -> bytes:
    return b"\x14" + struct.pack("<HH", sequence, len(payload)) + payload


def end_frame(reason: int = STREAM_END_STOPPED) -> bytes:
    return b"\x15" + bytes((reason,))


def test_full_stream_sequence() -> None:
    frames: list[bytes] = []
    receiver = StreamReceiver(on_frame=frames.append)
    receiver.feed(start_frame())
    assert receiver.started.is_set()
    receiver.feed(data_frame(0, b"a"))
    receiver.feed(data_frame(1, b"bb"))
    receiver.feed(end_frame())
    assert receiver.ended.is_set()
    assert frames == [b"a", b"bb"]
    assert receiver.session_id == SESSION
    assert receiver.frames_received == 2
    assert receiver.bytes_received == 3
    assert receiver.sequence_gaps == 0
    assert receiver.end_reason == STREAM_END_STOPPED


def test_counts_sequence_gaps() -> None:
    receiver = StreamReceiver()
    receiver.feed(start_frame())
    receiver.feed(data_frame(0))
    receiver.feed(data_frame(3))  # 1, 2 lost over the air
    receiver.feed(data_frame(4))
    assert receiver.sequence_gaps == 1
    assert receiver.frames_received == 3


def test_sequence_wraps_at_16_bits() -> None:
    receiver = StreamReceiver()
    receiver.feed(start_frame())
    receiver.feed(data_frame(0xFFFE))
    receiver.feed(data_frame(0xFFFF))
    receiver.feed(data_frame(0x0000))
    assert receiver.sequence_gaps == 0


def test_rejects_data_before_start() -> None:
    receiver = StreamReceiver()
    receiver.feed(data_frame(0))
    assert isinstance(receiver.error, TransferError)
    assert receiver.ended.is_set()


def test_rejects_foreign_frame_types() -> None:
    receiver = StreamReceiver()
    receiver.feed(start_frame())
    receiver.feed(b"\x11\x00\x00\x00\x00")  # FILE_END has no place in a stream
    assert isinstance(receiver.error, TransferError)


def test_rejects_duplicate_start() -> None:
    receiver = StreamReceiver()
    receiver.feed(start_frame())
    receiver.feed(start_frame())
    assert isinstance(receiver.error, TransferError)


def test_ignores_frames_after_end() -> None:
    receiver = StreamReceiver()
    receiver.feed(start_frame())
    receiver.feed(end_frame())
    receiver.feed(data_frame(0))
    assert receiver.frames_received == 0


async def test_wait_end_times_out() -> None:
    receiver = StreamReceiver()
    receiver.feed(start_frame())
    with pytest.raises(TransferTimeoutError):
        await receiver.wait_end(timeout=0.01)


async def test_wait_start_surfaces_feed_error() -> None:
    receiver = StreamReceiver()
    receiver.feed(data_frame(0))  # fails: no STREAM_START yet
    with pytest.raises(TransferError):
        await receiver.wait_start(timeout=1.0)


async def test_stream_session_sends_download_and_detaches_on_failure() -> None:
    transport = FakeTransport([{"ok": True, "data": {"state": "streaming"}}])
    client = ClipClient(transport)
    await client.connect()
    receiver = StreamReceiver()
    await stream_session(client, SESSION, receiver)
    assert transport.commands == [f"AT+DOWNLOAD={SESSION}"]
    assert transport._file_frame_handler == receiver.feed
    transport._emit_file_frame(start_frame())
    transport._emit_file_frame(data_frame(0))
    transport._emit_file_frame(end_frame())
    await receiver.wait_end(timeout=1.0)
    assert receiver.frames_received == 1


async def test_stream_session_rejects_invalid_session_id() -> None:
    transport = FakeTransport([])
    client = ClipClient(transport)
    await client.connect()
    with pytest.raises(ValueError):
        await stream_session(client, "not-a-session", StreamReceiver())
    assert transport._file_frame_handler is None


async def test_client_start_rtc_returns_session() -> None:
    transport = FakeTransport([{"ok": True, "data": {"session": SESSION, "mode": "rtc"}}])
    client = ClipClient(transport)
    await client.connect()
    assert await client.start_rtc() == SESSION
    assert transport.commands == ["AT+START=rtc"]


def test_dispatch_is_synchronous_no_added_latency() -> None:
    """The receiver must hand frames to the callback inside feed() — no queueing."""
    import time

    delivered: list[float] = []
    receiver = StreamReceiver(on_frame=lambda payload: delivered.append(time.monotonic()))
    receiver.feed(start_frame())
    send_at = time.monotonic()
    receiver.feed(data_frame(0))
    assert len(delivered) == 1
    assert delivered[0] - send_at < 0.001


async def test_latency_stats() -> None:
    import asyncio

    receiver = StreamReceiver()
    receiver.feed(start_frame())
    assert receiver.first_frame_delay_s is None
    await asyncio.sleep(0.05)
    receiver.feed(data_frame(0))
    await asyncio.sleep(0.02)
    receiver.feed(data_frame(1))
    delay = receiver.first_frame_delay_s
    assert delay is not None and 0.04 <= delay <= 0.5
    assert receiver.max_inter_frame_ms >= 15.0
    avg = receiver.avg_inter_frame_ms
    assert avg is not None and 15.0 <= avg <= 100.0
    assert receiver.first_frame_at is not None
    assert receiver.last_frame_at is not None
