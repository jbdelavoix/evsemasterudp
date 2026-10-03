# Changelog

All notable changes to this project are documented in this file.

## [3.1.0] - 2026-10-02

### Added
- Config flow UDP autodiscovery (select charger by serial/IP, manual fallback)
- Binary sensors: vehicle connected, charging, error, emergency
- Sensors: L2/L3 V/A (disabled by default on 1-phase), session duration, gun state, current state, session energy attributes
- Sync time button
- Post-login config fetch: version, max current, language, temperature unit, start mode, brightness, nickname
- Number: screen brightness `0–100` (`0x8162`)
- Selects: language, temperature unit (`C`/`F`), start mode (`app&button` / `app` / `auto`)
- Text: charger nickname
- Weekly charge schedule (`0x810e` / `0x010e`): 7 day slots (start + uint16 duration); HA `time` / `number` entities; CLI `--schedule`
- Coordinator push updates from UDP events (2 s poll fallback)
- Fast-change protection persisted in config entry options
- Config-flow translations: `en`, `fr`, `es`, `de`, `it`, `ja`
- Confirmed SQW49 / tethered-cable state mapping from live packet captures
- `current_state` labels: `15` = stopped by EVSE, `18` = stopped by EV
- Raw UDP `wire_hex` capture in `tests/test_full.py` for protocol analysis
- Offline unit tests (`tests/test_unit.py`) and live config CLI (`tests/test_config.py`)
- Protocol notes: [`doc/protocol.md`](doc/protocol.md)
- GitHub Actions CI (Hassfest + HACS validation) and tag-driven Release workflow for HACS
- Local brand images under `custom_components/evsemasterudp/brand/` (`icon.png`, `logo.png`, dark/@2x variants)

### Fixed
- Import crash (`NameError`) from mis-indented log line in `evse_client`
- Discovery no longer marks EVSE as logged-in without password
- Keepalive: handle EVSE→App `Heading` and reply with `HeadingResponse`; stop destructive 30 s full re-login
- Config `port` wired into client/communicator
- Energy sensor uses lifetime kWh (`TOTAL_INCREASING`); session kWh is a separate measurement sensor
- Response waiters use per-command futures (no `_last_response` race)
- Broken orphan `switch.py` removed
- Hex serial validation on pack
- Charging detection: require `output_state == 1` or `power > 10` (do not treat transitional `current_state == 14` alone as charging)
- Binary sensor charging aligned with the same rule
- `GetVersionResponse` software version parsing (16-byte field on real hardware)
- `LoginResponse` now unpacks the same info payload as `Login`
- System time sync (`0x8101`): correct `action` + China-shifted timestamp (local wall clock as in the app)
- EVSE address trust: ignore phone/`0x01xx` echoes that hijacked the charger IP and made SET/GET flaky
- Session health: re-login when the UDP session goes silent (outbound Heading no longer fakes a live session; recover after overnight / lost session)
- Session health hardening: invalidate on `PasswordError`; re-login if no `SingleACStatus` for 2 min; non-blocking post-login config fetch; live sensors require `logged_in` (not just Login broadcasts)

### Changed
- Gun state labels aligned with captures: `1` available, `2` plugged, `4` plugged/locked while charging
- `iot_class` set to `local_push` (UDP event-driven updates + short poll fallback)
- `current_state` `12` labeled `standby` (not `session_active`); device info uses live brand/model and no longer copies HW into firmware

## [3.0.1] - previous

- Minor translation change

## Contributors

- [Oniric75](https://github.com/Oniric75) — original Home Assistant integration ([upstream](https://github.com/Oniric75/evsemasterudp))
- [jbdelavoix](https://github.com/jbdelavoix) — this fork: protocol validation on live SQW49 hardware, state mapping, discovery & reliability fixes (2026)
- [johnwoo-nl](https://github.com/johnwoo-nl) — EmProto reverse engineering ([emproto](https://github.com/johnwoo-nl/emproto))
