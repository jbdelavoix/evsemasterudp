"""EVSE configuration option maps (no Home Assistant dependency)."""
from __future__ import annotations

LANGUAGE_OPTIONS = {
    "english": 1,
    "italian": 2,
    "german": 3,
    "french": 4,
    "spanish": 5,
    "hebrew": 6,
}
LANGUAGE_BY_CODE = {v: k for k, v in LANGUAGE_OPTIONS.items()}

TEMP_UNIT_OPTIONS = {
    "C": 1,
    "F": 2,
}
TEMP_UNIT_BY_CODE = {v: k for k, v in TEMP_UNIT_OPTIONS.items()}

# Start mode (0x810d). App labels (no standalone "button"):
# 0 = app&button, 1 = app, 2 = auto
START_MODE_OPTIONS = {
    "app&button": 0,
    "app": 1,
    "auto": 2,
}
START_MODE_BY_CODE = {v: k for k, v in START_MODE_OPTIONS.items()}

# Shell-safe alias (bash treats & as background).
START_MODE_CLI_OPTIONS = {
    **START_MODE_OPTIONS,
    "app_and_button": 0,
}
