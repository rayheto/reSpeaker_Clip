#!/usr/bin/env python3
"""Minimal real-device demo of clip.stream's producer/consumer paths.

Streams live RTC audio from a Clip over BLE and shows all
three data paths at once:

* ``StreamCapture``  -> complete received-packet log (``rtc-<session>.bin.part``
  while running, renamed on a normal end)
* ``StreamConsumer`` -> live-edge async push, counting chunks and stacks
* receiver stats     -> frames, bytes, discontinuity events, arrival gaps

Usage:
    python examples/demo_stream.py --address C4:F1:79:A4:09:A0 [--duration 10]
    python examples/demo_stream.py                 # scan for a device named "Clip"
"""

from __future__ import annotations

import argparse
import asyncio

from _bootstrap import SDK_ROOT  # noqa: F401

from clip import BleTransport, ClipClient
from clip.stream import STREAM_END_STOPPED, StreamCapture, StreamConsumer, StreamReceiver


async def run(address: str | None, duration: float | None) -> int:
    transport = BleTransport(address=address) if address else BleTransport(name="Clip")
    async with ClipClient(transport) as clip:
        receiver = StreamReceiver()
        counters = {"chunks": 0, "stacks": 0}

        async def on_chunk(chunk: bytes) -> None:
            counters["chunks"] += 1

        async def on_stack(stack: list[bytes]) -> None:
            counters["stacks"] += 1

        consumer = StreamConsumer(on_chunk=on_chunk, on_stack=on_stack)

        session = await clip.start_rtc()
        capture = StreamCapture(f"rtc-{session}.bin")
        receiver.add_sink(capture.feed)     # raw arrivals -> .bin log
        receiver.add_sink(consumer.feed)    # live-edge chunk/stack push

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
            # Bounded stop + lease release + sink shutdown, each guarded.
            try:
                if clip.is_connected and not receiver.ended.is_set():
                    await clip.stop_recording()
                    await receiver.wait_end(timeout=5.0)
            except Exception as exc:
                print(f"stop: {exc}")
            transport.detach_file_frame_handler(token)
            normal = receiver.error is None and receiver.end_reason == STREAM_END_STOPPED
            capture.finish(normal)
            await consumer.wait_closed(normal_end=normal)

        print()
        print(f"frames received : {receiver.frames_received}")
        print(f"bytes received  : {receiver.bytes_received}")
        print(f"seq discontin.  : {receiver.sequence_gaps}")
        gaps = receiver.inter_frame_gaps_ms
        if gaps:
            print(f"avg inter-frame : {receiver.avg_inter_frame_ms:.1f} ms "
                  f"(max {receiver.max_inter_frame_ms:.0f} ms)")
        print(f"consumer chunks : {counters['chunks']} (stacks: {counters['stacks']})")
        print(f"capture         : {capture.path if capture.complete else capture.part_path} "
              f"({'complete' if capture.complete else 'PARTIAL'}, {capture.frames} frames)")
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
    args = parser.parse_args()
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    try:
        return asyncio.run(run(args.address, args.duration))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except Exception as exc:
        print(f"demo: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
