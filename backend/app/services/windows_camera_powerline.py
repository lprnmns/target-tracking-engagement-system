"""Read/write the Windows UVC anti-flicker power-line frequency.

This helper is intentionally isolated from CameraRuntime and all actuator
services. It talks only to the selected DirectShow video-input filter through
the documented PROPSETID_VIDCAP_VIDEOPROCAMP KS property set.
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
from ctypes import POINTER, Structure, byref, c_long, c_ulong, c_void_p, cast, create_string_buffer, memmove, sizeof


POWERLINE_PROPERTY_ID = 13  # KSPROPERTY_VIDEOPROCAMP_POWERLINE_FREQUENCY
KSPROPERTY_TYPE_GET = 0x00000001
KSPROPERTY_TYPE_SET = 0x00000002
POWERLINE_LABELS = {0: "DISABLED", 1: "50HZ", 2: "60HZ", 3: "AUTO"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--set-hz", type=int, choices=(50, 60))
    return parser


def _result(*, accepted: bool, supported: bool = False, value: int | None = None, reason: str | None = None) -> dict:
    return {
        "accepted": accepted,
        "supported": supported,
        "value": value,
        "frequency_hz": 50 if value == 1 else 60 if value == 2 else None,
        "label": POWERLINE_LABELS.get(value, "UNKNOWN") if value is not None else "UNKNOWN",
        "reason": reason,
        "no_physical_command_generated": True,
    }


def main() -> int:
    args = _parser().parse_args()
    if os.name != "nt":
        print(json.dumps(_result(accepted=False, reason="WINDOWS_ONLY")))
        return 2
    try:
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
        from pygrabber.dshow_graph import FilterGraph
    except Exception as exc:
        print(json.dumps(_result(accepted=False, reason=f"DEPENDENCY_UNAVAILABLE:{exc}")))
        return 3

    class IKsPropertySet(IUnknown):
        _iid_ = GUID("{31EFAC30-515C-11D0-A9AA-00AA0061BE93}")
        _methods_ = [
            COMMETHOD(
                [], HRESULT, "Set",
                (["in"], POINTER(GUID), "guidPropSet"),
                (["in"], c_ulong, "dwPropID"),
                (["in"], c_void_p, "pInstanceData"),
                (["in"], c_ulong, "cbInstanceData"),
                (["in"], c_void_p, "pPropData"),
                (["in"], c_ulong, "cbPropData"),
            ),
            COMMETHOD(
                [], HRESULT, "Get",
                (["in"], POINTER(GUID), "guidPropSet"),
                (["in"], c_ulong, "dwPropID"),
                (["in"], c_void_p, "pInstanceData"),
                (["in"], c_ulong, "cbInstanceData"),
                # LPVOID points at caller-owned writable storage. Mark it as
                # an input pointer so comtypes passes the address itself
                # instead of allocating a pointer-to-pointer wrapper.
                (["in"], c_void_p, "pPropData"),
                (["in"], c_ulong, "cbPropData"),
                (["out"], POINTER(c_ulong), "pcbReturned"),
            ),
            COMMETHOD(
                [], HRESULT, "QuerySupported",
                (["in"], POINTER(GUID), "guidPropSet"),
                (["in"], c_ulong, "dwPropID"),
                (["out"], POINTER(c_ulong), "pTypeSupport"),
            ),
        ]

    class KSPROPERTY(Structure):
        _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong)]

    class KSPROPERTY_VIDEOPROCAMP_S(Structure):
        _fields_ = [
            ("Property", KSPROPERTY),
            ("Value", c_long),
            ("Flags", c_ulong),
            ("Capabilities", c_ulong),
        ]

    prop_set = GUID("{C6E13360-30AC-11D0-A18C-00A0C9118956}")
    graph = None
    try:
        graph = FilterGraph()
        graph.add_video_input_device(args.index)
        source = graph.get_input_device().instance
        ks = source.QueryInterface(IKsPropertySet)
        support_result = ks.QuerySupported(byref(prop_set), POWERLINE_PROPERTY_ID)
        support_value = int(support_result[0] if isinstance(support_result, tuple) else support_result)
        can_get = bool(support_value & KSPROPERTY_TYPE_GET)
        can_set = bool(support_value & KSPROPERTY_TYPE_SET)
        if not can_get:
            print(json.dumps(_result(accepted=False, supported=False, reason=f"GET_UNSUPPORTED:{support_value}")))
            return 4

        def read_value() -> int:
            payload = KSPROPERTY_VIDEOPROCAMP_S()
            payload.Property.Set = prop_set
            payload.Property.Id = POWERLINE_PROPERTY_ID
            payload.Property.Flags = KSPROPERTY_TYPE_GET
            buffer = create_string_buffer(256)
            memmove(buffer, byref(payload), sizeof(payload))
            ks.Get(
                byref(prop_set), POWERLINE_PROPERTY_ID,
                None, 0, cast(buffer, c_void_p), sizeof(buffer),
            )
            returned_payload = cast(buffer, POINTER(KSPROPERTY_VIDEOPROCAMP_S)).contents
            return int(returned_payload.Value)

        before = read_value()
        if args.set_hz is None:
            print(json.dumps(_result(accepted=True, supported=True, value=before)))
            return 0
        if not can_set:
            print(json.dumps(_result(accepted=False, supported=True, value=before, reason="SET_UNSUPPORTED")))
            return 5
        requested = 1 if args.set_hz == 50 else 2
        payload = KSPROPERTY_VIDEOPROCAMP_S()
        payload.Property.Set = prop_set
        payload.Property.Id = POWERLINE_PROPERTY_ID
        payload.Property.Flags = KSPROPERTY_TYPE_SET
        payload.Value = requested
        ks.Set(
            byref(prop_set), POWERLINE_PROPERTY_ID,
            None, 0, cast(byref(payload), c_void_p), sizeof(payload),
        )
        after = read_value()
        result = _result(
            accepted=after == requested,
            supported=True,
            value=after,
            reason=None if after == requested else f"READBACK_MISMATCH:{before}->{after}",
        )
        result["previous_value"] = before
        print(json.dumps(result))
        return 0 if after == requested else 6
    except Exception as exc:
        print(json.dumps(_result(accepted=False, reason=f"POWERLINE_CONTROL_FAILED:{type(exc).__name__}:{exc}:{traceback.format_exc()}")))
        return 7
    finally:
        if graph is not None:
            try:
                graph.remove_filters()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
