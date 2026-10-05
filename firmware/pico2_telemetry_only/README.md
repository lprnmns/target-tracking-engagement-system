# Pico 2 Telemetry-Only Firmware

This firmware is only for Phase 12.2 read-only acceptance.

## Safety Boundary

- Telemetry-only firmware.
- No motor output.
- No servo output.
- No trigger output.
- No STEP/DIR/PWM.
- No GPIO output is initialized or driven.
- USB serial telemetry only.
- PC commands are not processed in this phase.

## Install Flow

1. Install MicroPython UF2 for Raspberry Pi Pico 2.
2. Boot the Pico 2 into BOOTSEL mode and copy the MicroPython UF2.
3. Copy `main.py` to the board with Thonny or `mpremote`.
4. On Linux, check the serial port:

```bash
python -m serial.tools.list_ports
```

5. In the İSTİKLAL C2 UI, open Pico > Real Hardware Discovery.
6. Refresh ports and connect the Pico port in read-only mode.

## Expected Telemetry

The board prints newline-delimited JSON at 2 Hz. It does not read commands and does not drive outputs.

See [TELEMETRY_PROTOCOL.md](./TELEMETRY_PROTOCOL.md).
