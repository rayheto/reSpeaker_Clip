"""Stream live RTC audio from the Clip and save Opus frames to a file.

Each frame is written as a 2-byte little-endian length followed by the raw
Opus packet, so the capture can be decoded by any Opus decoder offline.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import struct
from pathlib import Path

from ..exceptions import ClipError
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_options(parser)
    parser.add_argument("--out", help="output file (default rtc-<session>.bin)")
    parser.add_argument("--duration", type=float, help="stream this many seconds; default waits for Ctrl-C")
    parser.add_argument("--pause-at", type=float, help="send AT+PAUSE this many seconds into the stream")
    parser.add_argument("--resume-at", type=float, help="send AT+RESUME this many seconds into the stream")
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
    client = make_client(args)
    async with client:
        session = await client.start_rtc()
        out_path = Path(args.out) if args.out else Path(f"rtc-{session}.bin")
        handle = out_path.open("wb")
        receiver = StreamReceiver(
            on_frame=lambda frame: handle.write(struct.pack("<H", len(frame)) + frame)
        )
        print(f"RTC session: {session}")
        try:
            await client.stream_rtc(session, receiver)
            await receiver.wait_start(timeout=10.0)
            print(f"Streaming to {out_path} (Ctrl-C to stop).")
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
        _print_summary(receiver)
    return 0


def main() -> int:
    try:
        return asyncio.run(run(build_parser().parse_args()))
    except Exception as exc:
        print(f"listen: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
