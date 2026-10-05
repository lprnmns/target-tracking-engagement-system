"""In-memory single-cockpit operation state.

This service is deliberately small: it owns operator intent, while physical
outputs remain exclusively under CommandGateway.  State is reset on process
restart, so a stale browser cannot silently resume a previous tracking run.
"""

from __future__ import annotations

import time
import threading

from app.schemas.operation import (
    FirePermission,
    OperationState,
    OperationTargetSelection,
    OperationUpdate,
)


class OperationService:
    def __init__(self) -> None:
        self._state = OperationState()
        self._lock = threading.RLock()

    def state(self) -> OperationState:
        with self._lock:
            return self._state.model_copy()

    def update(self, update: OperationUpdate) -> OperationState:
        with self._lock:
            changes = update.model_dump(exclude_none=True)
            tracking_explicit = update.tracking_requested is not None
            next_control_mode = changes.get("control_mode", self._state.control_mode)
            # A policy edit invalidates the selected detection as well as the
            # current tracking request. Keeping only the visible UI state in
            # sync is not sufficient: Gateway reads this backend state.
            if "target_policy" in changes and changes["target_policy"] != self._state.target_policy:
                changes.update(
                    {
                        "selected_logical_target_id": None,
                        "selected_detection_id": None,
                        "selected_detection_kind": None,
                        "selected_body_track_id": None,
                    }
                )
                if not tracking_explicit and "control_mode" not in changes:
                    changes["tracking_requested"] = bool(
                        next_control_mode.value == "AUTONOMOUS" and self._state.tracking_requested
                    )
            if "control_mode" in changes and changes["control_mode"] != self._state.control_mode:
                # AUTONOMOUS means acquire the first eligible target without
                # requiring an operator click.  A caller may still explicitly
                # send tracking_requested=false to pause it.
                if not tracking_explicit:
                    changes["tracking_requested"] = next_control_mode.value == "AUTONOMOUS"
            self._state = self._state.model_copy(
                update={**changes, "configured": True, "updated_at": time.time()}
            )
            return self._state.model_copy()

    def select_target(self, selection: OperationTargetSelection, *, keep_tracking: bool = False) -> OperationState:
        with self._lock:
            self._state = self._state.model_copy(
                update={
                    "selected_logical_target_id": selection.logical_target_id,
                    "selected_detection_id": selection.detection_id,
                    "selected_detection_kind": selection.detection_kind,
                    "selected_body_track_id": selection.body_track_id,
                    # Clicking another target during an active autonomous run
                    # is a handoff, not an implicit stop. A first selection
                    # still requires the visible TAKIBI BAŞLAT action.
                    "tracking_requested": bool(keep_tracking),
                    "configured": True,
                    "updated_at": time.time(),
                }
            )
            return self._state.model_copy()

    def clear_target(self) -> OperationState:
        with self._lock:
            self._state = self._state.model_copy(
                update={
                    "selected_logical_target_id": None,
                    "selected_detection_id": None,
                    "selected_detection_kind": None,
                    "selected_body_track_id": None,
                    "tracking_requested": False,
                    "updated_at": time.time(),
                }
            )
            return self._state.model_copy()

    def set_fire_permission(self, permission: FirePermission) -> OperationState:
        # Gateway-owned ARM/0/1 telemetry must not turn a legacy caller into a
        # UI-configured operation session. The cockpit uses ``update`` when it
        # explicitly changes this permission and therefore sets configured.
        with self._lock:
            self._state = self._state.model_copy(
                update={"fire_permission": FirePermission(permission), "updated_at": time.time()}
            )
            return self._state.model_copy()
