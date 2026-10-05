# Telemetry-Only JSON-Line Protocol

Each USB serial line is a standalone JSON object.

```json
{
  "type": "telemetry",
  "seq": 1,
  "device": "pico2",
  "firmware_version": "telemetry-only-0.1",
  "estop_state": false,
  "driver_enabled": false,
  "pan_position_steps": 0,
  "tilt_position_steps": 0,
  "limits": {
    "pan_left": false,
    "pan_right": false,
    "tilt_up": false,
    "tilt_down": false
  },
  "safe_state": true,
  "physical_outputs_enabled": false,
  "timestamp_ms": 123456
}
```

Required safety fields:

- `device` must be `pico2`.
- `firmware_version` should start with `telemetry-only`.
- `safe_state` should be `true`.
- `physical_outputs_enabled` must be `false`.
- `driver_enabled` must be `false`.

The backend treats `physical_outputs_enabled=true` as a critical condition for Phase 12.2.
