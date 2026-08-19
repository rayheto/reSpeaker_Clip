# SDK examples

These examples are standalone, real-device demos built on the installed
`clip` package.  They are intentionally minimal — each one shows one
producer/consumer pattern against a live Clip over BLE, with no CLI wrapper
or extra features.  The scripts in this directory run before the package is
installed thanks to `_bootstrap.py`, which inserts `sdk/src` onto
`sys.path`; install the SDK (or the relevant extra) only for the runtime
dependencies.

| Example | Purpose |
|---|---|
| `demo_stream.py` | Stream live RTC audio over BLE and exercise all three data paths at once: `.bin` packet capture, live-edge async consumer, and receiver stats |
| `demo_stream_playback.py` | Capture + real-time headphone playback of an RTC stream through the SDK `JitterBuffer` + Opus decoder + sound card; requires the `play` extra |

Both examples run until `Ctrl-C` when `--duration` is omitted.  Omitting
`--address` scans for a BLE device named "Clip" and uses the first match.

## `demo_stream.py` — producer/consumer demo

Install the BLE extra:

```sh
cd /path/to/reSpeaker_Clip/sdk
python -m pip install -e '.[ble]'
```

Examples:

```sh
python examples/demo_stream.py --address AA:BB:CC:DD:EE:FF  # replace with your device address
python examples/demo_stream.py --address AA:BB:CC:DD:EE:FF --duration 10  
python examples/demo_stream.py                 # scan for a device named "Clip"
```

This is the standalone producer/consumer counterpart of `clip.stream`.  It
wires a `StreamReceiver` to two sinks at once:

* `StreamCapture` — raw arrivals → `rtc-<session>.bin.part`, atomically
  renamed to `rtc-<session>.bin` on a normal end
* `StreamConsumer` — live-edge async push, counting chunks and stacks

At the end it prints receiver stats — frames, bytes, sequence
discontinuities, and inter-frame gap averages — plus the consumer and
capture counts.

## `demo_stream_playback.py` — live playback demo

Install the `play` extra first:

```sh
cd /path/to/reSpeaker_Clip/sdk
python -m pip install -e '.[play,ble]'
```

Examples:

```sh
python examples/demo_stream_playback.py --address AA:BB:CC:DD:EE:FF  # replace with your device address
python examples/demo_stream_playback.py --duration 10 --depth-frames 5 --device 3
```

> **Playback requires headphones:** the Clip has no echo cancellation, so
> speaker output feeds the microphone and howls.  Always use HEADPHONES.

This is the lowest-latency path: the `on_frame` callback feeds Opus packets
straight into a `LivePlayer` (SDK `JitterBuffer` → Opus decode →
`sounddevice` output) on a dedicated thread.  At the end it prints
jitter-buffer stats (underruns, catch-up drops, start wait), a queue-depth
distribution sampled at both `feed()` and `get()`, and a full latency
breakdown (queue wait / decode / `sd.write` / total, p50/p95/min/max/mean).

It accepts `--depth-frames` (jitter buffer depth in frames, default 5;
`0` = pass-through) and `--device` (an index or name substring, listed by
`python -m sounddevice`).  The player thread starts lazily on the first
frame to avoid spinning silence during BLE connection parameter negotiation.
