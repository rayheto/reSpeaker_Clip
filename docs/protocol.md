# reSpeaker Clip - BLE AT Protocol Specification

## 1. Protocol Overview

### 1.1 Design Principles

The reSpeaker Clip uses a JSON-based AT command protocol for communication between the mobile application and the device. All commands follow the Hayes AT command standard with a unified JSON response format.

**Key Design Principles:**
- **Human-readable**: JSON format for easy debugging and parsing
- **Extensible**: Easy to add new commands without breaking compatibility
- **Non-blocking**: File transfer allows concurrent command processing
- **Robust**: Comprehensive error handling and recovery
- **Efficient**: Binary data transfer over separate characteristic

### 1.2 Transport Layer (BLE GATT)

**Service UUID**: `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`

The protocol uses Bluetooth Low Energy with GATT (Generic Attribute Profile) as the transport layer. Three characteristics are provided:
1. **Command Receive** (Write): App sends AT commands to device
2. **Response Send** (Notify): Device sends JSON responses and progress updates
3. **File Data** (Notify): Device streams binary file data

### 1.3 Command Syntax

Three command types are supported:

| Type | Format | Example | Description |
|------|--------|---------|-------------|
| EXEC | `AT+XX` | `AT+GSTAT` | Execute operation without parameters |
| SET | `AT+XX=<value>` | `AT+MODE=enhanced` | Set parameter value |
| GET | `AT+XX?` | `AT+MODE?` | Query current parameter value |

### 1.4 Response Format

All responses use unified JSON format:

**Generic Response Schema:**
```json
{
  "ok": true,
  "data": { ... }
}
```

> The `"data"` value is inserted raw by the response builder, so its shape is
> command-specific (an object, a string, or omitted). Human-readable messages
> (both informational and errors) always use a `"msg"` key, never `"error"`.

**Success Response:**
```json
{
  "ok": true,
  "data": { ... }
}
```

**Error Response:**
```json
{
  "ok": false,
  "msg": "Error message"
}
```

### 1.5 Error Handling

All errors return JSON with `"ok": false` and a `"msg"` field containing a
descriptive message (e.g. `{"ok":false,"msg":"SD card not mounted"}`). There is
**no numeric error code** — handlers return only the message string. A few
commands also return an informational message on success via `"msg"` (e.g.
`AT+FACTORY`, `AT+NAME` clear).

## 2. BLE GATT Service Definition

### 2.1 Service UUID: 6E400001-B5A3-F393-E0A9-E50E24DCCA9E

This custom UUID defines the reSpeaker Clip communication service.

### 2.2 Characteristics

#### 2.2.1 Command Receive (Write)

**UUID**: `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
**Properties**: Write
**Max Length**: 512 bytes
**Purpose**: Receive AT commands from mobile app

The app writes AT command strings to this characteristic. Each write is processed as a complete command.

#### 2.2.2 Response Send (Notify)

**UUID**: `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`
**Properties**: Notify
**Max Length**: MTU - 3 (typically 244 bytes for MTU 247)
**Purpose**: Send JSON responses and progress notifications

The device sends:
- Command responses (success/error)
- Progress updates during file transfer
- Unsolicited event notifications

#### 2.2.3 File Data (Notify)

**UUID**: `6E400004-B5A3-F393-E0A9-E50E24DCCA9E`
**Properties**: Notify
**Max Length**: MTU - 3 (typically 244 bytes for MTU 247)
**Purpose**: Stream binary frame protocol during file transfer

Binary frames are sent through this characteristic. Each notification contains one frame, identified by the first byte (frame type). See Section 4 for the complete binary frame protocol specification.

Frame types sent on this characteristic:
- `0x01` DATA — file data chunk
- `0x10` FILE_START — begin file transfer
- `0x11` FILE_END — end file (with CRC32)
- `0x12` TRANSFER_DONE — all files complete
- `0x13` STREAM_START — begin RTC live stream
- `0x14` STREAM_DATA — RTC live Opus frame
- `0x15` STREAM_END — end RTC live stream

#### 2.2.4 Audio Visualization (Notify)

**UUID**: `6E400005-B5A3-F393-E0A9-E50E24DCCA9E`
**Properties**: Notify
**Max Length**: MTU - 3 (typically 244 bytes for MTU 247)
**Purpose**: Real-time audio energy visualization data

Sends 7 bytes of packed audio energy levels when recording is active.

**Data Format (7 bytes):**
```
[byte0] [byte1] [byte2] [byte3] [byte4] [byte5] [byte6]
  H L     H L     H L     H L     H L     H L     H _
```

- Each byte contains two 4-bit nibble values (High nibble = even index, Low nibble = odd index)
- 6 bytes × 2 values + 1 byte × 1 value = **13 audio energy values**
- Each value range: 0–10 (energy level per frequency band)
- Update rate: ~100 ms when recording is active
- Idle: no notifications sent

**Decoding example (Python):**
```python
values = []
for i in range(6):
    values.append(data[i] >> 4)       # high nibble
    values.append(data[i] & 0x0F)     # low nibble
values.append(data[6] & 0x0F)         # 13th value (low nibble of last byte)
```

### 2.3 Connection Requirements

| Requirement | Specification |
|-------------|---------------|
| Pairing | LE Secure Connections (mandatory) |
| Bonding | Required (stored for auto-reconnect) |
| Encryption | AES-128 CCM (mandatory) |
| MTU | Negotiated up to 517 (default 23) |
| Connection Interval | 15-80 ms (adaptive) |

### 2.4 MTU Negotiation

The device should negotiate MTU to optimal size:
- **Default MTU**: 23 bytes (BLE specification)
- **Maximum MTU**: 517 bytes (nRF5340 support)
- **Recommended MTU**: 247 bytes (optimal for throughput)

Larger MTU = fewer notifications = higher throughput.

## 3. Command Protocol

### 3.1 Command Types (EXEC, SET, GET)

#### EXEC Commands (No Parameters)
Format: `AT+XX`

Execute an operation or retrieve status:
- `AT+GSTAT` - Get device status
- `AT+DEVICE` - Get device name
- `AT+VERSION` - Get version info
- `AT+START` - Start recording (uses current mode)
- `AT+STOP` - Stop recording
- `AT+MARK` - Add bookmark
- `AT+PAUSE` - Pause recording
- `AT+RESUME` - Resume recording
- `AT+CANCEL` - Cancel transfer
- `AT+LIST` - List sessions/files
- `AT+MARKS` - Get session bookmarks
- `AT+DOWNLOAD` - Download file
- `AT+DELETE` - Delete session
- `AT+FORMAT` - Format SD card
- `AT+POWEROFF` - Power off device
- `AT+FACTORY` - Factory reset
- `AT+REBOOT` - Reboot device
- `AT+DFU` - Reboot into MCUboot DFU/recovery mode
- `AT+WIFI` - Start WiFi AP (equivalent to `AT+WIFI=on`)
- `AT+USB` - Enable USB CDC+MSC

#### SET Commands (With Parameters)
Format: `AT+XX=<value>`

Set configuration or execute with parameters:
- `AT+MODE=<normal|enhanced|stereo|merge>` - Set recording mode
- `AT+AUTODEL=<off|0|1-30>` - Set auto-delete policy
- `AT+BRIGHTNESS=<0-255>` - Set OLED brightness
- `AT+TIME=<unix_ts>` - Set system time
- `AT+PAIR=<reset>` - Reset BLE pairing
- `AT+FACTORY=<confirm>` - Factory reset
- `AT+START=<mode>` - Start recording with mode override
- `AT+MARK=<note>` - Add bookmark with note
- `AT+DELETE=<session>` - Delete session
- `AT+LIST=<session>` - List session details
- `AT+MARKS=<session>` - Get session bookmarks
- `AT+DOWNLOAD=<session/file>` - Download file
- `AT+WIFI=<on|off>` - Start/stop WiFi AP
- `AT+USB=<on|off>` - Enable/disable USB CDC+MSC
- `AT+LOG=<off|info|debug>` - Set SD log backend level

#### GET Commands (Query)
Format: `AT+XX?`

Query current configuration:
- `AT+DEVICE?` - Get device name
- `AT+NAME?` - Get user-defined device name
- `AT+MODE?` - Get current mode
- `AT+AUTODEL?` - Get auto-delete policy
- `AT+BRIGHTNESS?` - Get OLED brightness
- `AT+TIME?` - Get current time
- `AT+PAIR?` - Get pairing status
- `AT+WIFI?` - Get WiFi AP status
- `AT+USB?` - Get USB status
- `AT+LOG?` - Get SD log backend status

### 3.2 JSON Message Format

All responses use JSON with consistent structure:

**Success with data:**
```json
{
  "ok": true,
  "data": {
    "key": "value"
  }
}
```

**Success without data:**
```json
{
  "ok": true
}
```

**Error:**
```json
{
  "ok": false,
  "error": "Error message description"
}
```

**Note:** File transfer progress is communicated via binary frames on the File Data characteristic (see Section 4), not as JSON responses.

### 3.3 Command Reference

#### 3.3.1 Status Commands

##### AT+GSTAT - Get Device Status

Get current device state and information.

**Request:**
```
AT+GSTAT
```

**Response:**
```json
{
  "ok": true,
  "data": {
    "state": "IDLE",
    "recording": false,
    "session": null,
    "duration": 0,
    "battery": 85,
    "charging": true,
    "mode": "normal",
    "bitrate": 16000,
    "free_space": 1024,
    "device": "Clip"
  }
}
```

**Fields:**
- `state`: Current device state (IDLE/RECORDING/TRANSMITTING/WIFI_SYNC/PAUSED/ERROR)
- `recording`: Whether actively recording (true/false)
- `session`: Current session ID or null
- `duration`: Current recording duration in seconds
- `battery`: Battery percentage (0-100)
- `charging`: Charging status (true/false)
- `mode`: Recording mode (normal/enhanced)
- `bitrate`: Bitrate for current mode (normal=16000, enhanced=32000)
- `free_space`: Free space in MB
- `device`: Device name string

**Error Cases:**
- Never fails (always returns current state)

---

##### AT+TIME - System Time

Get or set system time (Unix timestamp).

**Request (Set):**
```
AT+TIME=1706918430
```

**Request (Get):**
```
AT+TIME?
```

**Response (Set):**
```json
{
  "ok": true,
  "data": { "time": 1706918430 }
}
```

**Response (Get):**
```json
{
  "ok": true,
  "data": { "time": "2024-02-03T10:00:30Z" }
}
```

> Set echoes the Unix timestamp back in `data.time` (integer); Get returns an
> ISO-8601 string in `data.time`. If time was never set, Get returns
> `{"ok":false,"msg":"Time not set (use AT+TIME=<timestamp>)"}`.

**Error Cases:**
- `{"ok":false,"msg":"Missing timestamp"}` / `"Invalid timestamp"` / `"Invalid time"`

---

##### AT+VERSION - Version Information

Get firmware version.

**Request:**
```
AT+VERSION
```

**Response:**
```json
{
  "ok": true,
  "firmware": "0.0.6"
}
```

**Fields:**
- `firmware`: Firmware version string (the only field returned)

---

#### 3.3.2 Recording Control

##### AT+START - Start Recording

Start a new recording session.

**Request:**
```
AT+START=normal
```

**Parameters:**
- `mode`: "normal", "enhanced", or "rtc"
  - `normal`/`stereo` and `enhanced`/`merge` start an on-card recording
  - `rtc` starts a live BLE stream session — nothing is written to the SD
    card (see Section 4.8). Requires BLE connected with File Data
    notifications enabled; the session aborts if the stream is not started
    with `AT+DOWNLOAD` within 5 seconds.

**Response:**
```json
{
  "ok": true,
  "data": {
    "session": "20240203100000"
  }
}
```

RTC sessions additionally report the mode:

```json
{
  "ok": true,
  "data": {
    "session": "20240203100000",
    "mode": "rtc"
  }
}
```

> `data` contains only the `session` id (an empty object if the id isn't ready yet).

**Error Cases:**
- `{"ok":false,"msg":"Already recording or invalid state"}` / `"Audio module busy"` / `"Failed to start recording"`
- `"WiFi active, cannot record"` / `"USB MSC active, disable USB first"` (recording blocked while WiFi/USB active)
- `"RTC requires BLE connected and file data notify enabled"` (RTC preconditions not met)

**State Change:** IDLE → RECORDING (state broadcast: `"RECORDING"`, or
`"STREAMING"` for RTC sessions)

**Side Effects:**
- Creates new session directory
- Initializes session.json
- Starts audio capture
- Enables button bookmarking

---

##### AT+STOP - Stop Recording

Stop current recording session.

**Request:**
```
AT+STOP
```

**Response:**
```json
{
  "ok": true,
  "data": {
    "session": "20240203100000",
    "duration": 600,
    "frames": 1440000,
    "file_count": 1,
    "total_size": 3600000
  }
}
```

**Fields:**
- `session`: Session ID
- `duration`: Recording duration in seconds
- `frames`: Total audio frames captured
- `file_count`: Number of files in the session (1 for a fresh stop)
- `total_size`: Total bytes of all files

**Error Cases:**
- `{"ok":false,"msg":"No active session"}` / `"Not recording"`

**State Change:** RECORDING → IDLE

**Side Effects:**
- Finalizes session.json
- Closes all files
- Stops audio capture
- Disables button bookmarking

---

##### AT+MARK - Add Bookmark

Add a bookmark at the current recording position.

**Request:**
```
AT+MARK
```

**Response:**
```json
{
  "ok": true,
  "data": {
    "offset": 123
  }
}
```

**Fields:**
- `offset`: Seconds from session start

> A note argument (`AT+MARK=<text>`) is accepted by the parser but is **not
> stored** — the bookmark records only the offset. Bookmarks are stored
> per-session in `marks.bin`.

**Error Cases:**
- `{"ok":false,"msg":"No active session"}` / `"Not recording"` (can only bookmark while recording)
- `{"ok":false,"msg":"MARK not supported in RTC mode"}` (RTC sessions store nothing on-card)

**Side Effects:**
- Writes bookmark to marks.bin
- Sends unsolicited bookmark notification
- Triggers haptic feedback

---

#### 3.3.3 Session Management

##### AT+LIST - List Sessions/Files

List all sessions with pagination, get session details, or list files with pagination.

**Request (All Sessions - First Page):**
```
AT+LIST
```

**Note:** Sessions are sorted newest-first (descending by session ID, which is a timestamp). A shared cache is used for efficient pagination — DELETE operations invalidate the cache.

**Request (Paginated Sessions):**
```
AT+LIST?2&10
```

**Request (Session Details):**
```
AT+LIST=20240203100000
```

**Request (Paginated File List):**
```
AT+LIST=20240203100000?1&20
```

**Response (All Sessions - Paginated):**
```json
{
  "ok": true,
  "data": {
    "total": 50,
    "page": 1,
    "per_page": 10,
    "sessions": [
      {"id": "20240203100000", "files": 30, "size": 5242880, "bookmarks": 5},
      {"id": "20240203120000", "files": 15, "size": 2621440, "bookmarks": 0}
    ]
  }
}
```

**Response (Session Details):**
```json
{
  "ok": true,
  "data": {
    "files": 30,
    "size": 5242880,
    "synced": 15,
    "bookmarks": 5,
    "channels": 2,
    "sample_rate": 16000,
    "mode": "normal"
  }
}
```

**Response (Paginated File List):**
```json
{
  "ok": true,
  "data": {
    "total": 200,
    "page": 1,
    "per_page": 10,
    "files": [
      "0001.opus",
      "0002.opus",
      "0003.opus"
    ]
  }
}
```

**Fields:**
- `total`: Total number of items (sessions or files)
- `page`: Current page number (default 1)
- `per_page`: Items per page (default 10, max 50)
- `sessions`: Array of session objects (session list pagination)
  - `id`: Session ID
  - `files`: Total number of audio files in session
  - `size`: Total bytes of all files
  - `bookmarks`: Number of bookmarks in session
- `files`: Array of file names (file list pagination)
- `id`: Session ID (non-paginated response, deprecated)
- `synced`: Number of files successfully transferred (only in session details)
- `channels`: Audio channels - 1=mono, 2=stereo (only in session details)
- `sample_rate`: Sample rate in Hz, e.g., 16000 (only in session details)
- `mode`: Recording mode - "normal" (stereo) or "enhanced" (mono with DSP) (only in session details)

**Usage Examples:**
```
# List sessions (default: first page, 10 items per page)
AT+LIST
# → Returns {"total":50,"page":1,"per_page":10,"sessions":[...]}

# Get second page of sessions
AT+LIST?2&10
# → Returns sessions 11-20

# Get session details (including synced count and audio format)
AT+LIST=20240203100000
# → Returns files, size, synced, bookmarks, channels, sample_rate for specific session

# List files with pagination (page 1, 10 items per page)
AT+LIST=20240203100000?1&10
# → Returns first 10 files

# Resume transfer from next file after synced count
# If synced=15, resume from file 0016.opus
AT+DOWNLOAD=20240203100000:0016.opus
```

**Error Cases:**
- `3001`: Session not found (when listing files)

---

##### AT+DELETE - Delete Session

Delete a recording session and all its files.

**Request:**
```
AT+DELETE=20240203100000
```

**Response:**
```json
{
  "ok": true,
  "data": { "deleted": true }
}
```

**Fields:**
- `deleted`: `true` once the session directory has been removed

**Error Cases:**
- `{"ok":false,"msg":"Session not found"}` / `"Invalid session ID"` / `"Cannot delete current recording session"`

**Side Effects:**
- Deletes the session directory and all its files
- Updates session count in GSTAT

---

##### AT+MARKS - Get Session Bookmarks

Retrieve bookmarks for a session. Supports summary and paginated formats.

**Request (Summary):**
```
AT+MARKS=20240203100000
```

**Response (Summary):**
```json
{
  "ok": true,
  "data": {
    "session": "20240203100000",
    "total": 50
  }
}
```

**Request (Paginated, page 1):**
```
AT+MARKS=20240203100000?1&10
```

**Response (Paginated):**
```json
{
  "ok": true,
  "data": {
    "total": 50,
    "page": 1,
    "per_page": 10,
    "bookmarks": [
      {"offset": 30},
      {"offset": 60}
    ]
  }
}
```

**Request (Page 2):**
```
AT+MARKS=20240203100000?2&10
```

**Fields:**
- `session`: Session ID (summary response)
- `total`: Total number of bookmarks
- `page`: Current page number (1-based)
- `per_page`: Items per page (default 10, max 50)
- `bookmarks`: Array of bookmark entries; each entry has only `offset` (seconds from session start). Notes are not stored.

**Pagination Logic:**
- Without `?`: Returns summary with total count
- With `?page&per_page`: Returns specific page
  - Default: page=1, per_page=10
  - Maximum per_page: 50
- Client increments `page` to get next page

**Error Cases:**
- `3001`: Session not found
- `3005`: Bookmark file corrupted

---

#### 3.3.4 File Transfer

##### AT+DOWNLOAD - Download File

Start file transfer from device to app. Two modes (the parser only splits on `:`):

**Request (Entire Session):**
```
AT+DOWNLOAD=<session_id>
```

**Request (Single File / Resume):**
```
AT+DOWNLOAD=<session_id>:<filename>
```

**Examples:**
```
# Download all files from session
AT+DOWNLOAD=20250225143000

# Download / resume from a specific file (skips files before it)
AT+DOWNLOAD=20250225143000:0016.opus
```

> Only the `:` separator is parsed. A `/` separator (`session/file`) is **not**
> supported — use `:` for single-file / resume mode.

**RTC Session Case:**

If the requested session is the active RTC session (`AT+START=RTC`), the
command starts the live stream instead of a file transfer (see Section 4.8):

```json
{
  "ok": true,
  "data": { "state": "streaming", "session": "20250225143000" }
}
```

followed by `STREAM_START` / `STREAM_DATA` frames on the File Data
characteristic. The `session:filename` form is rejected for RTC sessions
(`"RTC session has no files"`), and RTC streaming is only available when the
command arrives over BLE (`"RTC streaming requires BLE"` otherwise).

**Resume Logic:**
1. Client queries session details: `AT+LIST=<session_id>`
2. Response includes `synced` count (e.g., 15 files already transferred)
3. Client calculates next file: `synced + 1` → file 0016.opus
4. Client sends: `AT+DOWNLOAD=<session_id>:0016.opus`
5. Device transfers from 0016.opus onwards

**Response (Start, whole session):**
```json
{
  "ok": true,
  "data": { "state": "transmitting", "session": "20250225143000" }
}
```

**Response (Start, single file / resume):**
```json
{
  "ok": true,
  "data": { "state": "transmitting", "session": "20250225143000", "file": "0016.opus", "total": 30, "bytes": 0 }
}
```

**Binary Frames During Transfer:**

After the start response, the device sends binary frames on the File Data characteristic (`0x6E400004`). See Section 4 for the complete frame protocol specification.

**Data Flow:**
1. Device sends JSON start response on Response characteristic
2. For each file:
   - Device sends `FILE_START` frame (filename + size)
   - Device sends `DATA` frames (file data chunks)
   - Device sends `FILE_END` frame (full-file CRC32)
3. Device sends `TRANSFER_DONE` frame (session_id + file_count)
4. Client can resume by sending `AT+DOWNLOAD=session:next_file`

**Disconnect/Resume Flow:**
1. During transfer, BLE disconnects
2. Device automatically cancels transfer
3. Device continues recording (if in RECORDING state)
4. Client reconnects
5. Client sends `AT+DOWNLOAD=session:last_received_file`
6. Transfer resumes from next file

**Error Cases:**
- `{"ok":false,"msg":"Transfer already in progress"}` / `"Session or file not found"`
- `"Missing session_id"`, `"Invalid session ID"`, or `"DOWNLOAD argument too long"`
- `"Invalid download filename"` unless the resume file is exactly
  `NNNN.opus`, from `0001.opus` through the configured maximum chunk index

**State Change:** IDLE → TRANSMITTING

---

---

#### 3.3.5 Recording Control

##### AT+PAUSE - Pause Recording

Pause ongoing recording.

**Request:**
```
AT+PAUSE
```

**Response:**
```json
{
  "ok": true,
  "data": { "paused": true }
}
```

**State Change:** RECORDING → PAUSED

**Side Effects:**
- Stops DMIC capture
- Closes current file
- Keeps session open
- Recording can be resumed with `AT+RESUME`

> **RTC sessions:** pause stops the BLE stream and discards all buffered
> frames, but the microphone pipeline keeps running. Response carries
> `{"paused":true,"stream":true}` and the device state stays RECORDING.

**Error Cases:**
- `{"ok":false,"msg":"Not recording"}` / `"Failed to pause recording"`

---

##### AT+RESUME - Resume Recording

Resume paused recording.

**Request:**
```
AT+RESUME
```

**Response:**
```json
{
  "ok": true,
  "data": { "resumed": true }
}
```

**State Change:** PAUSED → RECORDING

**Side Effects:**
- Creates new file with incremented index
- Resumes DMIC capture
- Continues in same session

> **RTC sessions:** resume restarts the BLE stream from the current frame.
> Response carries `{"resumed":true,"stream":true}`.

**Error Cases:**
- `{"ok":false,"msg":"Not paused"}` / `"Failed to resume recording"`

---

##### AT+CANCEL - Cancel Transfer

Cancel an ongoing file transfer.

**Request:**
```
AT+CANCEL
```

**Response:**
```json
{
  "ok": true,
  "data": { "canceled": true }
}
```

**State Change:** TRANSMITTING → IDLE

**Side Effects:**
- Closes file
- Discards progress
- Does NOT create .transferred marker

**Error Cases:**
- `{"ok":false,"msg":"No active transfer"}` / `"Failed to cancel transfer"`

---

#### 3.3.6 Storage Management

##### AT+AUTODEL - Auto-Delete Policy

Configure automatic deletion policy for transferred sessions.

**Request (Set):**
```
AT+AUTODEL=7
```

**Request (Get):**
```
AT+AUTODEL?
```

**Response (Set/Get):**
```json
{
  "ok": true,
  "data": { "autodel": 7 }
}
```

> `autodel` is an integer (days) or the string `"off"`. Set echoes the applied value.

**Policy Values:**
| Value | Description |
|-------|-------------|
| `off` | Manual delete only (default) |
| `0` | Delete immediately after transfer |
| `1-30` | Delete N days after transfer |

**Error Cases:**
- `{"ok":false,"msg":"Auto-delete must be 0-30 days or off"}` / `"Missing autodel value"`

---

#### 3.3.7 Configuration

##### AT+MODE - Recording Mode

Set recording mode preset.

**Request (Set):**
```
AT+MODE=enhanced
```

**Request (Get):**
```
AT+MODE?
```

**Response (Set/Get):**
```json
{
  "ok": true,
  "data": { "mode": "enhanced" }
}
```

**Valid Values:** "normal", "enhanced"

**Mode Presets:**
- **Normal**: Stereo (L+R channels), 16kbps/channel (32kbps total), complexity 0, no DSP processing, 10-minute file segments
- **Enhanced**: Mono (L+R merged), 32kbps, complexity 1, DSP enabled (noise suppression + dereverberation), 2-minute file segments

Bitrate and complexity are fixed per mode and configured at build time via Kconfig (`CONFIG_CLIP_NORMAL_BITRATE`, `CONFIG_CLIP_ENHANCED_BITRATE`, etc.). They cannot be changed at runtime.

---

##### AT+BRIGHTNESS - OLED Brightness

Get or set the OLED display brightness (contrast). The value is saved to NVS and applied automatically on every boot.

**Request (Set):**
```
AT+BRIGHTNESS=<value>
```

**Request (Query):**
```
AT+BRIGHTNESS?
```

**Response (Set):**
```json
{
  "ok": true,
  "data": { "brightness": 200 }
}
```

**Response (Query):**
```json
{
  "ok": true,
  "data": { "brightness": 128 }
}
```

**Parameters:**
- `brightness`: Integer 0–255 (0 = dimmest, 255 = maximum brightness, default = 128)

**Error Cases:**
- `{"ok":false,"msg":"Brightness must be 0-255"}` / `"Invalid brightness"` / `"Missing brightness value"`

---

##### AT+DEVICE - Device Name

Get the device name.

**Request:**
```
AT+DEVICE
```

**Request (Query):**
```
AT+DEVICE?
```

**Response:**
```json
{
  "ok": true,
  "device": "Clip"
}
```

---

##### AT+NAME - User-Defined Device Name

Set or query a user-defined device name. This is stored persistently and survives reboots. It does not affect BLE or WiFi naming.

**Request (Set):**
```
AT+NAME=My Clip
```

**Request (Clear):**
```
AT+NAME=CLEAR
```

**Request (Query):**
```
AT+NAME?
```

**Response (Set):**
```json
{
  "ok": true,
  "data": {"name": "My Clip"}
}
```

**Response (Query):**
```json
{
  "ok": true,
  "data": {"name": "My Clip"}
}
```

**Response (Empty):**
```json
{
  "ok": true,
  "data": {"name": ""}
}
```

**Validation Rules:**
- Length: 1–256 bytes
- Allowed: printable UTF-8 characters (letters, digits, CJK, spaces, `-`, `_`, etc.)
- Not allowed: control characters (0x00–0x1F), empty string
- Surrounding quotes (`"..."`) are stripped from the argument, so `AT+NAME="My Clip"` is equivalent to `AT+NAME=My Clip`
- `AT+NAME=CLEAR` removes the name (sets to empty)

**Error Cases:**
- Name too long (> 256 bytes)
- Contains control characters
- Empty name (use `CLEAR` to remove)

---

##### AT+WIFI - WiFi AP Control

Control the WiFi Access Point for local file transfer.

**Request (Start):**
```
AT+WIFI=on
```

**Request (Stop):**
```
AT+WIFI=off
```

**Request (Query):**
```
AT+WIFI?
```

**Response (Start):**
```json
{
  "ok": true,
  "data": {
    "ssid": "ClipAP_A1B2",
    "password": "12345678",
    "ip": "192.168.4.1",
    "port": 8089
  }
}
```

**Response (Stop):**
```json
{
  "ok": true,
  "data": { "wifi": "off" }
}
```

**Response (Query):**
```json
{
  "ok": true,
  "data": {
    "running": true,
    "ssid": "ClipAP_A1B2",
    "password": "12345678",
    "ip": "192.168.4.1",
    "port": 8089,
    "connected": true
  }
}
```

**Fields:**
- `ssid`: WiFi SSID (ClipAP_XXXX, last 4 hex of chip ID)
- `password`: WPA2 password (12345678)
- `ip`: AP IP address (192.168.4.1)
- `port`: UDP transfer port (8089)
- `running`: Whether AP is active
- `connected`: Whether a client is connected

**State Change:** IDLE → WIFI_SYNC (on), WIFI_SYNC → IDLE (off)

**Constraints:**
- Cannot start WiFi while recording
- Cannot start recording while WiFi is active

**Auto-off:** WiFi AP automatically stops after 3 minutes if no client connects.

---

##### AT+USB - USB CDC+MSC Control

Enable or disable USB CDC (serial console) and MSC (mass storage / SD card access).

**Request (Enable):**
```
AT+USB=on
```

**Request (Disable):**
```
AT+USB=off
```

**Request (Query):**
```
AT+USB?
```

**Response (Enable/Disable):**
```json
{
  "ok": true,
  "data": {"status": "on"}
}
```

**Response (Query):**
```json
{
  "ok": true,
  "data": {"status": "on"}
}
```

or

```json
{
  "ok": true,
  "data": {"status": "off"}
}
```

**Auto-disable behavior:**
- USB automatically disables on cable unplug
- USB automatically disables after 10 minutes without a USB cable connected

---

##### AT+LOG - SD Log Backend Control

Control the SD card log backend (`/SD:/LOG`, rotating files). Useful for post-mortem
debugging on a device without the UART console (e.g. the production image).

**Request (Set):**
```
AT+LOG=off
AT+LOG=info
AT+LOG=debug
```

**Request (Query):**
```
AT+LOG?
```

**Response (Set):**
```json
{
  "ok": true,
  "data": {"log": "info"}
}
```

**Response (Query):**
```json
{
  "ok": true,
  "data": {"log": "off"}
}
```

**Modes:**
| Mode | Behavior |
|------|----------|
| `off` | Deactivates the FS log backend; the SD card is then free to idle power-gate (lowest idle current) |
| `info` | Ensures the SD is mounted, then logs at INF level and above to `/SD:/LOG` |
| `debug` | Same as `info` but at DBG level (most verbose; for troubleshooting) |

**Notes:**
- Boot default follows the build: the **debug** image enables `info` at boot; the
  **production** image defaults to `off`. `AT+LOG` overrides this at runtime.
- Enabling the backend (`info`/`debug`) keeps the SD mounted, which raises idle
  current — use for diagnostics, then set `off` to restore low-power idle.
- The query reports `info` whenever the backend is active (regardless of debug level).

**Error Cases:**
- Missing or invalid mode (must be `off`, `info`, or `debug`)
- SD card not available when enabling (`info`/`debug`)

---

##### AT+FORMAT - Format SD Card

Format the SD card using FATFS. Deletes all recordings.

**Request:**
```
AT+FORMAT
```

**Response:**
```json
{
  "ok": true
}
```

**Error Cases:**
- SD card not mounted
- Cannot format while recording

---

##### AT+POWEROFF - Power Off

Shut down the device (enters ship mode for ultra-low power).

**Request:**
```
AT+POWEROFF
```

**Response:**
```json
{
  "ok": true,
  "data": {"poweroff": "shutting down"}
}
```

**Side Effects:**
- Displays power off animation
- Enters PMIC ship mode (requires physical button press to wake)
- All unsaved data is preserved

---

##### AT+PAIR - Bluetooth Pairing

Manage BLE pairing.

**Request (Query):**
```
AT+PAIR?
```

**Request (Reset):**
```
AT+PAIR=reset
```

**Response (Query, paired):**
```json
{
  "ok": true,
  "msg": "\"paired\",\"addr\":\"AA:BB:CC:DD:EE:FF\""
}
```

**Response (Query, unpaired):**
```json
{
  "ok": true,
  "msg": "\"unpaired\""
}
```

> Note: unlike other query commands, `AT+PAIR?` returns its payload via the
> `"msg"` field (the status word and, when bonded, the peer address are embedded
> as a JSON-like string fragment inside `msg`), not under `"data"`.

**Response (Reset):**
```json
{
  "ok": true,
  "data": { "rebooting": true, "sd_erase": "pending" }
}
```

**Values:**
- "paired": Bonded to device
- "unpaired": Not bonded

**Side Effects of Reset:**
- Clears BLE bond information (`ble_clear_bonds`) and persists the deletion
  (`settings_save`) so it survives the reboot
- Replies immediately after clearing pairing configuration, before erasing the
  SD card, so the client does not time out on cards containing many recordings
- **Formats the SD card** in the background — destroys all recordings (privacy
  wipe on unpair)
- Reboots the device after SD erasure completes
- Requires re-pairing

> The bond clear + settings persist + SD format all run synchronously before the
> reboot, so the device is guaranteed to come back unbonded with a clean SD card.


---

##### AT+FACTORY - Factory Reset

Restore all settings to factory defaults.

**Request:**
```
AT+FACTORY=confirm
```

**Response:**
```json
{
  "ok": true,
  "msg": "Factory reset complete, rebooting..."
}
```

**Side Effects:**
- Clears all NVS configuration
- Clears BLE pairing
- Deletes ALL recordings from SD card
- Reboots device

**Error Cases:**
- `{"ok":false,"msg":"Add 'confirm' or 'yes' to proceed"}` / `"Factory reset failed"`

**Warning:** Requires "confirm" parameter to prevent accidental execution.

---

##### AT+REBOOT - Reboot Device

Restart the device.

**Request:**
```
AT+REBOOT
```

**Response:**
```json
{
  "ok": true,
  "data": { "reboot": "restarting" }
}
```

**Side Effects:**
- Terminates current recording (if any)
- Stops file transfer (if any)
- Reboots device

---

##### AT+DFU - Enter DFU/Recovery Mode

Set the boot-mode retention register and reboot into MCUboot serial recovery
(for USB/BLE firmware upgrade).

**Request:**
```
AT+DFU
```

**Response:**
```json
{
  "ok": true,
  "data": { "dfu": "rebooting" }
}
```

**Error Cases:**
- `{"ok":false,"msg":"Failed to set boot mode"}`

**Side Effects:**
- Writes `BOOT_MODE_TYPE_BOOTLOADER` to the retention register
- Reboots into MCUboot serial recovery (~500 ms after the response)

---

##### AT+WIFICFG - WiFi Channel / Regulatory Domain

Configure the WiFi AP channel and 2-letter regulatory domain (applied on the
next WiFi start). 5 GHz channels only (36–165).

**Request (Set):**
```
AT+WIFICFG=36:US
```

**Request (Get):**
```
AT+WIFICFG?
```

**Response (Set):**
```json
{
  "ok": true,
  "msg": "Saved (apply on next WiFi start)",
  "data": { "channel": 36, "reg_domain": "US" }
}
```

> Set is the only command that returns both a `"msg"` (informational) and a
> `"data"` echo.

**Response (Get):**
```json
{
  "ok": true,
  "data": { "channel": 36, "reg_domain": "US" }
}
```

**Error Cases:**
- `{"ok":false,"msg":"Missing argument (format: channel:CC)"}` / `"Invalid format (use: channel:CC, e.g. 36:US)"` / `"Channel must be 36-165 (5GHz)"` / `"Reg domain must be 2 uppercase letters"`

---

## 4. File Transfer Protocol (BLE Binary Frame)

### 4.1 Overview

File transfer uses a binary frame protocol over the File Data characteristic (`0x6E400004`). Each BLE notification carries one binary frame, identified by the first byte (frame type). This protocol is shared with the WiFi UDP transport (see Appendix D), with minor differences.

### 4.2 Frame Types

| Type | Hex | Direction | Description |
|------|-----|-----------|-------------|
| DATA | `0x01` | Device→App | File data chunk |
| FILE_START | `0x10` | Device→App | Begin file transfer |
| FILE_END | `0x11` | Device→App | End file (with full-file CRC32) |
| TRANSFER_DONE | `0x12` | Device→App | All files complete |
| STREAM_START | `0x13` | Device→App | Begin RTC live stream (BLE only) |
| STREAM_DATA | `0x14` | Device→App | RTC live Opus frame (BLE only) |
| STREAM_END | `0x15` | Device→App | End RTC live stream (BLE only) |

**BLE-specific behavior:**
- No per-frame CRC (BLE link layer guarantees reliable delivery)
- No FILE_ACK (no retransmission needed)
- No HEARTBEAT (BLE connection management handles keepalive)
- `STREAM_*` frames exist on BLE only (RTC over WiFi/UDP is not supported)

### 4.3 Frame Formats

#### DATA Frame

File data chunk with sequence number.

```
[type:1][seq_lo:1][seq_hi:1][len_lo:1][len_hi:1][payload:N]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x01` |
| 1 | 2 | seq | Sequence number (uint16 LE) |
| 3 | 2 | len | Payload length (uint16 LE) |
| 5 | N | payload | Raw Opus data |

**Header size:** 5 bytes
**Max payload:** MTU - 3 - 5 (e.g., 239 bytes for MTU 247)

#### FILE_START Frame

Signals the beginning of a new file transfer.

```
[type:1][fn_len:1][filename:fn_len][file_size:4]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x10` |
| 1 | 1 | fn_len | Filename length |
| 2 | N | filename | UTF-8 filename (e.g., `"0015.opus"`) |
| 2+N | 4 | file_size | Total file size in bytes (uint32 LE) |

#### FILE_END Frame

Signals the end of the current file with a full-file CRC32 for integrity verification.

```
[type:1][crc32:4]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x11` |
| 1 | 4 | crc32 | IEEE CRC32 of entire file data (uint32 LE) |

CRC32 is computed over all DATA frame payloads concatenated (i.e., the complete file data). Uses polynomial 0xEDB88320 (same as zlib.crc32 with initial value 0xFFFFFFFF).

#### TRANSFER_DONE Frame

Signals that all files in the session have been transferred.

```
[type:1][sid_len:1][session_id:sid_len][file_count:4]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x12` |
| 1 | 1 | sid_len | Session ID length |
| 2 | N | session_id | Session ID string (e.g., `"20260326120000"`) |
| 2+N | 4 | file_count | Total files transferred (uint32 LE) |

#### STREAM_START Frame

Signals the beginning of an RTC live stream (sent once after `AT+DOWNLOAD`
on an RTC session).

```
[type:1][sid_len:1][session_id:sid_len]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x13` |
| 1 | 1 | sid_len | Session ID length |
| 2 | N | session_id | Session ID string (e.g., `"20260326120000"`) |

#### STREAM_DATA Frame

One live Opus frame (20 ms of audio). Same layout as the DATA frame but on
an independent sequence space. Frames are emitted in capture order; when the
device is under BLE backpressure it **drops frames instead of blocking**.
Note that `seq` advances only after a frame is successfully handed to the
BLE link, so dropped frames do **not** consume sequence numbers: loss shows
up as missing frames, not as `seq` jumps. Receivers should still treat a
`seq` discontinuity as a protocol-drift indicator (defensive), not as a
device-side loss count.

```
[type:1][seq_lo:1][seq_hi:1][len_lo:1][len_hi:1][payload:N]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x14` |
| 1 | 2 | seq | Stream sequence number (uint16 LE, starts at 0) |
| 3 | 2 | len | Payload length (uint16 LE) |
| 5 | N | payload | One Opus packet |

#### STREAM_END Frame

Signals the end of the RTC stream.

```
[type:1][reason:1]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x15` |
| 1 | 1 | reason | `0`=stopped by AT+STOP, `1`=start timeout, `2`=BLE disconnect |

### 4.4 Transfer Flow

```
App                              Device
 │                                  │
 │─ AT+DOWNLOAD=20260326120000 ───>│
 │<─ {"ok":true} ──────────────────│
 │                                  │
 │  For each file in session:
 │                                  │
 │<─ FILE_START("0001.opus", 2400)─│
 │<─ DATA(seq=0, len=239, ...) ────│
 │<─ DATA(seq=1, len=239, ...) ────│
 │<─ DATA(seq=2, len=239, ...) ────│
 │<─ ...                          │
 │<─ DATA(seq=9, len=183, ...) ────│  Last chunk (<239)
 │<─ FILE_END(crc32=0xA1B2C3D4) ──│
 │                                  │
 │<─ FILE_START("0002.opus", 2400)─│
 │<─ DATA(seq=0, ...) ─────────────│
 │<─ ...                          │
 │<─ FILE_END(crc32=...) ──────────│
 │                                  │
 │<─ TRANSFER_DONE("20260326120000", 30)│
 │                                  │
```

**Key points:**
- All frames for a session are sent on the same File Data characteristic
- AT command responses continue to arrive on the Response characteristic during transfer
- The device can process AT commands (e.g., `AT+GSTAT`) concurrently with file transfer

### 4.5 Flow Control

File transfer runs in the background. AT commands can be sent during transfer:

**Supported during transfer:**
- `AT+GSTAT` — Query status (returns "TRANSMITTING" state with `state`, `session`, `total`, `bytes` fields)
- `AT+CANCEL` — Cancel transfer (thread-safe: handled in transfer thread)
- `AT+PAUSE` — Pause transfer
- `AT+RESUME` — Resume paused transfer

**Example:**
```
App: AT+DOWNLOAD=20260326120000
Device: {"ok":true}
Device: <FILE_START frame>
Device: <DATA frames...>
App: AT+GSTAT  (Non-blocking!)
Device: {"ok":true, "data":{"state":"TRANSMITTING",...}}
Device: <DATA frames continue...>
Device: <FILE_END frame>
Device: <TRANSFER_DONE frame>
```

### 4.6 Resume from File

To resume a partially transferred session, use the colon syntax:

```
AT+DOWNLOAD=<session_id>:<start_file>
```

**Resume logic:**
1. Client queries session details: `AT+LIST=<session_id>`
2. Response includes `synced` count (e.g., 15 files already transferred)
3. Client calculates next file: `synced + 1` → file `0016.opus`
4. Client sends: `AT+DOWNLOAD=<session_id>:0016.opus`
5. Device transfers from `0016.opus` onwards

**Example:**
```
# Query synced count
AT+LIST=20260326120000
→ {"ok":true,"data":{"synced":15,"files":30,...}}

# Resume from file 0016.opus
AT+DOWNLOAD=20260326120000:0016.opus
→ {"ok":true}
```

### 4.7 Continuous Sync (Real-time)

When the device is actively recording, the client can start a transfer that continues until recording stops. This enables real-time file download during recording.

**Flow:**
1. Start recording: `AT+START=enhanced`
2. Immediately start download: `AT+DOWNLOAD=<session_id>`
3. Device streams files as they are written to SD card
4. When recording stops (`AT+STOP`), device sends `TRANSFER_DONE`
5. Client knows all files have been received

**Usage in tools:**
- `record.py` — real-time sync during recording
- `clip-web.py` — background sync task when recording starts
- Both use `SessionSync(continuous=True)`

### 4.8 RTC Live Streaming

RTC mode streams microphone audio live over BLE without writing anything to
the SD card. It favors low latency over completeness: the device keeps only a
small bounded queue of encoded frames and drops the oldest ones when the
consumer is absent or too slow.

**Preconditions:** BLE connected **and** the File Data characteristic CCCD
subscribed (notify enabled) before `AT+START=RTC`.

**Flow:**
1. Client connects and subscribes to File Data notifications
2. `AT+START=RTC` → response carries the session id, mic pipeline starts
   (device state broadcast: `"STREAMING"`). Frames are encoded immediately
   but only buffered (bounded, drop-oldest).
3. `AT+DOWNLOAD=<session_id>` → device **discards** whatever was queued
   before this point (RTC delivers "now" — pre-DOWNLOAD audio is never
   sent), sends `STREAM_START`, then `STREAM_DATA` frames in real time
4. `AT+STOP` → `STREAM_END` (reason 0), session torn down

**Pause/resume:** `AT+PAUSE` discards all buffered data and stops emission
(the mic pipeline keeps running); `AT+RESUME` continues from the current
frame. `AT+MARK` is rejected in RTC mode.

**Automatic teardown:** the session aborts itself (event `rtc`/`timeout`) if
`AT+DOWNLOAD` does not arrive within 5 s of `AT+START=RTC`, or immediately on
BLE disconnect.

**Notes:**
- RTC sessions never appear in `AT+LIST` (nothing is stored)
- The stream shares the File Data characteristic with file transfer; the two
  are mutually exclusive (a file download cannot run while streaming)
- Backpressure policy: dropped frames are counted, never retried. Because
  `seq` only advances on successful send, frame loss appears as missing
  frames (sequence discontinuities should not occur under current firmware
  and the reliable BLE link; treat any as protocol drift)

## 5. State Machines

### 5.1 Device State Machine

```
┌─────────────────────────────────────────────────────────────┐
│                      Global Device State                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐                                          │
│  │ UNINITIALIZED│                                          │
│  └───────┬──────┘                                          │
│          │ boot complete                                    │
│          ▼                                                  │
│  ┌──────────────┐  AT+START/button RTC   ┌──────────┐       │
│  │     IDLE     │<────────────────────────│RECORDING │       │
│  └──┬───┬──────┘                        └─────┬────┘       │
│     │   │                                      │            │
│     │   │ AT+WIFI=on                             │ AT+STOP/   │
│     │   ▼                                      │ Long press │
│     │ ┌──────────────┐                          │            │
│     │ │  WIFI_SYNC  │                          │            │
│     │ └──────┬───────┘                          ▼            │
│     │        │                                      │            │
│     │        │ AT+WIFI=off                          │            │
│     │        ▼                                      │            │
│     │     IDLE                                    ┌─────┴────┐       │
│     │                                            │   IDLE   │       │
│     │ AT+DOWNLOAD                                  └──────────┘       │
│     │                                            │                    │
│     ▼                                            ▼                    │
│  ┌──────────────┐                        ┌──────────────┐       │
│  │ TRANSMITTING │<───────────────────────│    PAUSED    │──────>│
│  └──────┬───────┘    AT+PAUSE            └──────┬───────┘       │
│          │                                  │       │               │
│          │ AT+RESUME                         │ AT+CANCEL             │
│          ▼                                  ▼       ▼               │
│       IDLE <───────────────────────────────┘    IDLE               │
│                                                             │
│         │ AT+CANCEL / Error                             │
│         ▼                                               │
│  ┌──────────────┐                                       │
│  │    ERROR     │──────────────────────────────────────┘
│  └──────────────┘         Recovery / AT+REBOOT          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**States:**
- **UNINITIALIZED**: Booting, hardware initialization
- **IDLE**: Ready to record, transfer, or start WiFi
- **RECORDING**: Actively recording audio
- **TRANSMITTING**: Actively transferring file
- **WIFI_SYNC**: WiFi AP active, file transfer available
- **PAUSED**: Recording paused
- **ERROR**: Error state, requires intervention

**Constraints:**
- Cannot start WiFi while recording (RECORDING → WIFI_SYNC is invalid)
- Cannot start recording while WiFi is active (WIFI_SYNC → RECORDING is invalid)
- Only IDLE state can transition to RECORDING or WIFI_SYNC

### 5.2 Recording State Machine

```
┌─────────────────────────────────────────────────────────┐
│                    Recording State                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   ┌──────────┐   Button RTC / AT+START        ┌────────┐│
│   │   IDLE   │ ─────────────────────────────> │RECORDING│
│   └──────────┘                                  └────┬───┘
│        ▲                                             │   │
│        │                Long press (1s) / AT+STOP    │   │
│        │ <───────────────────────────────────────────┘   │
│        │                                                     │
│   Short press (add bookmark - only during recording)        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Transitions:**
- IDLE → RECORDING: `AT+START`, or long button press starting an RTC session
- RECORDING → IDLE: Long button press OR `AT+STOP`

**Recording-Specific Actions:**
- Short press during RECORDING: Add bookmark

### 5.3 Transfer State Machine

```
┌─────────────────────────────────────────────────────────┐
│                    Transfer State                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   ┌──────────┐      AT+DOWNLOAD       ┌──────────────┐ │
│   │   IDLE   │ ──────────────────────>│ TRANSMITTING │ │
│   └──────────┘                        └──────┬───────┘ │
│        ▀                                      │        │
│         │            AT+PAUSE /               │        │
│         │            Disconnect               │        │
│         └─────────────────────────────────────┘        │
│                  │                                    │
│                  ▼                                    │
│           ┌──────────┐                               │
│           │  PAUSED  │                               │
│           └────┬─────┘                               │
│                │                                     │
│    ┌───────────┴─────────────┐                       │
│    │                         │                       │
│    ▼                         ▼                       │
│ AT+RESUME              AT+CANCEL                    │
│    │                         │                       │
│    └─────────────────────────┼───────────────────────┘
│                              ▼
│                         ┌──────────┐
│                         │   IDLE   │
│                         └──────────┘
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Transitions:**
- IDLE → TRANSMITTING: `AT+DOWNLOAD`
- TRANSMITTING → PAUSED: `AT+PAUSE` OR disconnect
- PAUSED → TRANSMITTING: `AT+RESUME`
- TRANSMITTING/PAUSED → IDLE: `AT+CANCEL` OR completion OR timeout

### 5.4 Connection State Machine

```
┌─────────────────────────────────────────────────────────┐
│                   BLE Connection State                  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   ┌─────────────┐                                      │
│   │ DISCONNECTED│<─────────────────────────┐           │
│   └──────┬──────┘                          │           │
│          │                                 │           │
│          │ Connect / Auto-advertise        │           │
│          ▼                                 │           │
│   ┌─────────────┐                          │           │
│   │  CONNECTING │                          │           │
│   └──────┬──────┘                          │           │
│          │                                 │           │
│          │ Connected                       │           │
│          ▼                                 │           │
│   ┌─────────────┐   Pairing required? ┌───┴──────┐    │
│   │  CONNECTED  │ ──────────────────>│  PAIRING  │    │
│   └──────┬──────┘                    └─────┬─────┘    │
│          │                                 │          │
│          │ Paired                          │          │
│          ▼                                 │          │
│   ┌─────────────┐                          │          │
│   │   BONDED    │<─────────────────────────┘          │
│   └──────┬──────┘                                     │
│          │                                             │
│          │ Disconnect / AT+PAIR=reset                  │
│          └─────────────────────────────────────────────┘
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**States:**
- **DISCONNECTED**: Not connected, advertising
- **CONNECTING**: Connection in progress
- **CONNECTED**: Connected but not paired
- **PAIRING**: Pairing process active
- **BONDED**: Connected and bonded (secure)

## 6. Data Formats

### 6.1 On-Card Recording Layout

Recordings use one fixed FAT32 layout:

```
/SD:/REC/YYYYMMDD/HH/MM/SS/
  session.json
  marks.bin
  0/0001.opus
```

`YYYYMMDDHHMMSS` remains the session ID exposed by `AT+LIST`, `AT+DOWNLOAD`,
and `AT+DELETE`; `SS` is its final two digits. Segment files are placed in
numbered group directories, with at most `CONFIG_CLIP_STORAGE_FILES_PER_GROUP`
files in each group. The former `/SD:/REC/<session_id>/` layout is unsupported.

### 6.2 Session Metadata (session.json)

Stored in each session directory, contains session information, sync progress, and audio format.

**Created**: When recording starts (session is created)
**Updated**: When recording stops (duration, files updated) and when transfer ends (synced count)

```json
{
  "id": "20240203100000",
  "duration": 600,
  "files": 30,
  "synced": 15,
  "channels": 2,
  "sample_rate": 16000,
  "mode": "normal"
}
```

**Fields:**
- `id`: Session ID (timestamp format: YYYYMMDDHHMMSS, 14 digits)
- `duration`: Recording length in seconds (0 while recording)
- `files`: Total number of audio files in session (0 while recording)
- `synced`: Number of files that have been successfully transferred
- `channels`: Audio channels (1=mono, 2=stereo)
- `sample_rate`: Sample rate in Hz (e.g., 16000)
- `mode`: Recording mode ("normal" or "enhanced")

**Purpose:**
- Track transfer progress for resume functionality
- Store audio format for proper decoding/playback
- Enable cleanup of already-transferred files
- Support disconnect/reconnect scenarios

**Example Usage:**
```
# Session has 30 files, 15 have been transferred
# Audio is normal mode (stereo, 2 channels) at 16kHz
# Next transfer should start from file 0016.opus
AT+DOWNLOAD=20240203100000:0016.opus
```

### 6.3 Bookmark Data (marks.bin)

Binary format for efficient bookmark storage.

**Header (6 bytes):**
```
[4 bytes magic: "BMRK"]
[2 bytes count: uint16_t]
```

**Entry (78 bytes):**
```
[4 bytes timestamp: uint32]
[4 bytes offset: uint32 - seconds from session start]
[2 bytes file_index: uint16]
[4 bytes file_offset: uint32]
[64 bytes note: null-terminated UTF-8 string]
```

**Total entry size:** 78 bytes (fixed)

**Example C struct:**
```c
struct __attribute__((packed)) mark_entry {
    uint32_t timestamp;
    uint32_t offset_sec;
    uint16_t file_index;
    uint32_t file_offset;
    char note[64];
};
```

**Usage:**
- Stored on device: `/SD:/REC/{session_id}/marks.bin`
- Created when session starts
- Updated when bookmarks are added (in-memory, flushed on save)

### 6.4 Bookmarks JSON (bookmarks.json)

JSON format exported after sync for frontend visualization.

**File location:** `recordings/{session_id}/bookmarks.json`

**Format:**
```json
[
  {"offset": 30},
  {"offset": 60},
  {"offset": 90}
]
```

**Fields per bookmark:**
- `offset`: Seconds from session start (for positioning in merged audio)

> Bookmarks store only the offset — there is no note text.

**Usage:**
- Generated by sync tools (sync.py, record.py)
- Used by frontend to display markers on audio timeline
- Position calculation: `byte_offset = offset * sample_rate * channels * bytes_per_sample`

### 6.5 Opus Frame Format

Each Opus file is a sequence of frames:

```
[2 bytes length][Opus frame data][2 bytes length][Opus frame data]...
```

- **Length**: uint16, little-endian
- **Frame data**: Raw Opus encoded bytes
- **Frame size**: Typically 20ms @ 16kHz = 320 samples

### 6.5 Transfer Marker (.transferred)

Empty file created upon successful transfer completion.

```
touch /SD:/REC/20240203100000/.transferred
```

**Purpose:**
- Marks session as successfully transferred
- Used by auto-delete policy

## 7. Notifications and Events

### 7.1 Unsolicited Notifications

The device sends unsolicited notifications via the Response characteristic for important events.

#### 7.1.1 Recording State Change

Sent when recording state changes (start, stop, pause, resume). Uses `"event":"state"` to distinguish from AT command responses.

```json
{"event":"state","state":"RECORDING","session":"20240203100000"}
```

**Trigger:** Persistent recording starts with `AT+START`; RTC streaming starts
with `AT+START=rtc` or a button long press from IDLE.

```json
{"event":"state","state":"IDLE","session":"20240203100000","duration":600}
```

**Trigger:** Recording stops (AT+STOP or button long press). `duration` is in seconds.

```json
{"event":"state","state":"PAUSED","session":"20240203100000"}
```

**Trigger:** Recording paused (AT+PAUSE)

```json
{"event":"state","state":"RECORDING","session":"20240203100000"}
```

**Trigger:** Recording resumed (AT+RESUME)

**Fields:**
- `event`: Always `"state"` for state change events
- `state`: New state — `"RECORDING"`, `"IDLE"`, or `"PAUSED"`
- `session`: Session ID
- `duration`: Recording duration in seconds (only present on stop/IDLE)

**Notes:**
- Sent on the Response Send characteristic (same as AT responses)
- Distinguish from AT responses by checking for the `"event"` field
- Not sent if BLE is not connected or notifications are not enabled
- Triggered by both AT commands and button events

#### 7.1.2 Bookmark Mark Event

Sent when a bookmark is added during recording.

```json
{"event":"mark","session":"20240203100000","mark_count":3}
```

**Trigger:** Bookmark added (AT+MARK or button short press)

**Fields:**
- `event`: Always `"mark"` for bookmark events
- `session`: Session ID
- `mark_count`: Total number of bookmarks in the session after this mark

#### 7.1.3 Connection / USB / Storage Events

Other state-change events use the generic two-field form
`{"event":"<name>","status":"<status>"}` (built by `ble_notify_event`):

| `event` | `status` | Trigger |
|---------|----------|---------|
| `ble` | `connected` / `disconnected` | A central connects / disconnects |
| `usb` | `on` / `off` | USB CDC enabled / disabled (cable, 10-min auto-off, or `AT+USB`) |
| `storage` | `full` | SD card crosses the storage-full threshold; recording is refused |

Example:
```json
{"event":"usb","status":"on"}
```

> Distinguish notifications from AT responses by the presence of the `"event"`
> field. There is no generic `battery_low` / `storage_low` / `error` push
> notification; low battery is shown on the OLED only.

### 7.2 Audio Visualization Data

Real-time audio energy data is sent via the Audio Visualization characteristic (`0x6E400005`), not as a JSON event. See Section 2.2.4 for the data format.

### 7.3 System Events

#### Connection Event

```json
{
  "ok": true,
  "event": "connected",
  "addr": "AA:BB:CC:DD:EE:FF"
}
```

#### Disconnection Event

```json
{
  "ok": true,
  "event": "disconnected",
  "reason": "timeout"
}
```

### 7.4 BLE Event Notifications

Events are pushed to the app via the Response Send characteristic (`6E400003`) as JSON objects. All events contain an `"event"` key, which distinguishes them from AT command responses (which use `"ok"` as the top-level key without `"event"`).

**General Format:**
```json
{"event":"<type>","status":"<value>"}
```

#### Event Types

| Event Type | Status Values | Description |
|------------|---------------|-------------|
| `ble` | `connected`, `disconnected` | BLE connection state change |
| `wifi` | `on`, `off` | WiFi AP started or stopped |
| `usb` | `on`, `off` | USB CDC+MSC enabled or disabled |

**Examples:**

BLE connected:
```json
{"event":"ble","status":"connected"}
```

BLE disconnected:
```json
{"event":"ble","status":"disconnected"}
```

WiFi AP started:
```json
{"event":"wifi","status":"on"}
```

WiFi AP stopped (manual or auto-off):
```json
{"event":"wifi","status":"off"}
```

USB enabled:
```json
{"event":"usb","status":"on"}
```

USB disabled (manual or auto-disable):
```json
{"event":"usb","status":"off"}
```

**Client handling:** When receiving a JSON message on the Response Send characteristic, check for the `"event"` key. If present, the message is an event notification rather than a command response.

## 8. Error Handling

### 8.1 Error Response Format

Every error is a JSON object with `"ok": false` and a human-readable `"msg"`
string. **There are no numeric error codes** — handlers return only the message.

```json
{
  "ok": false,
  "msg": "Human-readable error message"
}
```

### 8.2 Common Error Messages

These message strings appear across commands (exact text from the handlers):

| Message | Typical cause |
|---------|---------------|
| `SD card not mounted` | SD not present / not mounted when a storage command runs |
| `Failed to list sessions` | SD I/O error enumerating sessions |
| `Session not found` | Unknown session id |
| `Cannot delete current recording session` | `AT+DELETE` on the active session |
| `Invalid session ID` | Malformed id |
| `Already recording or invalid state` | `AT+START` while recording |
| `No active session` / `Not recording` | `AT+STOP`/`AT+MARK`/`AT+PAUSE` with nothing running |
| `Not paused` | `AT+RESUME` while not paused |
| `Transfer already in progress` | `AT+DOWNLOAD` while one is active |
| `No active transfer` | `AT+CANCEL` with nothing transferring |
| `Mode must be normal or enhanced` | Bad `AT+MODE` value |
| `Brightness must be 0-255` | `AT+BRIGHTNESS` out of range |
| `Auto-delete must be 0-30 days or off` | Bad `AT+AUTODEL` value |
| `Log mode must be off, info or debug` | Bad `AT+LOG` value |
| `Cannot format while recording` | `AT+FORMAT` while recording |
| `Recording in progress, stop first` | `AT+USB`/`AT+WIFI` blocked by active recording |
| `Cannot start WiFi in current state` | `AT+WIFI=on` while recording/transferring |

### 8.3 Recovery

- **Storage errors** (`SD card not mounted`, list/format failures): reseat or
  replace the SD card; the SD stack lazily remounts on next access.
- **State errors** (`Already recording`, `Transfer already in progress`): stop
  the active operation first (`AT+STOP` / `AT+CANCEL`).
- **Persistent/hung state**: `AT+REBOOT`, or hold the button to power off into
  ship mode and repower.

## 9. Timing and Constraints

### 9.1 Command Timeouts

| Operation | Timeout |
|-----------|---------|
| Command processing | 5 seconds |
| File open | 2 seconds |
| Recording start | 3 seconds |
| Factory reset | 10 seconds |
| Reboot | 5 seconds |

### 9.2 Transfer Timeouts

| Operation | Timeout |
|-----------|---------|
| Transfer start | 10 seconds |
| Between chunks | 30 seconds |
| Pause resume | 5 minutes |
| Total transfer | 1 hour |

### 9.3 Rate Limiting

To prevent BLE congestion:
- **Max commands per second**: 10
- **Min interval between notifications**: 20ms

### 9.4 Buffer Sizes

| Buffer | Size |
|--------|------|
| Command buffer | 512 bytes |
| Response buffer | 512 bytes |
| File chunk buffer | 4096 bytes (compile-time via Kconfig) |
| Audio buffer | 32KB |
| SD card buffer | 4KB |

## 10. Security Considerations

### 10.1 Authentication

**LE Secure Connections (mandatory)**
- Uses Elliptic Curve Diffie-Hellman (ECDH)
- Provides MITM protection
- Numeric comparison or Passkey entry

### 10.2 Encryption

**AES-128 CCM (mandatory)**
- All BLE traffic encrypted
- Keys derived from pairing process
- Bonded devices store keys for reconnection

### 10.3 Authorization

**Single Bond Policy**
- Device stores bond for one central device
- New pairing clears previous bond
- AT+PAIR=reset clears bond manually (also formats the SD card for privacy, then reboots)

## 11. Command Sequences

### 11.1 Typical Recording Workflow

```
1. Connect: App discovers device, connects, pairs
2. Check status: AT+GSTAT
3. Set mode: AT+MODE=enhanced
4. Start recording: AT+START
5. [Optional] Add bookmarks: AT+MARK=Important point
6. Stop recording: AT+STOP
7. [Later] Sync session (see 11.2)
```

### 11.2 Complete Sync Workflow

```
1. List sessions: AT+LIST
2. For each session:
   a. Get session info: AT+LIST=<session>  (includes synced count, audio format)
   b. Get bookmarks: AT+MARKS=<session>
   c. Download: AT+DOWNLOAD=<session>
   d. Receive binary frames (FILE_START → DATA → FILE_END per file)
   e. Receive TRANSFER_DONE frame
   f. Verify file CRC32 from FILE_END frames
3. Optionally delete session: AT+DELETE=<session>
```

### 11.3 Error Recovery Sequences

**Transfer Failure Recovery:**
```
1. Detect error (disconnect or timeout)
2. Device automatically cancels transfer on disconnect
3. Wait for reconnection (auto-reconnect)
4. Query session: AT+LIST=<session_id> (get synced count)
5. Resume from next file: AT+DOWNLOAD=<session_id>:<next_file>
```

**SD Card Error Recovery:**
```
1. Detect error: {"error":"SD card error"}
2. Stop current operation
3. Reinsert SD card
4. Wait for detection
5. Retry operation
```

## 12. Design Notes

**Notes:**
- New commands are additive (old apps ignore unknown events)
- Optional fields can be added to responses
- Bitrate and complexity are mode-specific (configured at build time via Kconfig), not individually configurable at runtime
- Transfer chunk size is compile-time (`CONFIG_CLIP_TRANSFER_CHUNK_SIZE`)
- AGC is not supported (SpeexDSP FIXED_POINT build limitation)

## Appendix A: Complete Command Reference

### Quick Reference Table

| Command | Type | Purpose | Section |
|---------|------|---------|---------|
| AT+GSTAT | EXEC | Get device status | 3.3.1 |
| AT+TIME | GET/SET | System time | 3.3.1 |
| AT+VERSION | EXEC | Version info | 3.3.1 |
| AT+DEVICE | EXEC/GET | Device name | 3.3.7 |
| AT+START | EXEC/SET | Start recording | 3.3.2 |
| AT+STOP | EXEC | Stop recording | 3.3.2 |
| AT+MARK | EXEC/SET | Add bookmark | 3.3.2 |
| AT+LIST | GET/SET | List sessions/files | 3.3.3 |
| AT+DELETE | SET | Delete session | 3.3.3 |
| AT+MARKS | GET/SET | Get bookmarks | 3.3.3 |
| AT+DOWNLOAD | SET | Download file | 3.3.4 |
| AT+PAUSE | EXEC | Pause recording | 3.3.5 |
| AT+RESUME | EXEC | Resume recording | 3.3.5 |
| AT+CANCEL | EXEC | Cancel transfer | 3.3.5 |
| AT+AUTODEL | GET/SET | Auto-delete policy | 3.3.6 |
| AT+FORMAT | EXEC | Format SD card | 3.3.7 |
| AT+POWEROFF | EXEC | Power off device | 3.3.7 |
| AT+WIFI | EXEC/GET/SET | WiFi AP control | 3.3.7 |
| AT+USB | GET/SET | USB CDC+MSC control | 3.3.7 |
| AT+MODE | GET/SET | Recording mode | 3.3.7 |
| AT+BRIGHTNESS | GET/SET | OLED brightness | 3.3.7 |
| AT+PAIR | GET/SET | BLE pairing | 3.3.7 |
| AT+FACTORY | SET | Factory reset | 3.3.7 |
| AT+REBOOT | EXEC | Reboot | 3.3.7 |
| AT+NAME | GET/SET | User device name (≤256 bytes) | 3.3.7 |
| AT+LOG | GET/SET | SD log backend (off/info/debug) | 3.3.7 |

## Appendix E: Button Events

The device has a single user button (GPIO1.15, active-low) with multi-level press detection.

### E.1 Button Actions

| Action | Trigger | Behavior |
|--------|---------|----------|
| Single Click | Press & release (< 1s) | Context-dependent (see below) |
| Long Press | Hold > 1s | Start RTC streaming from IDLE or stop an active recording; confirm with vibration |
| Long Press Level 1/2/3 | Continue holding > 2s/3s/4s | Power off screen (cancel on release) |
| Release | Button released | Execute deferred action or power off |
| Double Click | Two quick presses | Reserved (no action) |

### E.2 Single Click Behavior

| Current State | Action |
|---------------|--------|
| RECORDING | Add bookmark |
| PAUSED | Add bookmark |
| IDLE | Show status bar (timed) |
| WIFI_SYNC | Show status bar (timed) |
| ERROR | Show status bar (timed) |

### E.3 Long Press Behavior

**Recording active:**
1. At 1s hold → Stop recording immediately + vibrate
2. Continue holding → Enter power-off flow

**Idle:**
1. At 1s hold → Vibrate to confirm threshold
2. Continue holding → Enter power-off flow
3. On release before power-off levels → Request an RTC session
4. RTC starts only when BLE is connected and File Data notify is enabled;
   otherwise the device remains idle

**Error / WiFi Sync:** Long press does not start a session.

**Charging:** Power-off is blocked. Long press levels are ignored.

### E.4 Power-Off Flow

1. Long press reaches Level 1 (> 2s hold) → Power-off screen displayed
2. User releases button → Device enters ship mode (ultra-low power)
3. If user releases before Level 1 → Action canceled, no power-off

### E.5 State Change Notifications

Button actions that change state send unsolicited notifications:

| Action | Notification |
|--------|-------------|
| Start RTC from button | `{"event":"state","state":"STREAMING",...}` |
| Stop recording | `{"event":"state","state":"IDLE",...}` |
| Add bookmark | `{"event":"mark","session":"...","mark_count":N}` |

## Appendix B: Example Sessions

### Session 1: First Time Setup

```
App: AT+GSTAT
Device: {"ok":true,"data":{"state":"IDLE","battery":100,"charging":false,...}}

App: AT+TIME=1706918430
Device: {"ok":true}

App: AT+MODE=enhanced
Device: {"ok":true}
```

### Session 2: Recording and Transfer

```
App: AT+START
Device: {"ok":true,"data":{"session":"20240203100000",...}}
Device: {"event":"state","state":"RECORDING","session":"20240203100000"}

[Recording in progress, Audio Vis data streaming on char 0x6E400005...]

App: AT+MARK=Important point
Device: {"ok":true,"data":{"timestamp":1706918430,...}}
Device: {"event":"mark","session":"20240203100000","mark_count":1}

App: AT+STOP
Device: {"ok":true,"data":{"duration":600,...}}
Device: {"event":"state","state":"IDLE","session":"20240203100000","duration":600}

App: AT+LIST
Device: {"ok":true,"data":{"total":1,"sessions":[...]}}

App: AT+DOWNLOAD=20240203100000
Device: {"ok":true}
Device: <FILE_START "0001.opus" size=2400>
Device: <DATA seq=0 len=239 payload=...>
Device: <DATA seq=1 len=239 payload=...>
...
Device: <FILE_END crc32=0xA1B2C3D4>
Device: <FILE_START "0002.opus" size=2400>
Device: <DATA seq=0 ...>
...
Device: <FILE_END crc32=...>
Device: <TRANSFER_DONE "20240203100000" count=30>
```

## Appendix C: Performance Characteristics

### BLE Transfer Rates

| MTU | Throughput | 1MB Time |
|-----|------------|----------|
| 23 | ~8 KB/s | ~2m 5s |
| 247 | ~22 KB/s | ~46s |
| 517 | ~28 KB/s | ~36s |

**Optimal Configuration:** MTU 517

### WiFi UDP Transfer Rates

| Scenario | Throughput | 1MB Time |
|----------|------------|----------|
| Typical WiFi | ~500 KB/s | ~2s |

### Memory Usage

| Component | Usage |
|-----------|-------|
| Audio buffer | 32 KB |
| Opus encoder | 20 KB |
| SpeexDSP | 10 KB |
| Transfer buffer | 4 KB |
| BLE stack | ~50 KB |
| Total fixed | ~116 KB |

**Available heap:** ~32 KB from 192 KB non-secure SRAM

## Appendix D: WiFi UDP Transfer Protocol

The WiFi UDP transport provides high-speed local file transfer when the device is in WiFi AP mode. It uses the same binary frame protocol as BLE (Section 4) with additional frames for reliability and keepalive.

### D.1 WiFi AP Configuration

| Parameter | Value |
|-----------|-------|
| SSID | `ClipAP_XXXX` (last 4 hex digits of chip ID) |
| Password | `12345678` |
| IP Address | `192.168.4.1` |
| UDP Port | `8089` |
| Protocol | UDP |

### D.2 Frame Types

| Type | Hex | Direction | Description |
|------|-----|-----------|-------------|
| DATA | `0x01` | Device→Client | File data (with per-frame CRC32) |
| FILE_ACK | `0x03` | Client→Device | File verification result |
| FILE_START | `0x10` | Device→Client | Begin file transfer |
| FILE_END | `0x11` | Device→Client | End file (full-file CRC32) |
| TRANSFER_DONE | `0x12` | Device→Client | All files complete |
| AT_RESP | `0x20` | Device→Client | AT command response (JSON) |
| HEARTBEAT | `0x30` | Bidirectional | Keepalive |

> RTC live-stream frames (`0x13`–`0x15`, Section 4.3) are BLE-only and are
> not defined for the UDP transport.

### D.3 BLE vs WiFi UDP Comparison

| | BLE | WiFi UDP |
|---|---|---|
| AT command | BLE Write char | UDP plain text `"AT+XXX\n"` |
| AT response | BLE Notify (JSON) | UDP AT_RESP frame (`0x20`) |
| DATA header | 5 bytes | 9 bytes (+4 CRC32) |
| Per-frame CRC | None (link layer) | IEEE CRC32 per frame |
| FILE_ACK | None | Yes (CRC mismatch → retransmit) |
| Heartbeat | None | 5s interval, 30s timeout |
| Throughput | ~15 KB/s | ~500 KB/s |

### D.4 Frame Formats (UDP-specific differences)

#### DATA Frame (UDP)

```
[type:1][seq_lo:1][seq_hi:1][len_lo:1][len_hi:1][crc32:4][payload:N]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x01` |
| 1 | 2 | seq | Sequence number (uint16 LE, wraps at 4096) |
| 3 | 2 | len | Payload length (uint16 LE) |
| 5 | 4 | crc32 | IEEE CRC32 of payload (uint32 LE) |
| 9 | N | payload | Raw Opus data |

**Header size:** 9 bytes (4 bytes larger than BLE due to per-frame CRC32)
**Max payload:** 1024 bytes

#### FILE_ACK Frame (Client→Device)

```
[type:1][result:1]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x03` |
| 1 | 1 | result | `0x00` = CRC OK, `0x01` = CRC mismatch |

Sent by client after receiving FILE_END. If CRC mismatch, device retransmits the file (up to 3 retries).

#### AT_RESP Frame

```
[type:1][len_lo:1][len_hi:1][json_data:N]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x20` |
| 1 | 2 | len | JSON response length (uint16 LE) |
| 3 | N | json_data | JSON response text |

#### HEARTBEAT Frame (Bidirectional)

```
[type:1][timestamp:4]
```

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | type | `0x30` |
| 1 | 4 | timestamp | Uptime in milliseconds (uint32 LE) |

**Interval:** 5 seconds
**Timeout:** 30 seconds (connection considered lost)

### D.5 AT Command Format (UDP)

AT commands are sent as plain text over UDP (no binary framing):

```
AT+GSTAT\n
AT+LIST\n
AT+DOWNLOAD=20260326120000\n
```

The trailing newline (`\n`) is required.

### D.6 Shared Frame Formats

FILE_START, FILE_END, and TRANSFER_DONE frames use the same format as BLE (see Section 4.3).

### D.7 Transfer Flow (UDP)

```
Client                            Device (192.168.4.1:8089)
 │                                  │
 │─ AT+DOWNLOAD=20260326120000\n ─>│
 │<─ AT_RESP({"ok":true,...}) ─────│
 │                                  │
 │  For each file:
 │<─ FILE_START("0001.opus", 2400)─│
 │<─ DATA(seq=0, len=1024, ...) ───│
 │<─ DATA(seq=1, len=1024, ...) ───│
 │<─ ...                          │
 │<─ FILE_END(crc32=0xA1B2C3D4) ──│
 │─ FILE_ACK(0x00) ──────────────>│  CRC OK
 │                                  │
 │  (If CRC mismatch:)
 │<─ FILE_END(crc32=...) ──────────│  Retransmit
 │─ FILE_ACK(0x01) ──────────────>│  CRC NACK
 │                                  │
 │<─ TRANSFER_DONE("20260326...", 30)│
 │                                  │
```
