"""Stream live RTC audio from the Clip and save Opus frames to a file.

Each frame is written as a 2-byte little-endian length followed by the raw
Opus packet, so the capture can be decoded by any Opus decoder offline.
"""

from __future__ import annotations

import argparse
import asyncio
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_options(parser)
    parser.add_argument("--out", help="output file (default rtc-<session>.bin)")
    parser.add_argument("--duration", type=float, help="stream this many seconds; default waits for Ctrl-C")
    return parser


async def run(args: argparse.Namespace) -> int:
    if args.duration is not None and args.duration <= 0:
        raise ValueError("--duration must be positive")
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
            try:
                if client.is_connected and not receiver.ended.is_set():
                    await client.stop_recording()
                    await receiver.wait_end(timeout=5.0)
            except (ClipError, asyncio.TimeoutError):
                pass
            client.transport.set_file_frame_handler(None)
            handle.close()
        reason = _END_REASONS.get(receiver.end_reason, str(receiver.end_reason))
        print(
            f"Ended: reason={reason} frames={receiver.frames_received} "
            f"bytes={receiver.bytes_received} seq_gaps={receiver.sequence_gaps}"
        )
    return 0


def main() -> int:
    try:
        return asyncio.run(run(build_parser().parse_args()))
    except Exception as exc:
        print(f"listen: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
