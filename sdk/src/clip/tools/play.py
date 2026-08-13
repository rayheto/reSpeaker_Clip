"""EXAMPLE: play live RTC audio from the Clip through the sound card.

.. warning::
   The Clip has no echo cancellation. Playing through speakers feeds the
   microphone and howls — always use HEADPHONES when running this example.

Starts an RTC session, plays the stream live through a jitter buffer, keeps
the received-packet ``.bin`` capture of the same session, and optionally
decodes to WAV (``--wav``) or simulates the jitter buffer offline after the
run (``--simulate-playback``).

Streaming itself (capture + diagnostics without playback) is the job of the
separate ``clip.stream`` tool.

Needs the play extra: ``pip install 'respeaker-clip-sdk[play]'``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
from pathlib import Path

from ..jitter import FRAME_MS, JitterBuffer, simulate_playback
from ..stream import (
    STREAM_END_STOPPED,
    StreamCapture,
    StreamReceiver,
)
from .common import add_connection_options, make_client
from .stream import _print_summary

_SAMPLE_RATE = 16000
_FRAME_SAMPLES = 320  # 20 ms at 16 kHz mono
_SIM_DEPTHS_MS = (0.0, 50.0, 100.0, 200.0)


class _Player:
    """EXAMPLE live playback: jitter buffer + Opus decode + sound card.

    Reference implementation only: the Clip has no echo cancellation, so
    playing through speakers feeds the microphone and howls. Always use
    HEADPHONES when demoing playback.

    The constructor is internally exception-safe: if any acquisition step
    fails, everything opened before it is closed before the error propagates.

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
        self._closed = False
        stream = None
        try:
            stream = sd.OutputStream(
                samplerate=_SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=_FRAME_SAMPLES,
                device=None if device is None else int(device) if device.isdigit() else device,
            )
            stream.start()
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 - constructor cleanup
                    pass
            raise ValueError(f"cannot open audio device {device!r}: {exc}") from exc
        self._stream = stream
        self._stop = threading.Event()
        self.decode_errors = 0
        self._thread = threading.Thread(target=self._run, name="clip-play", daemon=True)
        try:
            self._thread.start()
        except Exception:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001 - constructor cleanup
                pass
            raise

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
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._stream.stop()
        self._stream.close()

    def print_stats(self) -> None:
        s = self.jbuf.stats
        print()
        print("Playback (jitter buffer, EXAMPLE — use headphones, no AEC):")
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
        self._closed = False

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
        if self._closed:
            return
        self._closed = True
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
    parser.add_argument("--out", help="capture file (default rtc-<session>.bin)")
    parser.add_argument("--duration", type=float, help="stream this many seconds; default waits for Ctrl-C")
    parser.add_argument("--pause-at", type=float, help="send AT+PAUSE this many seconds into the stream")
    parser.add_argument("--resume-at", type=float, help="send AT+RESUME this many seconds into the stream")
    parser.add_argument("--buffer-ms", type=float, default=None,
                        help="jitter buffer depth in ms (default 100; 0 = pass-through)")
    parser.add_argument("--device",
                        help="output device: index or name substring "
                             "(list with: python -m sounddevice)")
    parser.add_argument("--wav", nargs="?", const="", default=None, metavar="PATH",
                        help="also decode the stream to a 16 kHz mono WAV file "
                             "(default name rtc-<session>.wav)")
    parser.add_argument("--simulate-playback", action="store_true",
                        help="after the run, replay the recorded arrivals through the jitter "
                             "buffer model and report underruns (supplements live playback)")
    return parser


async def _at_after(client, delay: float, method) -> None:
    await asyncio.sleep(delay)
    await method()


async def run(args: argparse.Namespace) -> int:
    # RTC streaming is BLE-only; reject before any resource is acquired,
    # dependency imported, or connection opened.
    if args.transport != "ble":
        raise ValueError(
            "clip.play requires --transport ble: RTC live streaming is BLE-only"
        )
    if args.duration is not None and args.duration <= 0:
        raise ValueError("--duration must be positive")
    for name, value in (("--pause-at", args.pause_at), ("--resume-at", args.resume_at)):
        if value is not None and (value <= 0 or (args.duration is not None and value >= args.duration)):
            raise ValueError(f"{name} must be positive and smaller than --duration")
    if args.buffer_ms is not None and args.buffer_ms < 0:
        raise ValueError("--buffer-ms must be >= 0")

    buffer_ms = 100.0 if args.buffer_ms is None else args.buffer_ms
    receiver = StreamReceiver()
    capture: StreamCapture | None = None
    wav_writer: _WavWriter | None = None
    player: _Player | None = None
    side_tasks: list[asyncio.Task] = []
    token: int | None = None
    cleanup_errors: list[Exception] = []
    primary: BaseException | None = None
    interrupted = False
    client = make_client(args)

    try:
        await client.connect()
        try:
            session = await client.start_rtc()
            out_path = Path(args.out) if args.out else Path(f"rtc-{session}.bin")
            wav_path = Path(args.wav) if args.wav else Path(f"rtc-{session}.wav")
            # Acquire optional outputs before opening the capture file so a
            # failure here cannot leak an open handle.
            if args.wav is not None:
                wav_writer = _WavWriter(wav_path)
            player = _Player(buffer_ms, device=args.device)
            capture = StreamCapture(out_path)
            receiver.add_sink(capture.feed)
            if wav_writer is not None:
                receiver.add_sink(wav_writer.feed)
            receiver.add_sink(player.feed)
            token = await client.stream_rtc(session, receiver)
            print(f"RTC session: {session}")
            await receiver.wait_start(timeout=10.0)
            print(f"Streaming to {out_path}, playing (buffer {player.jbuf.depth * FRAME_MS:.0f} ms) "
                  f"(Ctrl-C to stop).")
            if args.pause_at is not None:
                side_tasks.append(asyncio.create_task(_at_after(client, args.pause_at, client.pause_recording)))
            if args.resume_at is not None:
                side_tasks.append(asyncio.create_task(_at_after(client, args.resume_at, client.resume_recording)))

            end_wait = (
                receiver.wait_end()
                if args.duration is None
                else asyncio.wait_for(receiver.wait_end(), timeout=args.duration)
            )
            end_task = asyncio.ensure_future(end_wait)
            remaining = {end_task, *side_tasks}
            while remaining:
                done, remaining = await asyncio.wait(remaining, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task is not end_task and not task.cancelled():
                        exc = task.exception()
                        if exc is not None:
                            raise exc
                if end_task in done:
                    remaining = set()  # stream over: stop watching
                    if not end_task.cancelled():
                        exc = end_task.exception()
                        if exc is not None and not isinstance(exc, asyncio.TimeoutError):
                            raise exc
        except KeyboardInterrupt:
            interrupted = True
            print("Stopping...")
        except BaseException as exc:  # noqa: BLE001 - captured, cleanup runs first
            primary = exc
        finally:
            for task in side_tasks:
                task.cancel()
            for task in side_tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 - cleanup path
                    if primary is None and not interrupted:
                        primary = exc
                    else:
                        cleanup_errors.append(exc)
            try:
                if client.is_connected and not receiver.ended.is_set():
                    await client.stop_recording()
                    await receiver.wait_end(timeout=5.0)
            except Exception as exc:  # noqa: BLE001 - cleanup path
                cleanup_errors.append(exc)
            try:
                if token is not None:
                    client.transport.detach_file_frame_handler(token)
            except Exception as exc:  # noqa: BLE001 - cleanup path
                cleanup_errors.append(exc)
            normal = receiver.error is None and receiver.end_reason == STREAM_END_STOPPED
            if capture is not None:
                try:
                    capture.finish(normal)
                except Exception as exc:  # noqa: BLE001 - cleanup path
                    cleanup_errors.append(exc)
            if wav_writer is not None:
                try:
                    wav_writer.close()
                except Exception as exc:  # noqa: BLE001 - cleanup path
                    cleanup_errors.append(exc)
            if player is not None:
                try:
                    player.close()
                except Exception as exc:  # noqa: BLE001 - cleanup path
                    cleanup_errors.append(exc)
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001 - cleanup path
                cleanup_errors.append(exc)
    except KeyboardInterrupt:
        interrupted = True
        print("Stopping...")
    except BaseException as exc:  # noqa: BLE001 - connect failed or cleanup re-raised
        if primary is None and not interrupted:
            primary = exc
        else:
            if isinstance(exc, Exception):
                cleanup_errors.append(exc)

    if primary is not None:
        for extra in cleanup_errors:
            print(f"play: cleanup failure: {extra}", file=sys.stderr)
        raise primary
    if cleanup_errors:
        # No primary error: the first cleanup failure IS the failure.
        for extra in cleanup_errors[1:]:
            print(f"play: additional cleanup failure: {extra}", file=sys.stderr)
        raise cleanup_errors[0]

    if not interrupted or receiver.started.is_set():
        _print_summary(receiver)
    if capture is not None:
        if capture.complete:
            print(f"Capture: {capture.path} ({capture.frames} frames)")
        else:
            print(f"Capture PARTIAL (stream did not end normally): {capture.part_path}")
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
        print(f"play: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
