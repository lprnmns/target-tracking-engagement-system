import json
import time
from pathlib import Path

from app.schemas.mission import (
    STAGE1_REQUIRED_TARGETS,
    MissionScore,
    MissionSnapshot,
    MissionState,
    MissionUpdate,
    Stage1Event,
    Stage1HitRequest,
    Stage1PlanRequest,
    Stage1Target,
    Stage1WrongTargetRequest,
    Stage2RoundCompleteRequest,
    Stage2RoundEvent,
    Stage3RoundCompleteRequest,
    Stage3RoundEvent,
)
from app.services.storage_paths import project_root


class MissionService:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (project_root() / "config" / "runtime" / "mission_state.active.json")
        self.state = MissionState()
        self.last_event: tuple[str, dict] | None = None
        self._load()

    def snapshot(self) -> MissionSnapshot:
        return MissionSnapshot(state=self.state, score=self._score())

    def update(self, update: MissionUpdate | dict) -> MissionSnapshot:
        data = update.model_dump(exclude_none=True) if hasattr(update, "model_dump") else (update if isinstance(update, dict) else update.__dict__)
        stage1_legacy_fields = {
            "stage1_hits",
            "stage1_wrong_hits",
            "stage1_order",
        }
        stage2_legacy_fields = {"stage2_round", "stage2_hits"}
        stage3_legacy_fields = {"stage3_round", "stage3_hits", "stage3_friend_or_miss_penalties"}
        if stage1_legacy_fields & set(data):
            raise ValueError("STAGE1_EVENT_API_REQUIRED")
        if stage2_legacy_fields & set(data):
            raise ValueError("STAGE2_EVENT_API_REQUIRED")
        if stage3_legacy_fields & set(data):
            raise ValueError("STAGE3_EVENT_API_REQUIRED")
        next_stage = data.get("active_stage", self.state.active_stage)
        if data.get("timer_running") is True and next_stage == "stage1":
            raise ValueError("STAGE1_START_ENDPOINT_REQUIRED")
        if self.state.active_stage == "stage1" and next_stage != "stage1":
            # Aşama 2/3 Aşama 1 yarışma saatinin durumundan bağımsızdır.
            data["timer_running"] = False
        self.state = self.state.model_copy(update={**data, "updated_at": time.time()})
        self._persist()
        payload = self.snapshot().model_dump(mode="json")
        self.last_event = ("mission.updated", payload)
        return self.snapshot()

    def reset(self) -> MissionSnapshot:
        self.state = MissionState()
        self._persist()
        payload = self.snapshot().model_dump(mode="json")
        self.last_event = ("mission.reset", payload)
        return self.snapshot()

    def configure_stage1_plan(self, request: Stage1PlanRequest) -> MissionSnapshot:
        if self.state.active_stage != "stage1":
            raise ValueError("STAGE1_NOT_ACTIVE")
        if self.state.timer_running or self.state.stage1_order_locked:
            raise ValueError("STAGE1_PLAN_LOCKED")
        order = list(request.order)
        if set(order) != set(STAGE1_REQUIRED_TARGETS) or len(set(order)) != len(STAGE1_REQUIRED_TARGETS):
            raise ValueError("STAGE1_PLAN_TARGET_SET_INVALID")
        if order[0] != "Balistik Füze":
            raise ValueError("STAGE1_FIRST_TARGET_MUST_BE_BALLISTIC_MISSILE")
        self.state = self.state.model_copy(update={"stage1_order": order, "updated_at": time.time()})
        return self._record_event("mission.stage1_plan_configured")

    def start_stage1(self) -> MissionSnapshot:
        if self.state.active_stage != "stage1":
            raise ValueError("STAGE1_NOT_ACTIVE")
        if self.state.timer_running or self.state.stage1_order_locked:
            raise ValueError("STAGE1_ALREADY_STARTED")
        self.configure_stage1_plan(Stage1PlanRequest(order=self.state.stage1_order))
        self.state = self.state.model_copy(update={"stage1_order_locked": True, "timer_running": True, "updated_at": time.time()})
        return self._record_event("mission.stage1_started")

    def record_stage1_hit(self, request: Stage1HitRequest) -> MissionSnapshot:
        self._require_stage1_started()
        if self.state.elapsed_s >= 300:
            raise ValueError("STAGE1_TIME_EXPIRED")
        expected = self._stage1_next_target()
        if expected is None:
            raise ValueError("STAGE1_ALL_TARGETS_COMPLETED")
        if request.target != expected:
            return self.record_stage1_wrong_target(Stage1WrongTargetRequest(target=request.target))
        event = Stage1Event(kind="hit", target=request.target, score_awarded=request.score_awarded, elapsed_s=self.state.elapsed_s)
        completed = [*self.state.stage1_completed_targets, request.target]
        raw_points = min(80, self.state.stage1_raw_points + request.score_awarded)
        self.state = self.state.model_copy(
            update={
                "stage1_completed_targets": completed,
                "stage1_raw_points": raw_points,
                "stage1_hits": len(completed),
                "stage1_events": [*self.state.stage1_events, event][-100:],
                "updated_at": time.time(),
            }
        )
        return self._record_event("mission.stage1_hit_recorded")

    def record_stage1_wrong_target(self, request: Stage1WrongTargetRequest) -> MissionSnapshot:
        self._require_stage1_started()
        event = Stage1Event(kind="wrong_target", target=request.target, penalty=5, elapsed_s=self.state.elapsed_s)
        self.state = self.state.model_copy(
            update={
                "stage1_wrong_hits": self.state.stage1_wrong_hits + 1,
                "stage1_penalty_points": self.state.stage1_penalty_points + 5,
                "stage1_events": [*self.state.stage1_events, event][-100:],
                "updated_at": time.time(),
            }
        )
        return self._record_event("mission.stage1_wrong_target_recorded")

    def complete_stage2_round(self, request: Stage2RoundCompleteRequest) -> MissionSnapshot:
        if self.state.active_stage != "stage2":
            raise ValueError("STAGE2_NOT_ACTIVE")
        if self.state.stage2_failed:
            raise ValueError("STAGE2_FAILED_AFTER_THREE_ZERO_HIT_ROUNDS")
        if self.state.stage2_completed_rounds >= 4:
            raise ValueError("STAGE2_ALL_ROUNDS_COMPLETED")
        points = {0: -5, 1: 5, 2: 15, 3: 30}[request.confirmed_hits]
        zero_streak = self.state.stage2_zero_hit_streak + 1 if request.confirmed_hits == 0 else 0
        failed = zero_streak >= 3
        event = Stage2RoundEvent(
            round_number=self.state.stage2_completed_rounds + 1,
            confirmed_hits=request.confirmed_hits,
            points=points,
            zero_hit_streak=zero_streak,
        )
        completed = self.state.stage2_completed_rounds + 1
        self.state = self.state.model_copy(
            update={
                "stage2_round": min(4, completed + 1),
                "stage2_completed_rounds": completed,
                "stage2_hits": self.state.stage2_hits + request.confirmed_hits,
                "stage2_round_events": [*self.state.stage2_round_events, event],
                "stage2_zero_hit_streak": zero_streak,
                "stage2_failed": failed,
                "updated_at": time.time(),
            }
        )
        return self._record_event("mission.stage2_round_completed")

    def complete_stage3_round(self, request: Stage3RoundCompleteRequest) -> MissionSnapshot:
        if self.state.active_stage != "stage3":
            raise ValueError("STAGE3_NOT_ACTIVE")
        if self.state.stage3_failed:
            raise ValueError("STAGE3_FAILED_AFTER_THREE_ENEMY_MISSES")
        if self.state.stage3_completed_rounds >= 8:
            raise ValueError("STAGE3_ALL_ROUNDS_COMPLETED")
        points = {"f16": 30, "helicopter": 20, "ballistic_missile": 20, "mini_micro_uav": 10}[request.enemy_class] if request.enemy_hit else 0
        penalty = 10 if request.friend_hit or not request.enemy_hit else 0
        miss_streak = 0 if request.enemy_hit else self.state.stage3_miss_streak + 1
        failed = miss_streak >= 3
        event = Stage3RoundEvent(
            round_number=self.state.stage3_completed_rounds + 1,
            enemy_class=request.enemy_class,
            enemy_hit=request.enemy_hit,
            friend_hit=request.friend_hit,
            points=points,
            penalty=penalty,
            miss_streak=miss_streak,
        )
        completed = self.state.stage3_completed_rounds + 1
        self.state = self.state.model_copy(
            update={
                "stage3_round": min(8, completed + 1),
                "stage3_completed_rounds": completed,
                "stage3_hits": self.state.stage3_hits + int(request.enemy_hit),
                "stage3_friend_or_miss_penalties": self.state.stage3_friend_or_miss_penalties + penalty,
                "stage3_round_events": [*self.state.stage3_round_events, event],
                "stage3_miss_streak": miss_streak,
                "stage3_failed": failed,
                "updated_at": time.time(),
            }
        )
        return self._record_event("mission.stage3_round_completed")

    def markdown(self) -> str:
        snapshot = self.snapshot()
        state = snapshot.state
        score = snapshot.score
        return "\n".join([
            "# Competition Mission Evidence",
            "",
            f"- Active stage: {state.active_stage}",
            f"- Elapsed: {state.elapsed_s}s",
            f"- Remaining stage-1 time: {score.remaining_s}s",
            f"- Stage 1 score estimate: {score.stage1_score}",
            f"- Stage 2 score estimate: {score.stage2_score}",
            f"- Stage 3 score estimate: {score.stage3_score}",
            f"- Active score estimate: {score.active_score}",
            f"- Stage 1 order: {', '.join(state.stage1_order)}",
            f"- Stage 2 round/hits: {state.stage2_round}/{state.stage2_hits}",
            f"- Stage 3 round/hits/penalties: {state.stage3_round}/{state.stage3_hits}/{state.stage3_friend_or_miss_penalties}",
            "",
            "This file is generated from operator mission state. It does not enable physical commands.",
            "",
        ])

    def json(self) -> str:
        return self.snapshot().model_dump_json(indent=2)

    def _score(self) -> MissionScore:
        remaining = max(0, 300 - self.state.elapsed_s)
        raw = min(80, self.state.stage1_raw_points)
        bonus = round(20 * remaining / 300) if raw >= 80 and len(self.state.stage1_completed_targets) == 4 else 0
        stage1 = max(0, raw - self.state.stage1_penalty_points + bonus)
        stage2_round_scores = [event.points for event in self.state.stage2_round_events]
        stage2 = 0 if self.state.stage2_failed else max(0, sum(stage2_round_scores))
        stage3_round_scores = [event.points - event.penalty for event in self.state.stage3_round_events]
        stage3 = 0 if self.state.stage3_failed else max(0, sum(stage3_round_scores))
        active = stage1 if self.state.active_stage == "stage1" else stage2 if self.state.active_stage == "stage2" else stage3
        return MissionScore(
            stage1_score=stage1,
            stage2_score=stage2,
            stage3_score=stage3,
            active_score=active,
            remaining_s=remaining,
            total_estimated_score=stage1 + stage2 + stage3,
            stage1_raw_points=raw,
            stage1_penalty_points=self.state.stage1_penalty_points,
            stage1_bonus_points=bonus,
            stage1_next_target=self._stage1_next_target(),
            stage1_plan_locked=self.state.stage1_order_locked,
            stage2_round_scores=stage2_round_scores,
            stage2_zero_hit_streak=self.state.stage2_zero_hit_streak,
            stage2_failed=self.state.stage2_failed,
            stage2_passing_threshold_met=stage2 >= 20,
            stage3_round_scores=stage3_round_scores,
            stage3_miss_streak=self.state.stage3_miss_streak,
            stage3_failed=self.state.stage3_failed,
            stage3_award_threshold_met=stage3 >= 10,
        )

    def _stage1_next_target(self) -> Stage1Target | None:
        for target in self.state.stage1_order:
            if target not in self.state.stage1_completed_targets:
                return target  # type: ignore[return-value]
        return None

    def _require_stage1_started(self) -> None:
        if self.state.active_stage != "stage1":
            raise ValueError("STAGE1_NOT_ACTIVE")
        if not self.state.stage1_order_locked:
            raise ValueError("STAGE1_PLAN_NOT_LOCKED")
        if not self.state.timer_running:
            raise ValueError("STAGE1_TIMER_NOT_RUNNING")

    def _record_event(self, event_type: str) -> MissionSnapshot:
        self._persist()
        payload = self.snapshot().model_dump(mode="json")
        self.last_event = (event_type, payload)
        return self.snapshot()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.state = MissionState.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            self.state = MissionState()
