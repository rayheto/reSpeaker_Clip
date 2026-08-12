"""Stream live RTC audio from the Clip and save Opus frames to a file.

Each frame is written as a 2-byte little-endian length followed by the raw
Opus packet, so the capture can be decoded by any Opus decoder offline.

Optionally plays the stream live through the sound card with a jitter buffer
(``--play``, needs ``pip install 'respeaker-clip-sdk[play]'``) and/or runs an
offline jitter-buffer simulation over the recorded arrival times
(``--simulate-playback``, no audio hardware needed).

``--wav [PATH]`` additionally decodes the stream to a 16 kHz mono WAV file
(default name ``rtc-<session>.wav``) for offline listening and analysis.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import struct
import threading
from pathlib import Path

from ..exceptions import ClipError
from ..jitter import FRAME_MS, JitterBuffer, simulate_playback
from ..stream import (
    STREAM_END_DISCONNECT,
    STREAM_END_STOPPED,
    STREAM_END_TIMEOUT,
    StreamReceiver,
)
from .common import add_connection_options, make_client

_END_REASONS = {
    STREAM_END_STOPPED: "stopped",
    STREAM_END_TIMEOUT: "start-timeout",
    STREAM_END_DISCONNECT: "ble-disconnect",
}

_HISTOGRAM_BUCKET_MS = 50.0
_HISTOGRAM_BAR_WIDTH = 20

_SAMPLE_RATE = 16000
_FRAME_SAMPLES = 320  # 20 ms at 16 kHz mono
_SIM_DEPTHS_MS = (0.0, 50.0, 100.0, 200.0)


def _print_summary(receiver) -> None:
    reason = _END_REASONS.get(receiver.end_reason, str(receiver.end_reason))
    print(f"Ended: reason={reason}")
    rows = [
        ("frames", str(receiver.frames_received)),
        ("bytes", str(receiver.bytes_received)),
        ("seq_gaps", str(receiver.sequence_gaps)),
    ]
    first_delay = receiver.first_frame_delay_s
    if first_delay is not None:
        rows.append(("first_frame", f"{first_delay * 1000:.0f} ms"))
        avg = receiver.avg_inter_frame_ms
        if avg is not None:
            rows.append(("avg_inter_frame", f"{avg:.1f} ms"))
            rows.append(("max_inter_frame", f"{receiver.max_inter_frame_ms:.0f} ms"))
    for name, value in rows:
        print(f"  {name:<15} : {value}")
    if first_delay is not None and receiver.avg_inter_frame_ms is not None:
        _print_latency_histogram(receiver.inter_frame_gaps_ms)


def _print_latency_histogram(gaps: tuple[float, ...]) -> None:
    if not gaps:
        return
    bucket_count = max(1, math.ceil(max(gaps) / _HISTOGRAM_BUCKET_MS))
    buckets = [0] * bucket_count
    for gap in gaps:
        buckets[min(int(gap // _HISTOGRAM_BUCKET_MS), bucket_count - 1)] += 1
    labels = [
        f"{i * _HISTOGRAM_BUCKET_MS:.0f}-{(i + 1) * _HISTOGRAM_BUCKET_MS:.0f} ms"
        for i in range(bucket_count)
    ]
    width = max(len(label) for label in labels)
    print()
    print("Inter-frame latency distribution:")
    for label, count in zip(labels, buckets):
        pct = count * 100.0 / len(gaps)
        bars = round(pct * _HISTOGRAM_BAR_WIDTH / 100.0)
        if count and not bars:
            bars = 1
        print(f"  {label:>{width}} : {pct:5.1f}%  {'\u25a0' * bars}")


class _Player:
    """Reference live playback: jitter buffer + Opus decode + sound card.

    The audio device clock paces consumption: ``OutputStream.write()`` blocks
    while the device ring is full, so the consumer loop needs no timers.
    """

    def __init__(self, buffer_ms: float, device: str | None = None) -> None:
        try:
            import numpy as np
            import opuslib
            import sounddevice as sd
        except ImportError as exc:
            raise ValueError(
                "playback needs extra dependencies: "
                "pip install 'respeaker-clip-sdk[play]' "
                f"(missing: {exc.name})"
            ) from exc

        self._np = np
        depth_frames = round(buffer_ms / FRAME_MS)
        self.jbuf = JitterBuffer(depth_frames)
        self._silence = np.zeros(_FRAME_SAMPLES, dtype=np.int16)
        self._decoder = opuslib.Decoder(_SAMPLE_RATE, 1)
        try:
            self._stream = sd.OutputStream(
                samplerate=_SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=_FRAME_SAMPLES,
                device=None if device is None else int(device) if device.isdigit() else device,
            )
        except Exception as exc:
            raise ValueError(f"cannot open audio device {device!r}: {exc}") from exc
        self._stream.start()
        self._stop = threading.Event()
        self.decode_errors = 0
        self._thread = threading.Thread(target=self._run, name="clip-play", daemon=True)
        self._thread.start()

    def feed(self, payload: bytes) -> None:
        self.jbuf.put(payload)

    def _run(self) -> None:
        while not self._stop.is_set():
            frame = self.jbuf.get()
            if frame is None:
                samples = self._silence
            else:
                try:
                    pcm = self._decoder.decode(frame, _FRAME_SAMPLES)
                    samples = self._np.frombuffer(pcm, dtype=self._np.int16)
                except Exception:
                    self.decode_errors += 1
                    samples = self._silence
            self._stream.write(samples)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._stream.stop()
        self._stream.close()

    def print_stats(self) -> None:
        s = self.jbuf.stats
        print()
        print("Playback (jitter buffer):")
        print(f"  depth target    : {self.jbuf.depth} frames ({self.jbuf.depth * FRAME_MS:.0f} ms)")
        print(f"  frames played   : {s.frames_out}")
        print(f"  start wait      : {s.start_wait_frames} silent ticks")
        print(f"  underruns       : {s.underruns} ({s.underrun_frames} silent ticks)")
        print(f"  catch-up drops  : {s.dropped_catchup}")
        print(f"  decode errors   : {self.decode_errors}")


class _WavWriter:
    """Decodes incoming Opus frames into a 16 kHz mono WAV file.

    One persistent decoder keeps the codec state across frames, exactly as a
    realtime receiver would. Frames that fail to decode are counted and
    skipped (they would be packet loss on the air anyway).
    """

    def __init__(self, path: Path) -> None:
        try:
            import opuslib
        except ImportError as exc:
            raise ValueError(
                "saving decoded WAV needs opuslib: "
                f"pip install 'respeaker-clip-sdk[play]' (missing: {exc.name})"
            ) from exc
        import wave

        self.path = path
        self._decoder = opuslib.Decoder(_SAMPLE_RATE, 1)
        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)
        self._wave.setframerate(_SAMPLE_RATE)
        self.frames = 0
        self.decode_errors = 0

    def feed(self, payload: bytes) -> None:
        if not payload:
            # libopus treats an empty packet as loss and synthesizes comfort
            # noise; a missing frame should stay silent in the WAV instead.
            self.decode_errors += 1
            return
        try:
            pcm = self._decoder.decode(payload, _FRAME_SAMPLES)
        except Exception:
            self.decode_errors += 1
            return
        self._wave.writeframes(pcm)
        self.frames += 1

    def close(self) -> None:
        self._wave.close()

    @property
    def seconds(self) -> float:
        return self.frames * FRAME_MS / 1000.0


def _print_simulation(receiver) -> None:
    gaps = receiver.inter_frame_gaps_ms
    if not gaps:
        return
    print()
    print("Jitter buffer simulation (offline replay of this run's arrivals):")
    print(f"  {'depth':>7} | {'underruns':>9} | {'silent ticks':>12} | {'catch-up drops':>14}")
    for depth_ms in _SIM_DEPTHS_MS:
        s = simulate_playback(gaps, depth_ms)
        print(
            f"  {depth_ms:>5.0f} ms | {s.underruns:>9} | {s.underrun_frames:>12} | "
            f"{s.dropped_catchup:>14}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_options(parser)
    parser.add_argument("--out", help="output file (default rtc-<session>.bin)")
    parser.add_argument("--duration", type=float, help="stream this many seconds; default waits for Ctrl-C")
    parser.add_argument("--pause-at", type=float, help="send AT+PAUSE this many seconds into the stream")
    parser.add_argument("--resume-at", type=float, help="send AT+RESUME this many seconds into the stream")
    parser.add_argument("--play", action="store_true",
                        help="play live through the sound card via a jitter buffer")
    parser.add_argument("--buffer-ms", type=float, default=None,
                        help="jitter buffer depth in ms for --play (default 100; 0 = pass-through)")
    parser.add_argument("--simulate-playback", action="store_true",
                        help="after the run, replay the recorded arrivals through the jitter "
                             "buffer model and report underruns (no audio hardware needed)")
    parser.add_argument("--wav", nargs="?", const="", default=None, metavar="PATH",
                        help="also decode the stream to a 16 kHz mono WAV file "
                             "(default name rtc-<session>.wav)")
    parser.add_argument("--device",
                        help="output device for --play: index or name substring "
                             "(list with: python -m sounddevice)")
    return parser


async def _at_after(client, delay: float, method) -> None:
    await asyncio.sleep(delay)
    await method()


async def run(args: argparse.Namespace) -> int:
    if args.duration is not None and args.duration <= 0:
        raise ValueError("--duration must be positive")
    for name, value in (("--pause-at", args.pause_at), ("--resume-at", args.resume_at)):
        if value is not None and (value <= 0 or (args.duration is not None and value >= args.duration)):
            raise ValueError(f"{name} must be positive and smaller than --duration")
    if args.buffer_ms is not None and args.buffer_ms < 0:
        raise ValueError("--buffer-ms must be >= 0")

    player: _Player | None = None
    if args.play:
        buffer_ms = 100.0 if args.buffer_ms is None else args.buffer_ms
        player = _Player(buffer_ms, device=args.device)  # fails early with a clear message

    client = make_client(args)
    async with client:
        session = await client.start_rtc()
        out_path = Path(args.out) if args.out else Path(f"rtc-{session}.bin")
        handle = out_path.open("wb")
        wav_path = Path(args.wav) if args.wav else Path(f"rtc-{session}.wav")
        wav_writer: _WavWriter | None = _WavWriter(wav_path) if args.wav is not None else None

        def on_frame(frame: bytes) -> None:
            handle.write(struct.pack("<H", len(frame)) + frame)
            if wav_writer is not None:
                wav_writer.feed(frame)
            if player is not None:
                player.feed(frame)

        receiver = StreamReceiver(on_frame=on_frame)
        print(f"RTC session: {session}")
        try:
            await client.stream_rtc(session, receiver)
            await receiver.wait_start(timeout=10.0)
            mode = f", playing (buffer {player.jbuf.depth * FRAME_MS:.0f} ms)" if player else ""
            print(f"Streaming to {out_path}{mode} (Ctrl-C to stop).")
            side_tasks = []
            if args.pause_at is not None:
                side_tasks.append(asyncio.create_task(_at_after(client, args.pause_at, client.pause_recording)))
            if args.resume_at is not None:
                side_tasks.append(asyncio.create_task(_at_after(client, args.resume_at, client.resume_recording)))
            try:
                if args.duration is None:
                    await receiver.wait_end()
                else:
                    await asyncio.wait_for(receiver.wait_end(), timeout=args.duration)
            except asyncio.TimeoutError:
                pass
        except KeyboardInterrupt:
            print("Stopping...")
        finally:
            for task in side_tasks:
                task.cancel()
            try:
                if client.is_connected and not receiver.ended.is_set():
                    await client.stop_recording()
                    await receiver.wait_end(timeout=5.0)
            except (ClipError, asyncio.TimeoutError):
                pass
            client.transport.set_file_frame_handler(None)
            handle.close()
            if wav_writer is not None:
                wav_writer.close()
            if player is not None:
                player.close()
        _print_summary(receiver)
        if wav_writer is not None:
            print(f"WAV: {wav_path} ({wav_writer.seconds:.1f} s, "
                  f"decode errors: {wav_writer.decode_errors})")
        if player is not None:
            player.print_stats()
        if args.simulate_playback:
            _print_simulation(receiver)
    return 0


def main() -> int:
    try:
        return asyncio.run(run(build_parser().parse_args()))
    except Exception as exc:
        print(f"listen: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
