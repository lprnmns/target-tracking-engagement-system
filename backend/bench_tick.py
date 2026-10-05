"""Aşama 3 ağır yol + kanıt kaydı maliyet ölçümü (mock, donanımsız).

Kullanım (backend klasöründen):  python bench_tick.py
Ölçülenler (ms, tick başına):
  A) izleyici+ilişkilendirme+registry+öncelik bloğu (tracking_loop 852-939 eşdeğeri)
  B) kanıt kaydı (observe_frame + capture_* + finalize) LOCKED durumda
  C) ATIŞ ANI: record_shot_ack (+ bir sonraki tick'te _begin_lock yeniden açılması)
"""
from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / "tests"))
from conftest import DEFAULT_CONFIG, _safe_test_config  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.tracking import TrackingState, TrackingUpdate  # noqa: E402
from app.schemas.vision import BalloonDetection, BBox, BodyDetection, VisionEvent  # noqa: E402
from app.schemas.command_gateway import GatewayCommandResult  # noqa: E402


def make_event(i: int) -> VisionEvent:
    bodies, balloons = [], []
    for k, (x, team) in enumerate([(300, "enemy"), (600, "friend"), (900, "friend")]):
        dx = int(20 * ((i // 10) % 5))
        bodies.append(BodyDetection(id=k + 1, class_name="f16", class_id=0, confidence=0.9,
                                    bbox=BBox(x=x + dx, y=200, w=120, h=60), source="bench", target_team=team))
        balloons.append(BalloonDetection(id=k + 1, confidence=0.9, bbox=BBox(x=x + dx + 45, y=270, w=30, h=30),
                                         center_x=x + dx + 60, center_y=285, source="bench"))
    return VisionEvent(frame_id=i, timestamp_ms=int(time.time() * 1000), source="bench", frame_width=1280,
                       frame_height=720, fps=60.0, preprocess_ms=1, inference_ms=10, postprocess_ms=1,
                       total_latency_ms=12, body_detections=bodies, balloon_detections=balloons)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    cfg = _safe_test_config(yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")), tmp)
    p = tmp / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    app = create_app(config_path=p, log_dir=tmp / "logs", report_dir=tmp / "reports" / "self_tests")
    rt = app.state.runtime
    ev_root = tmp / "evidence"
    rt.engagement_evidence.root = ev_root

    heavy, evidence, shot, relock = [], [], [], []
    for i in range(1, 900):
        event = make_event(i)
        t0 = time.perf_counter()
        tracks = rt.auto_tracker.multi_target_tracker.update(event)
        assoc = rt.association.update(event, tracks)
        conf = rt.hit_confirmation.update(event, tracks)
        rt.target_registry.update(event, tracks, assoc, conf, target_policy="BALLOON_AIRCRAFT")
        ctrl_ev = rt.target_registry.filter_event_for_tracking(event, tracks, assoc, target_policy="BALLOON_AIRCRAFT")
        rt.target_priority.update(tracks, assoc, 1280, 720, 640, 360, allowed_body_detection_ids={1},
                                  target_registry=rt.target_registry)
        t1 = time.perf_counter()
        sel = tracks.tracks[0] if tracks.tracks else None
        upd = TrackingUpdate(state=TrackingState.LOCKED, frame_id=i, dt=0.02, frame_center_x=640, frame_center_y=334,
                             target_center_x=float(event.balloon_detections[0].center_x),
                             target_center_y=285.0, selected_target_kind="balloon",
                             selected_detection_id=1, selected_track_id=sel.track_id if sel else None)
        rt.engagement_evidence.record_confirmation_status(conf)
        rt.engagement_evidence.observe_frame(event, upd, tracks, assoc, mission_stage="stage3", command_profile="COMPETITION")
        rt.engagement_evidence.capture_active_camera_frame(rt.camera_runtime)
        rt.engagement_evidence.capture_active_digital_twin_state(lambda: rt.digital_twin.state(rt))
        rt.engagement_evidence.finalize_due_recording()
        t2 = time.perf_counter()
        if i > 100:
            heavy.append((t1 - t0) * 1000)
            evidence.append((t2 - t1) * 1000)
        # her 60 tick'te bir "atış"
        if i > 100 and i % 60 == 0:
            active = rt.engagement_evidence._active
            cand = {"balloon_track_id": active.balloon_track_id if active else None}
            ts = time.perf_counter()
            rt.engagement_evidence.record_shot_ack(rt, cand, GatewayCommandResult(accepted=True, command="BALON,PATLAT", detail="bench", pico_ack="OK,QUEUED", physical_command_generated=True))
            shot.append((time.perf_counter() - ts) * 1000)
            # bir sonraki tick'teki observe_frame yeniden kilit açar mı?
            ev2 = make_event(i + 1000)
            tr2 = rt.auto_tracker.multi_target_tracker.update(ev2)
            as2 = rt.association.update(ev2, tr2)
            rt.engagement_evidence._last_observed_at = 0.0
            ts = time.perf_counter()
            before = rt.engagement_evidence._active.engagement_id if rt.engagement_evidence._active else None
            rt.engagement_evidence.observe_frame(ev2, upd.model_copy(update={"frame_id": i + 1000}), tr2, as2,
                                                 mission_stage="stage3", command_profile="COMPETITION")
            after = rt.engagement_evidence._active.engagement_id if rt.engagement_evidence._active else None
            relock.append(((time.perf_counter() - ts) * 1000, before != after))

    def s(xs):
        xs = sorted(xs)
        return f"ort={statistics.mean(xs):6.2f}  p95={xs[int(.95*len(xs))]:6.2f}  max={xs[-1]:6.2f}"
    print("A) agir yol (izleyici+iliski+registry+oncelik) ms/tick :", s(heavy))
    print("B) kanit kaydi (LOCKED) ms/tick                         :", s(evidence))
    print("C) record_shot_ack ms                                  :", s(shot))
    print("D) atis sonrasi ilk observe_frame ms (yeni kayit acildi mi):", s([r[0] for r in relock]),
          " yeni_kayit_orani=", sum(1 for r in relock if r[1]), "/", len(relock))
    rt.engagement_evidence.flush(5.0)
    mf = sorted(ev_root.rglob("manifest.json"), key=lambda q: q.stat().st_size)
    if mf:
        print("   en buyuk manifest boyutu: %.2f MB, kayit sayisi=%d" % (mf[-1].stat().st_size / 1e6, len(mf)))


if __name__ == "__main__":
    main()
