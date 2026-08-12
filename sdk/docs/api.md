# Public API

```python
from clip import BleTransport, ClipClient, UdpTransport
```

Construct one transport and pass it to `ClipClient`.  `ClipClient` is an async
context manager and does not perform implicit time synchronization or other
state-changing work during `connect()`.

```python
async with ClipClient(UdpTransport("192.168.4.1", 8089)) as clip:
    status = await clip.status()
    sessions = await clip.list_all_sessions()
```

The principal read APIs are `status`, `battery`, `storage`, `device_name`,
`firmware_version`, `get_time`, `list_sessions`, `list_all_sessions`,
`session_details`, `list_files`, and `list_bookmarks`.

The principal state-changing APIs are `set_time`, `set_mode`,
`set_auto_delete_days`, `set_brightness`, `set_name`, `start_recording`,
`stop_recording`, `pause_recording`, `resume_recording`, `bookmark`, `start_wifi`,
`stop_wifi`, `set_wifi_config`, and `set_usb_enabled`.

Destructive calls require an explicit local confirmation:

```python
await clip.delete_session("20260716022113", confirm=True)
await clip.format_storage(confirm=True)
await clip.reset_pairing(confirm=True)  # pairing reset also wipes SD recordings
await clip.factory_reset(confirm=True)
```

## Download

```python
result = await clip.download_session(
    "20260716022113",
    "recordings",
    start_file="0016.opus",  # optional resume point
)
```

Files are stored under `recordings/<session-id>/`.  A `session.json` metadata
file is written first.  Each incoming file writes to `NNNN.opus.part`; only a
matching declared length and `FILE_END` CRC32 atomically publish `NNNN.opus`.

## RTC streaming

```python
from clip.stream import StreamReceiver

receiver = StreamReceiver(on_frame=handle_frame)
session = await clip.start_rtc()                   # AT+START=rtc; nothing on SD
await clip.stream_rtc(session, receiver)           # AT+DOWNLOAD=<session>
await receiver.wait_start(timeout=10)
...
await clip.stop_recording()                        # ends the RTC stream
await receiver.wait_end(timeout=5)
clip.transport.set_file_frame_handler(None)        # detach the receiver
```

`start_rtc()` returns the RTC session id.  `stream_rtc()` returns once the
device acknowledges; STREAM_START/STREAM_DATA/STREAM_END frames then flow on
the file-data path until `STREAM_END`.  `StreamReceiver(on_frame)` hands every
Opus frame payload to the callback as it arrives and tracks `frames_received`,
`bytes_received`, and `sequence_gaps` (frames lost over the air), plus arrival
timing via `inter_frame_gaps_ms`, `avg_inter_frame_ms`, `max_inter_frame_ms`,
and `first_frame_delay_s`.  `end_reason` is one of `STREAM_END_STOPPED`,
`STREAM_END_TIMEOUT`, `STREAM_END_DISCONNECT` (all in `clip.stream`).

### Jitter buffer

`clip.jitter` decouples the bursty BLE arrivals from steady 20 ms playback
(`FRAME_MS`):

```python
from clip.jitter import FRAME_MS, JitterBuffer, simulate_playback

buf = JitterBuffer(depth_frames=5)   # ~100 ms of buffering
buf.put(frame)                       # producer: every arrival, any thread
frame = buf.get()                    # consumer: once per 20 ms tick; None = silence

stats = simulate_playback(receiver.inter_frame_gaps_ms, depth_ms=100)
```

`JitterBuffer(depth_frames, max_depth_frames=None)` policy: nothing is emitted
until `depth` frames are buffered (initial fill); `get()` returns `None` on
underrun; when the queue grows past `max_depth` the oldest frames are dropped
to snap back to `depth`, bounding latency to the live edge.  `depth_frames=0`
is pass-through.  `stats` is a `JitterStats` dataclass: `frames_in`,
`frames_out`, `start_wait_frames`, `underruns`, `underrun_frames`,
`dropped_catchup`.  `simulate_playback(inter_frame_gaps_ms, depth_ms,
frame_ms=FRAME_MS, max_depth_ms=None)` replays recorded arrival gaps through
the same model offline, for sizing the buffer from a real capture without
playing audio.

## CLI

```sh
clip-sdk --transport ble --address AA:BB:CC:DD:EE:FF status
clip-sdk --transport udp --host 192.168.4.1 sessions
clip-sdk --transport udp download 20260716022113 recordings
clip.terminal --transport udp
clip.sync --transport udp --all --output recordings
clip.record --transport ble --duration 60
clip.listen --transport ble --play --wav
clip.web --transport udp
```

`clip-sdk command` is available for development and accepts only a single
`AT+...` command string.  The installable `clip.terminal`, `clip.sync`,
`clip.record`, and `clip.listen` tools use the same package API. `clip.listen`
needs the `play` extra for `--play` and `--wav`; `clip.web` adds a local
browser panel when installed with the `web` extra; use the typed methods for
production code.
