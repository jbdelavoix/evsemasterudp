# EmProto / EVSE Master UDP — protocol notes

Unofficial specification of the UDP protocol used by the **EVSEMaster** mobile app
and compatible AC chargers (e.g. Morec / SQW49 OEM).

**Transport:** UDP, typically port **28376**, LAN broadcast + unicast.  
**Byte order:** big-endian unless noted.  
**Status:** reverse-engineered; validated on live SQW49 hardware and iPhone
`rvictl` captures where marked. Gaps and app-only quirks remain.

Sources:

- [johnwoo-nl/emproto](https://github.com/johnwoo-nl/emproto)
- This integration’s packet captures under `tests/captures/`
- Live GET/SET against hardware (`tests/test_config.py`, `tests/test_full.py`)

---

## 1. Datagram framing

Every message is one EmProto datagram:

| Offset | Size | Field |
|--------|------|--------|
| 0 | 2 | Header `0x0601` |
| 2 | 2 | Total length (header + payload + checksum + tail) |
| 4 | 1 | Key type (usually `0x00`) |
| 5 | 8 | Device serial (raw 8 bytes; displayed as 16 hex digits) |
| 13 | 6 | Password ASCII (NUL-padded; often empty on EVSE→App) |
| 19 | 2 | Command |
| 21 | N | Payload |
| 21+N | 2 | Checksum = `(sum of all bytes except last 4) mod 0xFFFF` |
| 23+N | 2 | Tail `0x0F02` |

Fixed overhead = **25** bytes (`21` header-ish + `4` checksum/tail).  
Payload length = `total_length - 25`.

### Direction hint

| Command range | Typical direction |
|---------------|-------------------|
| `0x0001`–`0x00FF` | EVSE → App (events / responses) |
| `0x8001`–`0x80FF` | App → EVSE (session / charge) |
| `0x8100`–`0x81FF` | App → EVSE (config SET/GET) |
| `0x0100`–`0x01FF` | EVSE → App (config responses) |

Many config pairs are `0x81xx` ↔ `0x01xx` with the same low byte.

---

## 2. Session overview

```
EVSE broadcasts Login (0x0001) / status on the LAN
        │
App (or HA) discovers serial + IP
        │
App → RequestLogin (0x8002) + password
        │
EVSE → LoginResponse (0x0002)  or PasswordError (0x0155)
        │
App → LoginConfirm (0x8001)
        │
Keepalive: EVSE Heading (0x0003) ↔ App HeadingResponse (0x8003)
        │
Status push: SingleACStatus (0x0004), charging status (0x0005), …
        │
Config: App 0x81xx  ↔  EVSE 0x01xx
```

**Conflict:** only one UDP client should own the session. Running the official
app and this integration at the same time makes SET/GET flaky (phone may echo
`0x01xx` and look like the charger).

**Address trust:** learn the EVSE IP only from true EVSE origins (`Login`,
`Heading`, `SingleACStatus`, …), not from every `0x01xx` source.

---

## 3. Command catalogue

### 3.1 Discovery / session

| Cmd | Name | Dir | Notes |
|-----|------|-----|--------|
| `0x0001` | Login | EVSE→App | Discovery broadcast: brand, model, HW, limits |
| `0x0002` | LoginResponse | EVSE→App | Same info payload shape as Login |
| `0x8002` | RequestLogin | App→EVSE | Auth start |
| `0x8001` | LoginConfirm | App→EVSE | After successful login |
| `0x0155` | PasswordErrorResponse | EVSE→App | Bad password |
| `0x0003` | Heading | EVSE→App | Keepalive (real EVSEs send this) |
| `0x8003` | HeadingResponse | App→EVSE | Ack keepalive |

### 3.2 Status / charge

| Cmd | Name | Dir | Notes |
|-----|------|-----|--------|
| `0x0004` | SingleACStatus | EVSE→App | V/A/W, temps, gun/output/current state |
| `0x8004` | SingleACStatusResponse | App→EVSE | Ack |
| `0x0005` | SingleACChargingStatusPublicAuto | EVSE→App | Session fields while charging |
| `0x0006` | SingleACChargingStatusResponse | App→EVSE | Ack |
| `0x8007` / `0x0007` | ChargeStart / Response | App↔EVSE | Start (optional reservation fields) |
| `0x8008` / `0x0008` | ChargeStop / Response | App↔EVSE | Stop |
| `0x0009` | CurrentChargeRecord | EVSE→App | Session / record update |
| `0x8009` | RequestChargeStatusRecord | App→EVSE | History request |
| `0x000A` | UploadLocalChargeRecord | EVSE→App | Local record upload |
| `0x800D` | CurrentChargeRecordResponse | App→EVSE | Ack |

### 3.3 Configuration (validated)

| App cmd | EVSE cmd | Name | Payload sketch |
|---------|----------|------|----------------|
| `0x8101` | `0x0101` | System time | See §4 |
| `0x8106` | `0x0106` | Version | HW/SW strings, feature flags |
| `0x8107` | `0x0107` | Max output current | `action`, amps (`6…32`) |
| `0x8108` | `0x0108` | Nickname | `action`, UTF-8 name ≤32, padded |
| `0x810D` | `0x010D` | Start mode (offline charge) | `action`, status — §5 |
| `0x810E` | `0x010E` | Weekly schedule | §6 |
| `0x810F` | `0x010F` | Language | `action`, language — §5 |
| `0x8112` | `0x0112` | Temperature unit | `action`, unit — §5 |
| `0x8162` | `0x0162` | Screen brightness | §7 |

Also seen on the wire but lightly decoded here: fees `0x8104`/`0x8105`, etc.

### 3.4 Typical `action` byte

For most `0x81xx` config commands:

| Value | Meaning |
|-------|---------|
| `1` | SET |
| `2` | GET |

**Exception — brightness (`0x8162`):** captured traffic uses `1`≈GET, `2`=SET
inside a slightly different layout (see §7).

---

## 4. System time (`0x8101` / `0x0101`)

### App → EVSE (SET), 16-byte payload

| Offset | Size | Field |
|--------|------|--------|
| 0 | 1 | `action` = `1` (SET) |
| 1 | 4 | Timestamp (uint32 BE) |
| 5 | 11 | Zero padding |

### EVSE → App

| Offset | Size | Field |
|--------|------|--------|
| 0 | 1 | `action` (often `0`) |
| 1 | 4 | Timestamp (uint32 BE) |

### Timestamp semantics (important)

The charger’s wall clock behaves like **Asia/Shanghai (UTC+8, no DST)**.

The official app does **not** send plain UTC `time()`. It sends a shifted Unix
timestamp such that:

```text
datetime.fromtimestamp(ts, tz=Asia/Shanghai)
    == user's local wall-clock Y-M-D h:m:s
```

So if it is `00:01` in Paris, the app sends a `ts` whose China interpretation
is also `00:01`. Schedule HH:MM values are compared against that same wall clock
(not labelled UTC/GMT in the protocol).

Implementation helper: `protocol/em_time.py` (`date_to_em_timestamp`).

---

## 5. Simple config maps

### Language (`0x810F`)

| Code | Language |
|------|----------|
| 1 | English |
| 2 | Italian |
| 3 | German |
| 4 | French |
| 5 | Spanish |
| 6 | Hebrew |

Payload: `[action, language]`.

### Temperature unit (`0x8112`)

| Code | Unit |
|------|------|
| 1 | °C |
| 2 | °F |

Payload: `[action, unit]`.

### Start mode / offline charge (`0x810D`)

App labels (no standalone “button-only”):

| Code | Label |
|------|--------|
| 0 | app&button |
| 1 | app |
| 2 | auto |

Payload: `[action, status]` (GET often `[2, 0]`).

### Max current (`0x8107`)

Payload: `[action, amps]` with amps typically `6…32` (capped by charger max).

### Nickname (`0x8108`)

Payload: `[action]` + up to 32 bytes name (NUL-padded / truncated).

---

## 6. Weekly charge schedule (`0x810E` / `0x010E`)

Also called **AlarmChargeStrategy** in EmProto sources.

### Payload

| Offset | Size | Field |
|--------|------|--------|
| 0 | 1 | `action` (`1`=SET, `2`=GET) |
| 1 | 63 | Seven day slots × 9 bytes |

Day index `0…6` = **Monday … Sunday**.

### Slot (9 bytes)

| Offset | Size | Field |
|--------|------|--------|
| 0 | 1 | `mode` — `1` = off, `3` = weekly on |
| 1 | 1 | Start hour `0…23` |
| 2 | 1 | Start minute `0…59` |
| 3 | 2 | Duration minutes (uint16 BE) |
| 5 | 4 | Flags (usually `ff ff ff ff`) |

End time is **not** stored; UI end = start + duration (may wrap midnight).

**Example** (start `23:59`, duration `22h58` = 1378 = `0x0562`):

```text
03 17 3b 05 62 ff ff ff ff
```

→ `23:59 → 22:57 / 22h58`, weekly.

**Off slot** (as sent by the app):

```text
01 00 00 00 00 ff ff ff ff
```

### Notes

- GET requests from the app often pad with garbage; trust **SET** payloads and
  clean EVSE responses.
- App « unique » / one-shot scheduling is **not** implemented via this mode byte
  (`mode=2` still shows as a normal schedule). One-shot appears broken in the
  app on tested hardware; not supported here.
- Implementation: `protocol/schedule.py`.

---

## 7. Screen brightness (`0x8162` / `0x0162`)

Captured from EVSEMaster iOS traffic.

### SET (App → EVSE)

```text
00 02 <brightness 0…100>
```

### GET probe (App → EVSE)

Longer probe seen in captures, e.g. starting `00 01 …` (see code).

### Response (EVSE → App)

Often `[0, action, brightness, …]`.

---

## 8. Meta state (SQW49 / tethered cable)

Derived from `SingleACStatus` fields (confirmed on captures):

| Field | Useful values |
|-------|----------------|
| `gun_state` | `1` available (cable out), `2` plugged, `4` plugged/locked while charging |
| `output_state` | `0` idle, `1` charging (power on), `2` ready |
| `current_state` | e.g. `12` standby, `13` finished, `14` charging/handshake, `15` stopped by EVSE, `18` stopped by EV |
| `emergency_btn_state` | `0` pressed / emergency, `1` OK |

**Charging (power delivery):** treat as charging only if
`output_state == 1` **or** `power > 10 W`. Do **not** treat transitional
`current_state == 14` alone as charging.

---

## 9. ChargeStart reservation (incomplete)

`0x8007` includes reservation-related fields (`reservation_date`, flags).
This may be how a true one-shot reservation would work, but it was **not**
successfully exercised via the app on the tested charger. Treat as WIP.

---

## 10. Captures & tooling

| Path | Content |
|------|---------|
| `tests/captures/brightness.pcap` | Brightness + misc config |
| `tests/captures/schedule.pcap` | Schedule SET/GET + system time |
| `tests/test_unit.py` | Offline pack/unpack tests |
| `tests/test_config.py` | Live GET/SET without Home Assistant |
| `tests/test_discovery.py` | Listen for serial/IP |
| `tests/test_full.py` | Discover, login, dump / wire hex |

Close the EVSEMaster app before live SET tests; reopen it afterwards to verify
values on the charger UI.

---

## 11. Implementation map

| Concern | Code |
|---------|------|
| Frame encode/decode | `protocol/datagram.py` |
| Commands | `protocol/datagrams.py` |
| UDP session / EVSE object | `protocol/communicator.py` |
| Config enums | `protocol/config_maps.py` |
| China-shifted time | `protocol/em_time.py` |
| Weekly schedule | `protocol/schedule.py` |
| Home Assistant client | `evse_client.py` |
