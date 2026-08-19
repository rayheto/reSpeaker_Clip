#!/usr/bin/env python3
"""Minimal real-device demo: capture + live playback of RTC audio.

Streams live RTC audio from a Clip over BLE and plays it through the sound
card in real time via the on_frame callback (path 1: lowest latency).

.. warning::
   The Clip has no echo cancellation. Playing through speakers feeds the
   microphone and howls — always use HEADPHONES.

Usage:
    python examples/demo_stream_playback.py --address C4:F1:79:A4:09:A0 [--duration 10]
    python examples/demo_stream_playback.py                 # scan for a device named "Clip"
"""

from __future__ import annotations

import argparse
import asyncio
import threading
import time
from collections import deque

from _bootstrap import SDK_ROOT  # noqa: F401

from clip import BleTransport, ClipClient
from clip.jitter import FRAME_MS, JitterBuffer
from clip.stream import StreamReceiver

try:
    import numpy as np
    import opuslib
    import sounddevice as sd
except ImportError as exc:
    raise SystemExit(
        f"Missing dependency: {exc.name}\n"
        "Install with: pip install 'respeaker-clip-sdk[play]'"
    ) from exc

_SAMPLE_RATE = 16000
_FRAME_SAMPLES = 320  # 20 ms at 16 kHz mono
# _FRAME_MS comes from clip.jitter.FRAME_MS (20.0)


class LivePlayer:
    """Jitter buffer + Opus decoder + sound card, fed from on_frame.

    Uses the SDK's :class:`clip.jitter.JitterBuffer` for the queue policy
    (initial fill, underrun, catch-up drop). A parallel timestamp deque
    tracks per-frame arrival time for latency measurement; it is kept in
    sync with the jitter buffer's catch-up drops.
    """

    def __init__(self, depth_frames: int = 5, device: str | None = None) -> None:
        self._jbuf = JitterBuffer(depth_frames)
        self._timestamps: deque[float] = deque()
        self._silence = np.zeros(_FRAME_SAMPLES, dtype=np.int16)
        self._decoder = opuslib.Decoder(_SAMPLE_RATE, 1)
        self._closed = False
        self.decode_errors = 0

        # Latency tracking: feed() stamps arrival; _run() measures segments.
        self._latencies: list[float] = []         # total feed→play (ms)
        self._queue_times: list[float] = []       # queue wait (ms)
        self._decode_times: list[float] = []      # Opus decode (ms)
        self._write_times: list[float] = []       # sd.write block (ms)

        # Queue depth tracking: snapshot JitterBuffer depth at each feed()
        # (producer) and _run() get() (consumer) to see the fill/drain pattern.
        self._depth_at_feed: list[int] = []       # depth after put()
        self._depth_at_get: list[int] = []         # depth before get()

        stream = sd.OutputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=_FRAME_SAMPLES,
            device=None if device is None else int(device) if device.isdigit() else device,
        )
        stream.start()
        self._stream = stream
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="clip-live-play", daemon=True)
        # Thread starts lazily on first feed() to avoid spinning silence
        # before BLE frames arrive (connection param negotiation can take
        # seconds — without this, start_wait_frames would be huge).
        self._thread_started = False
        self._lock = threading.Lock()

    def feed(self, opus_packet: bytes) -> None:
        """Called from on_frame (asyncio thread). Non-blocking."""
        if self._closed:
            return
        dropped_before = self._jbuf.stats.dropped_catchup
        self._jbuf.put(opus_packet)
        self._depth_at_feed.append(self._jbuf.buffered)
        # Keep timestamp deque in sync with jitter buffer's catch-up drops.
        while self._timestamps and self._jbuf.stats.dropped_catchup > dropped_before:
            self._timestamps.popleft()
            dropped_before += 1
        self._timestamps.append(time.perf_counter())
        # Lazy thread start: only once the first frame arrives.
        with self._lock:
            if not self._thread_started:
                self._thread_started = True
                self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._depth_at_get.append(self._jbuf.buffered)
            frame = self._jbuf.get()
            ts_arrival = self._timestamps.popleft() if (frame is not None and self._timestamps) else None

            if frame is None:
                samples = self._silence
            else:
                t_decode_start = time.perf_counter()
                try:
                    pcm = self._decoder.decode(frame, _FRAME_SAMPLES)
                    samples = np.frombuffer(pcm, dtype=np.int16)
                except Exception:
                    self.decode_errors += 1
                    samples = self._silence
                t_decode_end = time.perf_counter()

                if ts_arrival is not None:
                    self._queue_times.append(
                        (t_decode_start - ts_arrival) * 1000.0)
                    self._decode_times.append(
                        (t_decode_end - t_decode_start) * 1000.0)

            t_write_start = time.perf_counter()
            self._stream.write(samples)
            t_write_end = time.perf_counter()

            if frame is not None and ts_arrival is not None:
                self._write_times.append(
                    (t_write_end - t_write_start) * 1000.0)
                self._latencies.append(
                    (t_write_end - ts_arrival) * 1000.0)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._stream.stop()
        self._stream.close()

    def print_stats(self) -> None:
        s = self._jbuf.stats
        print()
        print("Live playback (SDK JitterBuffer + Opus decode + sound card):")
        print(f"  depth target    : {self._jbuf.depth} frames ({self._jbuf.depth * FRAME_MS:.0f} ms)")
        print(f"  frames in       : {s.frames_in}")
        print(f"  frames played   : {s.frames_out}")
        print(f"  start wait      : {s.start_wait_frames} silent ticks")
        print(f"  underruns       : {s.underruns} ({s.underrun_frames} silent ticks)")
        print(f"  catch-up drops  : {s.dropped_catchup}")
        print(f"  decode errors   : {self.decode_errors}")
        self._print_depth_stats()
        self._print_latency_stats()

    def _print_depth_stats(self) -> None:
        if not self._depth_at_feed and not self._depth_at_get:
            return
        df = sorted(self._depth_at_feed) if self._depth_at_feed else []
        dg = sorted(self._depth_at_get) if self._depth_at_get else []
        print()
        print("Queue depth distribution (JitterBuffer.buffered):")
        if df:
            n = len(df)
            print(f"  at feed  (n={n}): "
                  f"p50={self._pct_int(df,50)}  p95={self._pct_int(df,95)}  "
                  f"min={df[0]}  max={df[-1]}  mean={sum(df)/n:.1f}  (frames)")
        if dg:
            n = len(dg)
            print(f"  at get   (n={n}): "
                  f"p50={self._pct_int(dg,50)}  p95={self._pct_int(dg,95)}  "
                  f"min={dg[0]}  max={dg[-1]}  mean={sum(dg)/n:.1f}  (frames)")
            zero_pct = dg.count(0) * 100.0 / n if n else 0.0
            print(f"  depth=0 at get : {dg.count(0)} ({zero_pct:.1f}%) = underruns")

    @staticmethod
    def _pct_int(sorted_vals: list[int], pct: float) -> int:
        if not sorted_vals:
            return 0
        idx = min(int(len(sorted_vals) * pct / 100.0), len(sorted_vals) - 1)
        return sorted_vals[idx]

    @staticmethod
    def _pct(sorted_vals: list[float], pct: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(int(len(sorted_vals) * pct / 100.0), len(sorted_vals) - 1)
        return sorted_vals[idx]

    def _print_latency_stats(self) -> None:
        if not self._latencies:
            print("  (no latency samples)")
            return
        lat = sorted(self._latencies)
        qt = sorted(self._queue_times) if self._queue_times else []
        dt = sorted(self._decode_times) if self._decode_times else []
        wt = sorted(self._write_times) if self._write_times else []
        n = len(lat)
        print()
        print(f"Latency (on_frame arrival → sd.write return, {n} samples):")
        print(f"  total    : p50={self._pct(lat,50):.1f}  p95={self._pct(lat,95):.1f}  "
              f"min={lat[0]:.1f}  max={lat[-1]:.1f}  mean={sum(lat)/n:.1f}  (ms)")
        if qt:
            print(f"  queue    : p50={self._pct(qt,50):.1f}  p95={self._pct(qt,95):.1f}  "
                  f"min={qt[0]:.1f}  max={qt[-1]:.1f}  (ms)")
        if dt:
            print(f"  decode   : p50={self._pct(dt,50):.2f}  p95={self._pct(dt,95):.2f}  "
                  f"min={dt[0]:.2f}  max={dt[-1]:.2f}  (ms)")
        if wt:
            print(f"  sd.write : p50={self._pct(wt,50):.1f}  p95={self._pct(wt,95):.1f}  "
                  f"min={wt[0]:.1f}  max={wt[-1]:.1f}  (ms)")


async def run(address: str | None, duration: float | None,
              depth_frames: int, device: str | None) -> int:
    transport = BleTransport(address=address) if address else BleTransport(name="Clip")
    async with ClipClient(transport) as clip:
        session = await clip.start_rtc()
        player = LivePlayer(depth_frames, device)

        def on_frame(opus_packet: bytes) -> None:
            """Synchronous, inline on the receive path — keep non-blocking."""
            player.feed(opus_packet)

        receiver = StreamReceiver(on_frame=on_frame)

        token = await clip.stream_rtc(session, receiver)
        print(f"RTC session: {session}")
        try:
            await receiver.wait_start(timeout=10.0)
            if duration is None:
                print("Streaming until Ctrl-C ...")
                await receiver.wait_end()
            else:
                print(f"Streaming for {duration:g}s ...")
                try:
                    await asyncio.wait_for(receiver.wait_end(), timeout=duration)
                except asyncio.TimeoutError:
                    pass
        finally:
            # Bounded stop + lease release + player close, each guarded.
            try:
                if clip.is_connected and not receiver.ended.is_set():
                    await clip.stop_recording()
                    await receiver.wait_end(timeout=5.0)
            except Exception as exc:
                print(f"stop: {exc}")
            transport.detach_file_frame_handler(token)
            player.close()

        print()
        print(f"frames received : {receiver.frames_received}")
        print(f"bytes received  : {receiver.bytes_received}")
        print(f"seq discontin.  : {receiver.sequence_gaps}")
        gaps = receiver.inter_frame_gaps_ms
        if gaps:
            print(f"avg inter-frame : {receiver.avg_inter_frame_ms:.1f} ms "
                  f"(max {receiver.max_inter_frame_ms:.0f} ms)")
        player.print_stats()
        if receiver.error is not None:
            print(f"error           : {receiver.error}")
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="BLE address; omit to scan by name")
    parser.add_argument(
        "--duration",
        type=float,
        help="seconds to stream; default waits for Ctrl-C",
    )
    parser.add_argument(
        "--depth-frames", type=int, default=5,
        help="jitter buffer depth in frames (default 5; 0 = pass-through)",
    )
    parser.add_argument(
        "--device",
        help="audio output device: index or name substring "
             "(list with: python -m sounddevice)",
    )
    args = parser.parse_args()
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    if args.depth_frames < 0:
        parser.error("--depth-frames must be >= 0")
    try:
        return asyncio.run(run(args.address, args.duration,
                               args.depth_frames, args.device))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except Exception as exc:
        print(f"demo: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
