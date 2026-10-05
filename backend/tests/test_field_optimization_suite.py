import time
from types import SimpleNamespace
from app.schemas.config import TrackingConfig
from app.schemas.tracking import TrackingFireResult
from app.schemas.vision import BBox, BalloonDetection, BodyDetection
from app.services.auto_tracker_service import AutoTrackerService
from app.services.tracking_loop import TrackingLoop
from app.services import stage3_roi


def _tracker() -> AutoTrackerService:
    config = SimpleNamespace(tracking=TrackingConfig())
    logger = SimpleNamespace(emit=lambda *args, **kwargs: None)
    return AutoTrackerService(config=config, logger=logger)


def test_instant_target_switch_on_fire():
    tracker = _tracker()
    tracker.target_switch_grace_s = 1.0

    balloon = BalloonDetection(id=1, confidence=0.9, bbox=BBox(x=100, y=100, w=40, h=40), center_x=120, center_y=120)
    tracker._remember_balloon(balloon)
    tracker._locked_balloon_track_id = 101

    assert tracker._target_identity_reserved() is True

    fire_res = TrackingFireResult(
        accepted=True,
        command="BALON,PATLAT",
        reason_codes=[],
        detail="Fire dispatched",
        physical_command_generated=True,
    )
    tracker.record_fire_result(fire_res)

    assert tracker._target_identity_reserved() is False
    tracker._clear_target_identity_lock()
    assert tracker.preferred_target_x is None
    assert tracker.preferred_target_y is None
    assert tracker._locked_balloon_track_id is None


def test_unfired_loss_retains_one_second_grace():
    tracker = _tracker()
    tracker.target_switch_grace_s = 1.0

    balloon = BalloonDetection(id=2, confidence=0.9, bbox=BBox(x=200, y=200, w=40, h=40), center_x=220, center_y=220)
    tracker._remember_balloon(balloon)

    assert tracker._last_fire_at is None
    assert tracker._target_identity_reserved() is True

    tracker._locked_target_last_seen_at = time.monotonic() - 1.1
    assert tracker._target_identity_reserved() is False


def test_default_controller_mode_is_opt_sine_track():
    tracker = _tracker()

    assert tracker.controller_mode == "OPT_SINE_TRACK"
    mode = TrackingLoop._controller_mode_for_stage("stage2", "BALLOON")
    assert mode == "OPT_SINE_TRACK"


def test_two_tier_attachment_gate():
    body = BodyDetection(
        id=1,
        track_id=10,
        class_name="mini_micro_uav",
        class_id=0,
        confidence=0.9,
        target_team="enemy",
        bbox=BBox(x=100, y=100, w=100, h=50),
    )

    balloon_overlap = BalloonDetection(
        id=1,
        confidence=0.9,
        bbox=BBox(x=110, y=125, w=30, h=30),
        center_x=125,
        center_y=140,
    )

    assert stage3_roi.attachment_roi_contains(body.bbox, balloon_overlap.center_x, balloon_overlap.center_y, relaxed=False) is False
    assert stage3_roi.body_balloon_overlap_reject(body.bbox, balloon_overlap.bbox, relaxed=False) is True

    assert stage3_roi.attachment_roi_contains(body.bbox, balloon_overlap.center_x, balloon_overlap.center_y, relaxed=True) is True
    assert stage3_roi.body_balloon_overlap_reject(body.bbox, balloon_overlap.bbox, relaxed=True) is False


def test_tilt_dead_reckoning():
    class DummyTL:
        STEPS_PER_DEG_TILT = 88.889
        HOME_TOLERANCE_DEG = 0.05
        MAX_RETURN_ACCEL_SPS2 = 20000.0
        MAX_RETURN_SPEED_SPS = 6000.0
        _dead_reckon_tilt_deg = 30.0
        _current_tilt_speed_sps = 0.0

    tl = DummyTL()
    update_tilt = TrackingLoop._update_dead_reckoning_tilt.__get__(tl, DummyTL)
    compute_tilt = TrackingLoop._compute_smooth_return_to_tilt_speed.__get__(tl, DummyTL)

    new_tilt = update_tilt(-888.89, dt=0.015)
    assert abs(new_tilt - 29.85) < 0.01

    speed = compute_tilt(15.0, dt=0.015)
    assert speed < 0
