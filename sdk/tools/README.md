# SDK tools

These tools are new and independent of `applications/clip/tests/tools`.
Their implementation lives in the installed `clip.tools` package.  The scripts
in this directory are only development wrappers for the same implementation.
Install the SDK to register the commands on `PATH`.

```sh
cd /home/lht/clip/sdk
python -m pip install -e '.[ble]'
```

| Installed command | Purpose |
|---|---|
| `clip.terminal` | Interactive current-firmware AT terminal over BLE or Wi-Fi UDP |
| `clip.sync` | Download newest, selected, or all sessions; optional post-success delete |
| `clip.record` | Start/stop a recording with optional duration and periodic bookmarks |
| `clip.listen` | Live RTC audio streaming over BLE; `.bin` capture, optional playback, WAV, jitter simulation |
| `clip.web` | Local browser control panel; requires the `web` extra |
| `clip.wifi` | BLE-to-Wi-Fi handoff using the host platform's native Wi-Fi tool |

Examples:

```sh
clip.terminal --transport ble --address AA:BB:CC:DD:EE:FF
clip.terminal --transport udp
clip.sync --transport udp --all --output recordings
clip.record --transport ble --mode enhanced --duration 60
clip.listen --transport ble --address AA:BB:CC:DD:EE:FF --duration 30
clip.web --transport udp
clip.wifi --address AA:BB:CC:DD:EE:FF
```

During SDK development, `python tools/terminal.py` (and the matching `sync.py`
and `record.py` wrappers) run the exact same installed implementation.  The UDP
default is the actual firmware port `8089`, not the old tool's `8080`.

For the web panel install its extra and browse to the printed loopback URL:

```sh
python -m pip install -e '.[web]'       # Wi-Fi/UDP
python -m pip install -e '.[web,ble]'   # BLE
clip.web --transport ble
```

The server deliberately defaults to `127.0.0.1`. If you expose it on a LAN with
`--bind`, put it behind suitable network access control because it can control
recording and delete sessions.

For `clip.listen` live playback and WAV export install the `play` extra; the
`.bin` capture and `--simulate-playback` work without it:

```sh
python -m pip install -e '.[play,ble]'
clip.listen --transport ble --play --buffer-ms 200 --device 3 --wav
clip.listen --transport ble --duration 30 --simulate-playback
```

`--play` paces playback through a jitter buffer (`--buffer-ms` depth,
default 100 ms, `0` = pass-through; `--device` is an index or name substring,
listed by `python -m sounddevice`). `--wav [PATH]` decodes to a 16 kHz mono
WAV file (`rtc-<session>.wav` by default). `--simulate-playback` replays the
run's arrival times through the jitter-buffer model and prints an underrun
table at several depths, no audio hardware needed.

When `clip.web` starts with `--transport ble`, its **Switch transfer to Wi-Fi**
button starts the device AP over BLE, automatically joins the host to it, and
switches the web server's connection to UDP. The backend selects `nmcli` on
Linux, `networksetup` on macOS, or `netsh` on Windows. The browser only asks the
local Python server to perform this operation; it never receives the Wi-Fi
password or attempts to change the operating-system network itself.
