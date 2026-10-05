"""Engagement Debounce + Derivative Kick + Tilt koruması testleri.

Saha gözlemleri:
1. Tek karelik glitch radarı bozuyor (TARGET_ENGAGING + 0.5s donma + kol atlama).
2. Kadraj kenarından giren hedefte Y ekseninde dev salınım (derivative kick).
3. Arama fazında tilt'e komut kaçıyor.

Bu suite üç savmayı doğrular:
- EngagementDebouncer N ardışık kare olmadan onay vermez, boşlukta sıfırlanır.
- İlk karede D terimi 0'dır (tohumlama) ve Y hızı tanh doygunluğuyla sınırlıdır.
- ``sweep`` origin'inde gateway Y komutunu koşulsuz 0'a indirger.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

from app.services.command_gateway import CommandGateway
from app.services.light_tracking_controller import LightTrackingController
from app.services.patrol_radar_service import PatrolRadarService
from app.services.tracking_loop import EngagementDebouncer


# ---------------------------------------------------------------------------
# 1) Engagement Debounce
# ---------------------------------------------------------------------------

def test_debouncer_requires_consecutive_frames():
    deb = EngagementDebouncer(required=5)
    for _ in range(4):
        assert deb.observe(True) is False, "4 kare henüz onay vermemeli"
    assert deb.observe(True) is True, "5. ardışık kare onaylamalı"
    # Onay tek seferlik: sayaç sıfırlandı
    assert deb.streak == 0
    assert deb.observe(True) is False


def test_debouncer_resets_on_single_gap():
    deb = EngagementDebouncer(required=5)
    deb.observe(True)
    deb.observe(True)
    deb.observe(True)
    assert deb.observe(False) is False  # tek boş kare → sayaç sıfır
    assert deb.streak == 0
    deb.observe(True)
    deb.observe(True)
    deb.observe(True)
    deb.observe(True)
    assert deb.observe(True) is True  # boşluktan sonra tam 5 kare gerekir


def test_patrol_radar_consecutive_detections_config(tmp_path):
    service = PatrolRadarService(
        persistence_path=tmp_path / "patrol.json",
        load_persisted=False,
        consecutive_detections_required=7,
    )
    assert service.consecutive_detections_required == 7
    default = PatrolRadarService(persistence_path=tmp_path / "p2.json", load_persisted=False)
    assert default.consecutive_detections_required == 5


# ---------------------------------------------------------------------------
# 2) Derivative kick + Y doygunluğu (LightTrackingController)
# ---------------------------------------------------------------------------

def _a2_controller() -> LightTrackingController:
    return LightTrackingController("A2", persisted_path=None)


def test_first_frame_derivative_is_zero():
    """İlk karede D=0: raw_pid_y yalnızca şekillendirilmiş P terimine eşit."""
    ctl = _a2_controller()
    frame_w, frame_h = 1280, 720
    target_y = frame_h / 2.0 - 300.0  # error_y = -300 px (kadraj kenarı)
    out = ctl.update(
        target_x=frame_w / 2.0,
        target_y=target_y,
        frame_width=frame_w,
        frame_height=frame_h,
        dt=0.015,
        bbox_width=50.0,
        bbox_height=50.0,
        is_fresh=True,
    )
    e_lin = ctl.params.tilt_error_linear_px
    shaped = e_lin * math.tanh(-300.0 / e_lin)
    expected_p = ctl.params.kp_y * shaped
    assert math.isclose(out.raw_pid_y, expected_p, abs_tol=2.0), (
        f"ilk kare D=0 olmalı: raw_pid_y={out.raw_pid_y:.1f} beklenen P={expected_p:.1f}"
    )
    # Eski davranış: D = kd * 300/0.015 = 40.000 sps (8000 doygunluk). Yeni sınır:
    assert abs(out.speed_y) <= ctl.params.tilt_max_speed + 1


def test_y_speed_bounded_under_violent_reversal():
    """İkinci kare sert işaret değişiminde bile Y hızı tilt tavanını aşamaz."""
    ctl = _a2_controller()
    frame_w, frame_h = 1280, 720
    common = dict(frame_width=frame_w, frame_height=frame_h, dt=0.015,
                  bbox_width=50.0, bbox_height=50.0, is_fresh=True)
    ctl.update(target_x=frame_w / 2.0, target_y=frame_h / 2.0 - 300.0, **common)
    out = ctl.update(target_x=frame_w / 2.0, target_y=frame_h / 2.0 + 300.0, **common)
    assert abs(out.speed_y) <= ctl.params.tilt_max_speed + 1
    # Pan hareketi yok (hedef yatayda merkezde)
    assert abs(out.speed_x) <= ctl.params.min_speed + 1


def test_y_saturation_is_soft_not_cliff():
    """tanh şekillendirme: 2× hatada komut 2× olmaz, yumuşak doyar."""
    ctl = _a2_controller()
    frame_w, frame_h = 1280, 720
    common = dict(frame_width=frame_w, frame_height=frame_h, dt=0.015,
                  bbox_width=50.0, bbox_height=50.0, is_fresh=True)
    out_small = ctl.update(target_x=frame_w / 2.0, target_y=frame_h / 2.0 - 60.0, **common)
    ctl2 = _a2_controller()
    out_large = ctl2.update(target_x=frame_w / 2.0, target_y=frame_h / 2.0 - 300.0, **common)
    # 5× hata → komut oranı ~2.2× civarı (doğrusal olsaydı 5× olurdu)
    ratio = abs(out_large.raw_pid_y) / max(1.0, abs(out_small.raw_pid_y))
    assert 1.5 < ratio < 3.5, f"yumuşak doygunluk bekleniyordu, oran={ratio:.2f}"


# ---------------------------------------------------------------------------
# 3) Arama fazında tilt kilidi (CommandGateway)
# ---------------------------------------------------------------------------

def _gateway() -> CommandGateway:
    serial_mock = MagicMock()
    serial_mock.light_protocol_enabled = True
    return CommandGateway(serial=serial_mock, logger=MagicMock())


def test_sweep_origin_forces_tilt_zero():
    gw = _gateway()
    sx, sy = gw._apply_motion_guards(1500, 777, "sweep")
    assert sy == 0, "sweep origin'inde Y koşulsuz 0 olmalı"
    assert sx != 0


def test_tracking_origin_keeps_tilt_command():
    gw = _gateway()
    sx, sy = gw._apply_motion_guards(0, 900, "tracking")
    assert sy == 900, "tracking origin'inde Y komutu korunmalı"


def test_axis_lock_still_applies():
    gw = _gateway()
    gw.set_axis_lock("tilt", True)
    _, sy = gw._apply_motion_guards(0, 900, "tracking")
    assert sy == 0
