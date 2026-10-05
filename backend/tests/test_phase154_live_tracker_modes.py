from __future__ import annotations

import time

import numpy as np

from app.schemas.vision import BBox, BalloonDetection, BodyDetection, VisionEvent
from app.services.body_tracker_service import BodyTrackerService
from app.services.live_tracker_bridge import tracker_profile_catalog
from app.services.multi_target_tracker_service import MultiTargetTrackerService


EXPECTED_PROFILES = {
    "current_custom",
    "bytetrack",
    "botsort_no_cmc",
    "botsort_sparse",
    "botsort_ecc",
    "ocsort",
    "norfair_custom",
}


def _event(frame_id: int, body: list[BodyDetection]) -> VisionEvent:
    return VisionEvent(
        frame_id=frame_id,
        timestamp_ms=int(time.time() * 1000) + frame_id,
        source="tracker-test",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=2,
        postprocess_ms=1,
        total_latency_ms=4,
        body_detections=body,
        balloon_detections=[
            BalloonDetection(
                id=frame_id,
                confidence=0.91,
                bbox=BBox(x=200 + frame_id * 2, y=120, w=30, h=30),
                center_x=215 + frame_id * 2,
                center_y=135,
                source="balloon-test",
            )
        ],
    )


def test_all_lab_tracker_profiles_are_constructible_and_keep_live_ids() -> None:
    image = np.random.default_rng(154).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    profiles = tracker_profile_catalog()
    assert {item["profile_id"] for item in profiles} == EXPECTED_PROFILES
    assert all(item["available"] for item in profiles)

    for item in profiles:
        profile = item["profile_id"]
        body_tracker = BodyTrackerService()
        balloon_tracker = MultiTargetTrackerService()
        assert body_tracker.configure(profile) == profile
        assert balloon_tracker.configure(profile) == profile
        body_output: list[BodyDetection] = []
        balloon_status = None
        for frame_id in range(1, 5):
            body_output = body_tracker.update(
                [
                    BodyDetection(
                        id=frame_id,
                        class_name="f16",
                        class_id=0,
                        confidence=0.92,
                        bbox=BBox(x=100 + frame_id * 2, y=100, w=80, h=50),
                        source="body-test",
                    )
                ],
                frame_id=frame_id,
                capture_timestamp_ns=time.time_ns() + frame_id * 1_000_000,
                image=image,
            )
            balloon_status = balloon_tracker.update(_event(frame_id, body_output), image=image)
        assert body_output[0].track_id == 1
        assert balloon_status is not None
        assert balloon_status.tracker_kind == profile
        assert balloon_status.tracks[0].track_id == 1
        assert body_tracker.last_error is None
        assert balloon_tracker.last_error is None


def test_tracker_catalog_and_hot_apply_have_backend_readback(client) -> None:
    catalog = client.get("/api/vision/runtime/trackers")
    assert catalog.status_code == 200
    assert catalog.json()["active_profile"] == "current_custom"
    assert {item["profile_id"] for item in catalog.json()["profiles"]} == {"current_custom"}
    assert catalog.json()["locked_profile"] == "current_custom"

    profile = client.get("/api/vision/runtime/settings").json()
    profile.update({"tracker_enabled": True, "tracker_type": "bytetrack"})
    applied = client.post("/api/vision/runtime/apply-settings", json=profile)
    assert applied.status_code == 200
    assert applied.json()["accepted"] is True
    readback = client.get("/api/vision/runtime/trackers").json()
    assert readback["active_profile"] == "current_custom"
    assert readback["body_profile"] == "current_custom"
    assert readback["balloon_profile"] == "current_custom"
    assert readback["body_error"] is None
    assert readback["balloon_error"] is None
