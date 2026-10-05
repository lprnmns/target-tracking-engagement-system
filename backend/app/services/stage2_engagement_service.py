"""Turns visual hit confirmation into an auditable A2 round result.

It has no serial dependency.  It only consumes the HitConfirmation state
written after CommandGateway ACKs a shot, so an operator cannot type an
arbitrary number of hits into a live competition round.
"""

from __future__ import annotations

import time

from app.schemas.mission import MissionSnapshot, Stage2RoundCompleteRequest
from app.schemas.stage2_engagement import Stage2EngagementStatus
from app.schemas.tracking import EngagementStatus


class Stage2EngagementService:
    def __init__(self) -> None:
        self._fired: set[int] = set()
        self._confirmed: set[int] = set()
        self._pending: set[int] = set()
        self._reengage: set[int] = set()
        self._status = Stage2EngagementStatus()

    def reset(self, current_round: int = 1) -> Stage2EngagementStatus:
        self._fired.clear()
        self._confirmed.clear()
        self._pending.clear()
        self._reengage.clear()
        self._status = Stage2EngagementStatus(current_round=current_round)
        return self._status

    def register_shot(self, balloon_track_id: int, current_round: int) -> Stage2EngagementStatus:
        self._fired.add(balloon_track_id)
        self._pending.add(balloon_track_id)
        self._status = self._build(current_round)
        return self._status

    def observe(self, confirmations: EngagementStatus, current_round: int) -> Stage2EngagementStatus:
        pending: set[int] = set()
        confirmed = set(self._confirmed)
        reengage: set[int] = set()
        for record in confirmations.records:
            if record.balloon_track_id not in self._fired:
                continue
            if record.state.value == "PENDING_CONFIRMATION":
                pending.add(record.balloon_track_id)
            elif record.state.value == "CONFIRMED_HIT":
                confirmed.add(record.balloon_track_id)
            elif record.state.value == "REENGAGE":
                reengage.add(record.balloon_track_id)
        self._pending = pending
        self._confirmed = confirmed
        self._reengage = reengage
        self._status = self._build(current_round)
        return self._status

    def status(self) -> Stage2EngagementStatus:
        return self._status

    def close_round(self, mission) -> MissionSnapshot:
        state = mission.state
        if state.active_stage != "stage2":
            raise ValueError("STAGE2_NOT_ACTIVE")
        self._status = self._build(state.stage2_round)
        if self._pending:
            raise ValueError("A2_ROUND_CONFIRMATION_PENDING")
        snapshot = mission.complete_stage2_round(Stage2RoundCompleteRequest(confirmed_hits=min(3, len(self._confirmed))))
        self.reset(snapshot.state.stage2_round)
        return snapshot

    def _build(self, current_round: int) -> Stage2EngagementStatus:
        pending = sorted(self._pending)
        confirmed = sorted(self._confirmed)
        reengage = sorted(self._reengage)
        reasons: list[str] = []
        if pending:
            reasons.append("A2_ROUND_CONFIRMATION_PENDING")
        return Stage2EngagementStatus(
            current_round=current_round,
            fired_track_ids=sorted(self._fired),
            pending_track_ids=pending,
            confirmed_track_ids=confirmed,
            reengage_track_ids=reengage,
            ready_to_close=not pending,
            reason_codes=reasons,
            updated_at=time.time(),
        )
