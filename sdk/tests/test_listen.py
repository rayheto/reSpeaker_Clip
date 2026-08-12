"""Tests for clip.tools.listen helpers (offline, no device needed)."""

import math
import struct
import wave

import pytest

opuslib = pytest.importorskip("opuslib")

from clip.tools.listen import _FRAME_SAMPLES, _SAMPLE_RATE, _WavWriter


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
