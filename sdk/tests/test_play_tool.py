"""Tests for the clip.play example tool and its helpers (offline)."""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave

import pytest

opuslib = pytest.importorskip("opuslib")

from clip.tools.play import _FRAME_SAMPLES, _SAMPLE_RATE, _Player, _WavWriter, build_parser, run

import clip
import clip.tools.play as play_tool

from conftest import FakeTransport
from test_stream_tool import SESSION, StreamTransport, end_frame, start_frame  # noqa: F401


def _encode_sine(frames: int = 25, freq: float = 440.0) -> list[bytes]:
    encoder = opuslib.Encoder(_SAMPLE_RATE, 1, opuslib.APPLICATION_AUDIO)
    packets = []
    for i in range(frames):
        samples = []
        for n in range(_FRAME_SAMPLES):
            t = (i * _FRAME_SAMPLES + n) / _SAMPLE_RATE
            samples.append(int(12000 * math.sin(2 * math.pi * freq * t)))
        pcm = struct.pack(f"<{_FRAME_SAMPLES}h", *samples)
        packets.append(encoder.encode(pcm, _FRAME_SAMPLES))
    return packets


def test_wav_writer_roundtrip(tmp_path):
    packets = _encode_sine()
    path = tmp_path / "out.wav"
    writer = _WavWriter(path)
    for packet in packets:
        writer.feed(packet)
    writer.close()

    assert writer.frames == len(packets)
    assert writer.decode_errors == 0
    assert writer.seconds == pytest.approx(len(packets) * 0.02)

    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == _SAMPLE_RATE
        raw = w.readframes(w.getnframes())
    assert w.getnframes() == len(packets) * _FRAME_SAMPLES
    decoded = struct.unpack(f"<{len(raw)//2}h", raw)
    # first codec frame is attenuated (cold state); compare from frame 2 on
    peak = max(abs(s) for s in decoded[_FRAME_SAMPLES * 2:])
    assert peak > 5000  # a 440 Hz tone at this amplitude survives the round trip


def test_wav_writer_skips_bad_frames(tmp_path):
    packets = _encode_sine(frames=5)
    writer = _WavWriter(tmp_path / "out.wav")
    writer.feed(b"\xff\xff\xff")  # corrupted Opus packet
    for packet in packets:
        writer.feed(packet)
    writer.close()
    assert writer.decode_errors == 1
    assert writer.frames == len(packets)


def test_wav_writer_close_is_idempotent(tmp_path):
    writer = _WavWriter(tmp_path / "out.wav")
    writer.close()
    writer.close()  # must not raise


def test_player_partial_construction_cleans_up(monkeypatch):
    """If the audio device fails mid-construction, everything opened so far
    is closed before the error propagates (no leaked stream)."""
    opened = []

    class FakeOutputStream:
        def __init__(self, **_kwargs):
            self.closed = False
            opened.append(self)

        def start(self):
            raise RuntimeError("device gone")

        def stop(self):
            pass

        def close(self):
            self.closed = True

        def write(self, _samples):
            pass

    class FakeSounddevice:
        OutputStream = FakeOutputStream

    monkeypatch.setitem(sys.modules, "sounddevice", FakeSounddevice)
    with pytest.raises(ValueError, match="cannot open audio device"):
        _Player(buffer_ms=100.0)
    assert len(opened) == 1
    assert opened[0].closed  # constructor cleaned up after itself


# --- run() lifecycle --------------------------------------------------------


class StubPlayer:
    """Headless stand-in so tool tests never touch a real audio device."""

    instances: list["StubPlayer"] = []
    fail = False

    def __init__(self, buffer_ms: float, device=None):
        if StubPlayer.fail:
            raise ValueError("stub player configured to fail")
        from clip.jitter import JitterBuffer

        self.jbuf = JitterBuffer(max(0, round(buffer_ms / 20.0)))
        self.closed = False
        StubPlayer.instances.append(self)

    def feed(self, payload: bytes) -> None:
        self.jbuf.put(payload)

    def close(self) -> None:
        self.closed = True

    def print_stats(self) -> None:
        print("stub playback stats")


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        transport="ble",
        address=None,
        name="Clip",
        host="192.168.4.1",
        port=8089,
        out=None,
        duration=0.05,
        pause_at=None,
        resume_at=None,
        buffer_ms=None,
        device=None,
        wav=None,
        simulate_playback=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _use(monkeypatch, transport) -> None:
    monkeypatch.setattr(play_tool, "make_client", lambda args: clip.ClipClient(transport))
    monkeypatch.setattr(play_tool, "_Player", StubPlayer)
    StubPlayer.instances = []
    StubPlayer.fail = False


async def test_play_run_happy_path(monkeypatch, tmp_path):
    transport = StreamTransport()
    _use(monkeypatch, transport)
    monkeypatch.chdir(tmp_path)
    exit_code = await run(_args(duration=0.05))
    assert exit_code == 0
    assert (tmp_path / f"rtc-{SESSION}.bin").exists()
    assert not (tmp_path / f"rtc-{SESSION}.bin.part").exists()
    assert transport._file_frame_handler is None
    assert StubPlayer.instances and StubPlayer.instances[0].closed


async def test_play_run_rejects_udp(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="BLE-only"):
        await run(_args(transport="udp"))
    assert list(tmp_path.iterdir()) == []


async def test_play_player_failure_leaks_nothing(monkeypatch, tmp_path):
    """Player construction fails -> no capture file, clean error."""
    transport = StreamTransport()
    _use(monkeypatch, transport)
    StubPlayer.fail = True
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="stub player configured to fail"):
        await run(_args(duration=0.05))
    # Capture is opened AFTER the player: nothing on disk.
    assert list(tmp_path.iterdir()) == []
    assert transport._file_frame_handler is None


def test_play_parser_keeps_playback_options() -> None:
    args = build_parser().parse_args([])
    assert args.buffer_ms is None
    assert args.wav is None
    assert args.simulate_playback is False
