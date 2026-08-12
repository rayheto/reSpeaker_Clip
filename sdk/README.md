# reSpeaker Clip SDK

The new Python SDK for the current reSpeaker Clip firmware.  It is intentionally
independent from `applications/clip/tests`: that directory remains the legacy
test/tool collection and is neither imported nor modified by this package.

The SDK provides:

- an asynchronous, typed AT-command API;
- BLE transport (`pip install -e '.[ble]'`) and dependency-free Wi-Fi/UDP transport;
- sequential command dispatch, so concurrent callers cannot consume one another's
  command responses;
- streaming file download to `*.part` files, with length and CRC32 checks before an
  atomic rename;
- live RTC audio streaming (`AT+START=rtc`), with sequence-gap accounting; and
- an intentionally small CLI for common inspection and download tasks.

## Install for development

```sh
cd /home/lht/clip/sdk
python -m pip install -e '.[dev,ble]'
pytest
```

BLE is optional.  Wi-Fi/UDP needs only the Python standard library.

Installation registers these commands on `PATH`:

```sh
clip.terminal --transport ble --address AA:BB:CC:DD:EE:FF
clip.sync --transport udp --all --output recordings
clip.record --transport ble --mode enhanced --duration 60
clip.listen --transport ble --address AA:BB:CC:DD:EE:FF
clip.web --transport udp
```

`clip-sdk` remains the compact JSON-oriented CLI; the `clip.*` commands are the
interactive and workflow-oriented tools. `clip.web` is always registered but
requires the `web` extra when run: `pip install -e '.[web]'` (or `.[web,ble]`
when it controls a BLE device).

## RTC live streaming

`AT+START=rtc` runs the microphone pipeline without touching the SD card;
`AT+DOWNLOAD=<session>` then streams live Opus frames over BLE:

```sh
clip.listen --transport ble --address AA:BB:CC:DD:EE:FF --duration 30
```

Frames are written as 2-byte little-endian length + raw Opus packet
(`rtc-<session>.bin` by default), ready for any Opus decoder. `Ctrl-C` sends
`AT+STOP`.

Live playback and WAV export need the `play` extra (`pip install -e '.[play]'`
or `.[play,ble]`):

```sh
clip.listen --transport ble --address AA:BB:CC:DD:EE:FF --play
clip.listen --transport ble --play --buffer-ms 200 --device 3 --wav
clip.listen --transport ble --duration 30 --simulate-playback
```

`--play` decodes the stream and plays it through the sound card, paced by a
jitter buffer that smooths the bursty BLE arrivals (`--buffer-ms` sets the
depth, default 100 ms, `0` = pass-through; `--device` selects the output by
index or name substring — list devices with `python -m sounddevice`).
`--wav [PATH]` additionally decodes the stream to a 16 kHz mono WAV file
(`rtc-<session>.wav` by default). `--simulate-playback` needs no audio
hardware: after the run it replays the recorded arrival times through the
jitter-buffer model and prints underruns at several depths.

The Python API mirrors the CLI:

```python
from clip.stream import StreamReceiver

receiver = StreamReceiver(on_frame=print)
session = await clip.start_rtc()
await clip.stream_rtc(session, receiver)
await receiver.wait_start(timeout=10)
...
await clip.stop_recording()          # ends the RTC stream
await receiver.wait_end(timeout=5)
```

## Quick start

```python
import asyncio
from pathlib import Path

from clip import ClipClient, BleTransport


async def main() -> None:
    async with ClipClient(BleTransport(name="Clip")) as clip:
        print(await clip.status())
        sessions = await clip.list_all_sessions()
        if sessions:
            result = await clip.download_session(sessions[0].id, Path("recordings"))
            print(result.files)


asyncio.run(main())
```

For Wi-Fi after enabling the Clip AP:

```python
from clip import ClipClient, UdpTransport

async with ClipClient(UdpTransport()) as clip:
    print(await clip.storage())
```

More detail is in [docs/architecture.md](docs/architecture.md) and
[docs/api.md](docs/api.md).  Direct-run utilities are in
[tools/README.md](tools/README.md).
