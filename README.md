<p align="center">
  <img src="https://raw.githubusercontent.com/jbdelavoix/evsemasterudp/main/custom_components/evsemasterudp/brand/logo.png" alt="EVSE Master UDP" width="420" />
</p>

# EVSE Master UDP

Home Assistant custom integration for EV chargers that speak the **EVSE Master UDP** protocol (port **28376**).

Based on the protocol reverse-engineering from [johnwoo-nl/emproto](https://github.com/johnwoo-nl/emproto).

## Disclaimer

This integration is provided **as is**. You use it at your own risk. The authors accept no liability for damage, malfunction, warranty loss, fire, injury, or any other consequence. Confirm that your station is safe and compliant with local regulations before use.

## Safety

- Repeated start/stop cycles can wear contactors. Built-in cooldown helps but does not remove your responsibility.
- **Do not** run the official **EVSEMaster** mobile app at the same time as this integration — both fight for the same UDP session and will conflict.

## Compatibility

Supports chargers that use the official **EVSEMaster** app over UDP, including among others:

- **Morec** wallboxes
- Other brands / OEM Chinese stations that pair with EVSEMaster (validated e.g. on **SQW49**)

Official app:

- [EVSEMaster on the App Store (iOS)](https://apps.apple.com/app/evsemaster/id1474532183)
- [EVSEMaster on Google Play (Android)](https://play.google.com/store/apps/details?id=com.evsemaster.dev)

If your charger works with EVSEMaster on the LAN, this integration is likely compatible. Default UDP port: **28376**.

## Requirements

- Home Assistant **2024.1+** (HACS recommended). Brand icons in the UI need **2026.3+** (local `brand/` folder).
- Charger and Home Assistant on the **same LAN / VLAN** (no client isolation / guest Wi‑Fi)
- **UDP broadcast and unicast** on port **28376** must reach the Home Assistant host

### Home Assistant in Docker

**Important:** discovery and live status depend on **UDP broadcasts** on port **28376**.  
A default Docker **bridge** network usually **cannot** receive them — **Add integration → autodiscovery will find nothing**.

Home Assistant must sit on the **same L2 LAN** as the charger. Two setups that work:

#### Option A — host networking (simplest)

```yaml
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    network_mode: host
    volumes:
      - ./homeassistant:/config
    environment:
      - TZ=Europe/Paris
    restart: unless-stopped
    # Do NOT also publish 28376/udp via ports: with host networking
```

#### Option B — dedicated LAN IP (macvlan / ipvlan / “home-lan”)

Give the container its **own address on the home LAN** (same subnet as the charger), for example:

```yaml
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    volumes:
      - ./homeassistant:/config
    environment:
      - TZ=Europe/Paris
    restart: unless-stopped
    networks:
      portals:
      services:
      home-lan:
        ipv4_address: 192.168.1.10   # pick a free IP on the charger’s LAN

networks:
  portals:
    # ...
  services:
    # ...
  home-lan:
    # macvlan/ipvlan (or equivalent) bridged to your LAN NIC
    # driver: macvlan
    # driver_opts:
    #   parent: eth0
    # ipam:
    #   config:
    #     - subnet: 192.168.1.0/24
    #       gateway: 192.168.1.1
```

The important part is **`home-lan` + a real LAN `ipv4_address`**: HA must receive broadcasts as a peer on that subnet (same subnet as the charger). Extra compose networks (`portals`, `services`, …) are fine alongside it.

**What does not work well:**

| Setup | Result |
|-------|--------|
| `network_mode: host` (Linux) | Discovery + status OK |
| Container IP on the **home LAN** (macvlan / ipvlan / similar) | Discovery + status OK |
| Bridge + `ports: ["28376:28376/udp"]` only | Often **fails** (broadcasts not forwarded) |
| Docker Desktop **macOS / Windows** | No real LAN host networking → prefer Linux host or a VM with bridged NIC |
| Guest Wi‑Fi / client isolation / different VLAN | Broadcasts blocked → no discovery |

Also allow **UDP 28376** inbound/outbound on the host firewall.

## Installation

### HACS (recommended)

1. HACS → Integrations → Custom repositories  
2. URL: `https://github.com/jbdelavoix/evsemasterudp` — type **Integration**  
3. Install **EVSE Master UDP**  
4. Restart Home Assistant  

### Manual

Copy `custom_components/evsemasterudp/` into your Home Assistant `config/custom_components/` so that `manifest.json` is at:

`config/custom_components/evsemasterudp/manifest.json`

Restart Home Assistant.

## Find your serial (and IP) before setup

You do **not** need to guess the IP. The charger announces itself on UDP **28376**. Use the scripts from a machine on the **same network** as the charger (close the EVSEMaster app first).

```bash
# From the repository root
python tests/test_discovery.py
# Optional longer wait:
python tests/test_discovery.py --timeout 60
```

Example output:

```text
IP        : 192.168.1.50
Port      : 39576
Serial    : 8662888793459659
Brand     : EVSE
Model     : SQW49
```

Copy the **Serial** value for Home Assistant (or rely on in-UI autodiscovery).

Full login + live status + JSONL packet dump (password = EVSEMaster app; many chargers ship with **`123456`**):

```bash
EVSE_PASSWORD='123456' python tests/test_full.py --listen 45
# Or interactive password prompt:
python tests/test_full.py --listen 45
```

Captures are written under `tests/captures/`.

| Script | Purpose |
|--------|---------|
| `tests/test_unit.py` | Fast offline unit tests (protocol pack/unpack, no HA / no hardware) |
| `tests/test_config.py` | Live GET/SET brightness, temp, start mode, language, schedule… (no HA) |
| `tests/test_discovery.py` | Listen for broadcasts → print **serial** and **IP** |
| `tests/test_full.py` | Discover, authenticate, dump status / wire hex |
| `tests/test_basic.py` | Offline smoke tests (imports / socket) |

```bash
python tests/test_unit.py
# Live config (close EVSEMaster app first; reopen app to verify):
EVSE_PASSWORD='123456' python tests/test_config.py --brightness 40
EVSE_PASSWORD='123456' python tests/test_config.py --temp F --start-mode auto
EVSE_PASSWORD='123456' python tests/test_config.py --schedule all=03:00/60
EVSE_PASSWORD='123456' python tests/test_config.py --schedule mon=01:00/30,tue=off
EVSE_PASSWORD='123456' python tests/test_config.py --schedule-clear
```


## Configuration in Home Assistant

1. **Settings → Devices & services → Add integration → EVSE Master UDP**
2. A first screen explains the prerequisites and asks you to **press Submit / Valider** to start **autodiscovery** (~10 s on UDP **28376**).
3. Select your charger from the list (or choose manual serial entry / « Search again »).
4. Enter the **password** from the EVSEMaster app (factory default on many chargers: **`123456`**).
5. Optional: friendly name, UDP port (default `28376`).

Close the EVSEMaster mobile app first — it conflicts with the same UDP session.

If the list is empty: same LAN/VLAN as the charger, UDP 28376 open, and for Docker use **host networking** or a **LAN IP** (macvlan / `home-lan`) — see above.

| Field | Description |
|-------|-------------|
| Serial | Device serial (from discovery list or manual entry) |
| Password | App password (often factory default **`123456`**; stored in the config entry) |
| Port | UDP listen port (default `28376`) |
| Name | Friendly name for entities |

Entities refresh from UDP events with a **2 s** coordinator fallback. Fast-change protection (minutes between stop→start) is a number entity, not a config-flow option.

<p align="center">
  <img src="https://raw.githubusercontent.com/jbdelavoix/evsemasterudp/main/img/ha_device_panel.jpg" alt="Home Assistant device view" width="780" />
</p>

## Entities

Names below use the friendly name chosen at setup (default **EVSEMaster**). Entity IDs follow Home Assistant slug rules (e.g. `sensor.evsemaster_power`).

### Sensors

| Entity | Unit | Notes |
|--------|------|--------|
| State | — | Meta state: `IDLE`, `PLUGGED_IN`, `CHARGING`, `ERROR`, `EMERGENCY`, `OFFLINE`, … |
| Charge Status | — | `charging` / `not_charging` / `soft_protection` (+ cooldown attribute) |
| Power | W | Instantaneous power |
| Current | A | Phase L1 (primary) |
| Current L2 / L3 | A | Disabled by default on 1-phase chargers |
| Voltage | V | Phase L1 (primary) |
| Voltage L2 / L3 | V | Disabled by default on 1-phase chargers |
| Energy | kWh | Lifetime counter (`total_increasing`) |
| Session Energy | kWh | Energy for the current / last session |
| Session Duration | s | Session duration |
| Temperature Inner | °C | Internal temperature |
| Temperature Outer | °C | External / plug area temperature |
| Gun State | — | `available` / `plugged` / `plugged_locked` / … |
| Current State | — | Protocol state: `standby` / `charging` / `stopped_by_evse` / `stopped_by_ev` / … |

### Binary sensors

| Entity | On when |
|--------|---------|
| Vehicle Connected | Cable plugged into the vehicle (`gun_state` ≥ 2, or charging signals) |
| Charging | Delivering power (`output_state == 1` or power > 10 W) |
| Error | Fault codes reported by the charger |
| Emergency | Emergency stop active (mapped from `emergency_btn_state`) |

### Buttons

| Entity | Action |
|--------|--------|
| Start Charge | Start charging (respects fast-change cooldown) |
| Stop Charge | Stop charging |
| Sync Time | Push Home Assistant / host time to the charger |

### Numbers

| Entity | Unit | Notes |
|--------|------|--------|
| Max Current | A | Configurable charge current limit (6 A … charger max) |
| Screen Brightness | % | Display brightness `0–100` (protocol `0x8162`) |
| Fast Change Protection | min | Cooldown after a stop before the next start is allowed (`0` = off) |
| Schedule *Day* Duration | min | Weekly window length (`0` = off, up to `1439` = 23h59). Protocol `0x810e` |

### Times

| Entity | Notes |
|--------|--------|
| Schedule *Day* Start | Weekly charge window start (Mon–Sun). Empty / unavailable when the day slot is off |

### Selects

| Entity | Options |
|--------|---------|
| Language | `english` / `italian` / `german` / `french` / `spanish` / `hebrew` |
| Temperature Unit | `C` / `F` |
| Start Mode | `app&button` / `app` / `auto` |

### Text

| Entity | Notes |
|--------|--------|
| Nickname | Charger display / app name (max 32 chars) |

## Features

- UDP autodiscovery in the config flow (plus manual serial entry)
- Password authentication (factory default often `123456`)
- Live status over UDP with short coordinator fallback
- Session stop reason labels where the charger reports them

## Automation examples

### Off-peak start

```yaml
automation:
  - alias: "EVSE charge off-peak"
    trigger:
      - platform: time
        at: "22:30:00"
    condition:
      - condition: state
        entity_id: binary_sensor.evsemaster_vehicle_connected
        state: "on"
    action:
      - service: button.press
        target:
          entity_id: button.evsemaster_start_charge
```

### Stop from vehicle SoC

```yaml
automation:
  - alias: "Stop charge at 80%"
    trigger:
      - platform: numeric_state
        entity_id: sensor.vehicle_battery_level
        above: 80
    action:
      - service: button.press
        target:
          entity_id: button.evsemaster_stop_charge
```

Entity IDs depend on the friendly name chosen at setup.

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| No discovery / empty list on Add integration | Same LAN as the charger; EVSEMaster app closed; UDP 28376 open; **Docker: host networking or LAN IP (macvlan)** — bridge/`ports:` alone is not enough |
| Auth failed | Password from EVSEMaster app (try factory default **`123456`**); serial matches discovery |
| Works then drops | App opened in parallel; Wi‑Fi isolation; host firewall |
| Entities stuck | Reload integration; confirm broadcasts with `tests/test_discovery.py` |

## Protocol

UDP framing, commands, config, schedule and time sync notes:

- **[doc/protocol.md](doc/protocol.md)** — EmProto / EVSE Master UDP spec (from captures + [emproto](https://github.com/johnwoo-nl/emproto))

## Support

- Issues: https://github.com/jbdelavoix/evsemasterudp/issues  
- Changelog: [CHANGELOG.md](CHANGELOG.md)  
- Upstream project: https://github.com/Oniric75/evsemasterudp  

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- Protocol reverse engineering: [johnwoo-nl/emproto](https://github.com/johnwoo-nl/emproto)  
- Original Home Assistant integration: [Oniric75/evsemasterudp](https://github.com/Oniric75/evsemasterudp)  
- This fork — live hardware validation, discovery, Docker/UDP docs, and state-mapping work: [jbdelavoix](https://github.com/jbdelavoix)  

---

<details>
<summary>Developer notes</summary>

### Layout

```
custom_components/evsemasterudp/
├── __init__.py
├── manifest.json
├── config_flow.py
├── evse_client.py
├── sensor.py / binary_sensor.py / button.py / number.py
├── translations/   # en, fr, es, de, it, ja
├── brand/          # icon.png + logo.png (HA / HACS)
└── protocol/
    ├── communicator.py
    ├── datagram.py
    └── datagrams.py
tests/
├── test_basic.py
├── test_discovery.py
└── test_full.py
```

### Protocol

- Default port: **28376** UDP  
- Discovery via broadcast; session kept with Heading / HeadingResponse  
- Password sent in cleartext in the protocol (same as the official app); the setup form shows it in clear as well (factory default is often `123456`)

### Release

1. Bump `custom_components/evsemasterudp/manifest.json` version (must match the tag)  
2. Update `CHANGELOG.md` with a `## [X.Y.Z]` section  
3. Push `main` — CI runs Hassfest + HACS validation  
4. Tag and push (creates the GitHub Release via `.github/workflows/release.yml`) :

```bash
git tag 3.1.0
git push origin main
git push origin 3.1.0
```

HACS picks up the new GitHub Release automatically for installed users.  

</details>
