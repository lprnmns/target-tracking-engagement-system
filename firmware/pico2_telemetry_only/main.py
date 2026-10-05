"""
Telemetry-only Pico 2 firmware.

SAFETY BOUNDARY:
- No motor output.
- No servo output.
- No trigger or firing output.
- No STEP/DIR/PWM output.
- No GPIO output is initialized or driven.
- USB serial telemetry only.
"""

import json
import time

FIRMWARE_VERSION = "telemetry-only-0.1"
TELEMETRY_HZ = 2


def ticks_ms() -> int:
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def telemetry(seq: int) -> dict:
    return {
        "type": "telemetry",
        "seq": seq,
        "device": "pico2",
        "firmware_version": FIRMWARE_VERSION,
        "estop_state": False,
        "driver_enabled": False,
        "pan_position_steps": 0,
        "tilt_position_steps": 0,
        "limits": {
            "pan_left": False,
            "pan_right": False,
            "tilt_up": False,
            "tilt_down": False,
        },
        "safe_state": True,
        "physical_outputs_enabled": False,
        "timestamp_ms": ticks_ms(),
    }


def main() -> None:
    seq = 1
    delay_s = 1 / TELEMETRY_HZ
    while True:
        print(json.dumps(telemetry(seq), separators=(",", ":")))
        seq = (seq + 1) % 256
        if seq == 0:
            seq = 1
        time.sleep(delay_s)


main()
