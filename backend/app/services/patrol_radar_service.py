"""
PatrolRadarService — 3 Yollu Taret Radar Devriye Mekanizması.

Aşama 2 ve Aşama 3'te dar açılı lens kadrajında hedef yokken taretin 3 yolu
(1. Yol: Sol ~120°, 2. Yol: Orta ~135°, 3. Yol: Sağ ~150°) sırayla taramasını
ve hedef algılandığında takibe devredip hedef düşünce kaldığı yerden devam
etmesini yönetir.
"""

from __future__ import annotations

import json
import math
import time
from enum import Enum
from pathlib import Path
from typing import Any

from app.schemas.operation import PathAngleConfig
from app.services.storage_paths import project_root


class PatrolStrategy(str, Enum):
    CORRIDOR_HOP = "CORRIDOR_HOP"     # Option A: 3-Way Waypoints (120 -> 135 -> 150)
    SMOOTH_SWEEP = "SMOOTH_SWEEP"     # Option B: Continuous smooth 30 deg sweep
    CENTER_DWELL = "CENTER_DWELL"     # Option C: Center Dwell (Pusu @ 135 deg, 15m FOV covers 3 lanes)


class SweepGenerator:
    """Continuous sinusoidal sweep generator across corridor angles (135 +- sector/2 deg).

    Uses a sinusoidal position profile: theta(t) = center + span * sin(phase).
    Velocity profile: v(t) = span * omega_s * cos(phase).
    At sector boundaries (+-span), cos(phase) == 0, guaranteeing zero shock, zero backlash,
    and zero sudden velocity step without requiring ad-hoc braking logic.

    Kinematics:
      - 40 deg HFOV @ 1280 px -> 0.03125 deg/px.
      - 10 ms exposure & <= 3 px motion blur limit -> peak velocity <= 9.375 deg/s.
      - For span = 15 deg (+-15 deg sweep): omega_s = 9.375 / 15.0 = 0.625 rad/s (period ~10.05 s).
    """

    HFOV_DEG: float = 40.0
    IMAGE_WIDTH_PX: int = 1280
    EXPOSURE_S: float = 0.010
    MAX_BLUR_PX: float = 3.0

    def __init__(
        self,
        center_deg: float = 135.0,
        sector_deg: float = 30.0,
        speed_dps: float = 9.375,
        turnaround_s: float = 0.40,
    ) -> None:
        self.center_deg = float(center_deg)
        self.sector_deg = float(sector_deg)
        self.span_deg = self.sector_deg / 2.0
        self.speed_dps = float(speed_dps)
        self.turnaround_s = float(turnaround_s)
        self.direction: float = 1.0
        self.phase: float = 0.0
        self._blend_elapsed: float | None = None
        self._recompute_kinematics()

    def _recompute_kinematics(self) -> None:
        self.span_deg = max(2.5, self.sector_deg / 2.0)
        # Blur-limited max angular speed
        deg_per_px = self.HFOV_DEG / max(float(self.IMAGE_WIDTH_PX), 1.0)
        max_blur_speed = (self.MAX_BLUR_PX * deg_per_px) / max(self.EXPOSURE_S, 1e-4)  # ~9.375 deg/s
        self.peak_speed_dps = min(self.speed_dps, max_blur_speed)
        self.omega_rad_s = self.peak_speed_dps / self.span_deg  # rad/s

    def update_params(
        self,
        center_deg: float | None = None,
        sector_deg: float | None = None,
        speed_dps: float | None = None,
    ) -> None:
        if center_deg is not None:
            self.center_deg = float(center_deg)
        if sector_deg is not None:
            self.sector_deg = max(5.0, float(sector_deg))
        if speed_dps is not None:
            self.speed_dps = max(1.0, min(25.0, float(speed_dps)))
        self._recompute_kinematics()

    def sync_phase_to_angle(self, current_deg: float, direction: float = 1.0) -> None:
        """Synchronizes internal phase to match current_deg seamlessly."""
        span = max(self.span_deg, 1.0)
        norm = (float(current_deg) - self.center_deg) / span
        norm_clamped = max(-1.0, min(1.0, norm))
        base_asin = math.asin(norm_clamped)
        if direction >= 0:
            self.phase = base_asin if base_asin >= 0 else (2.0 * math.pi + base_asin)
        else:
            self.phase = math.pi - base_asin

    def velocity_dps(self, dt: float, current_deg: float | None = None) -> float:
        """Computes instantaneous smooth angular velocity in deg/s."""
        self.phase = (self.phase + self.omega_rad_s * dt) % (2.0 * math.pi)
        v = self.span_deg * self.omega_rad_s * math.cos(self.phase)
        self.direction = 1.0 if v >= 0 else -1.0

        # Boundary guard against external offset/drift
        if current_deg is not None and not math.isnan(current_deg):
            max_deg = self.center_deg + self.span_deg
            min_deg = self.center_deg - self.span_deg
            if current_deg >= max_deg and v > 0:
                self.phase = (math.pi / 2.0 + self.omega_rad_s * dt) % (2.0 * math.pi)
                v = self.span_deg * self.omega_rad_s * math.cos(self.phase)
                self.direction = -1.0
            elif current_deg <= min_deg and v < 0:
                self.phase = (3.0 * math.pi / 2.0 + self.omega_rad_s * dt) % (2.0 * math.pi)
                v = self.span_deg * self.omega_rad_s * math.cos(self.phase)
                self.direction = 1.0
        return v

    def calculate_sweep_speed_sps(self, dt: float, pan_scale: float = 133.333, current_deg: float | None = None) -> int:
        """Returns instantaneous commanded speed in steps-per-second (sps)."""
        v_dps = self.velocity_dps(dt, current_deg)
        return int(round(v_dps * pan_scale))

    def handoff_speed_sps(
        self,
        pid_target_speed_sps: float,
        dt: float,
        blend_time_s: float = 0.20,
        pan_scale: float = 133.333,
    ) -> int:
        """Smoothly blends sweep speed into visual-PID tracking speed over blend_time_s.

        Uses a raised-cosine ramp: alpha = 0.5 * (1 - cos(pi * t / T)).
        Prevents jerk, sudden camera vibration, and step loss during handoff.
        """
        if self._blend_elapsed is None:
            self._blend_elapsed = 0.0
        self._blend_elapsed += dt

        if self._blend_elapsed >= blend_time_s:
            return int(round(pid_target_speed_sps))

        alpha = 0.5 * (1.0 - math.cos(math.pi * self._blend_elapsed / blend_time_s))
        sweep_sps = self.calculate_sweep_speed_sps(dt, pan_scale)
        blended = (1.0 - alpha) * sweep_sps + alpha * pid_target_speed_sps
        return int(round(blended))

    def reset_handoff(self) -> None:
        self._blend_elapsed = None

    def reference_angle_deg(self) -> float:
        """Reference angle implied by current phase."""
        return self.center_deg + self.span_deg * math.sin(self.phase)


class PatrolRadarService:
    def __init__(
        self,
        config: PathAngleConfig | None = None,
        persistence_path: Path | None = None,
        load_persisted: bool = True,
        lost_frames_required: int = 3,
        consecutive_detections_required: int = 5,
    ) -> None:
        self.persistence_path = persistence_path if persistence_path is not None else (project_root() / "config" / "runtime" / "patrol_radar.active.json")
        if config is not None:
            self.config = config
        else:
            self.config = PathAngleConfig()
            if load_persisted:
                self._load_persisted_config()
        self.sweep_generator = SweepGenerator(
            center_deg=getattr(self.config, "path2_deg", 135.0),
            sector_deg=getattr(self.config, "sweep_sector_deg", 30.0),
            speed_dps=getattr(self.config, "sweep_speed_dps", 9.375),
        )
        # Devriye sırası: CENTER_DWELL modunda daima Orta (2) pusu, CORRIDOR_HOP modunda Orta (2) -> Sağ (3) -> Orta (2) -> Sol (1)
        strategy_str = str(getattr(self.config, "strategy", "SMOOTH_SWEEP")).upper().strip()
        if strategy_str == "CENTER_DWELL":
            self.patrol_sequence = [2]
        else:
            self.patrol_sequence = [2, 3, 2, 1]
        self._sequence_index = 0
        self._last_switch_mono = 0.0
        self._state = "IDLE"  # IDLE, SLEWING_TO_CENTER, PATROLLING, SETTLING, INSPECTING, SWEEPING, TARGET_ENGAGING, PAUSED
        # 0.5 deg: "merkeze ulasildi" toleransi (README S.4.2 ile birebir).
        self.SWEEP_CENTER_TOLERANCE_DEG: float = 0.5
        self._rounds_completed = 0
        self._on_path = False
        self._slewing = False
        self._latched_path: int | None = None
        self._dwell_started_mono = 0.0
        self._settle_started_mono = 0.0
        self.settle_time_s = 0.50  # 500ms aktif frenleme ve mekanik sönümlenme süresi
        self.target_loss_hold_s = 0.25  # 250ms hedef kayıp onay penceresi (balon patlayınca süpürmeye hızlı dönüş)
        self._target_lost_since_mono = 0.0
        # Engagement debounce: TrackingLoop bu değeri okur; N ardışık kare
        # doğrulanmadan radar takibe devredilmez (tek karelik glitch koruması).
        self.consecutive_detections_required = max(1, int(consecutive_detections_required))
        self._consecutive_detections = 0
        self.lost_frames_required = lost_frames_required  # Hysteresis: peş peşe N kare kayıp olmadan kol atlama!
        self._engaging_lost_frames = 0
        self.pop_blind_s = 0.15  # 150ms patlama sonrası kör pencere
        self._pop_blind_until_mono = 0.0

    @property
    def state(self) -> str:
        return self._state

    @property
    def latched_path(self) -> int | None:
        return self._latched_path

    @property
    def is_slewing(self) -> bool:
        return bool(self.config.patrol_enabled and self._slewing)

    @property
    def is_settling(self) -> bool:
        return bool(self.config.patrol_enabled and self._state == "SETTLING")

    @property
    def is_inspecting(self) -> bool:
        return bool(self.config.patrol_enabled and self._state == "INSPECTING")

    @property
    def is_on_path(self) -> bool:
        return bool(self.config.patrol_enabled and (self._on_path or self._state == "INSPECTING"))

    @property
    def is_patrolling(self) -> bool:
        return bool(self.config.patrol_enabled and self._state in {"PATROLLING", "SETTLING", "INSPECTING", "SLEWING", "SLEWING_TO_CENTER", "SWEEPING"})

    @property
    def is_slewing_to_center(self) -> bool:
        return bool(self.config.patrol_enabled and self._state == "SLEWING_TO_CENTER")

    @property
    def is_engaging(self) -> bool:
        return bool(self.config.patrol_enabled and self._state == "TARGET_ENGAGING")

    @property
    def is_settle_complete(self) -> bool:
        if self._state != "SETTLING":
            return True
        return (time.monotonic() - self._settle_started_mono) >= self.settle_time_s

    @property
    def is_in_pop_blind(self) -> bool:
        return time.monotonic() < self._pop_blind_until_mono

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        dwell_spent = (now - self._dwell_started_mono) if (self._on_path and self._dwell_started_mono > 0) else 0.0
        dwell_remaining = max(0.0, round(self.config.dwell_time_s - dwell_spent, 2)) if self._on_path else self.config.dwell_time_s
        return {
            "state": self._state,
            "patrol_enabled": self.config.patrol_enabled,
            "active_path": self.config.active_path,
            "active_target_angle": self.get_active_path_angle(),
            "path1_deg": self.config.path1_deg,
            "path2_deg": self.config.path2_deg,
            "path3_deg": self.config.path3_deg,
            "dwell_time_s": self.config.dwell_time_s,
            "strategy": getattr(self.config, "strategy", "CORRIDOR_HOP"),
            "sweep_speed_dps": getattr(self.config, "sweep_speed_dps", 9.375),
            "sweep_sector_deg": getattr(self.config, "sweep_sector_deg", 30.0),
            "scan_tilt_deg": getattr(self.config, "scan_tilt_deg", 30.0),
            "sweep_direction": getattr(self.sweep_generator, "direction", 1.0),
            "rounds_completed": self._rounds_completed,
            "on_path": self._on_path,
            "dwell_remaining_s": dwell_remaining,
            "seconds_at_current_path": round(now - self._last_switch_mono, 2) if self._last_switch_mono > 0 else 0.0,
        }

    def _load_persisted_config(self) -> None:
        """Kayıtlı koridor açılarını ve dwell süresini diskten yükler."""
        try:
            if self.persistence_path.exists():
                data = json.loads(self.persistence_path.read_text(encoding="utf-8"))
                if "path1_deg" in data:
                    self.config.path1_deg = float(data["path1_deg"])
                if "path2_deg" in data:
                    self.config.path2_deg = float(data["path2_deg"])
                if "path3_deg" in data:
                    self.config.path3_deg = float(data["path3_deg"])
                if "dwell_time_s" in data:
                    self.config.dwell_time_s = max(0.2, float(data["dwell_time_s"]))
                if "strategy" in data:
                    self.config.strategy = str(data["strategy"])
                if "sweep_speed_dps" in data:
                    self.config.sweep_speed_dps = float(data["sweep_speed_dps"])
                    if hasattr(self, "sweep_generator"):
                        self.sweep_generator.update_params(speed_dps=self.config.sweep_speed_dps)
                if "sweep_sector_deg" in data:
                    self.config.sweep_sector_deg = float(data["sweep_sector_deg"])
                    if hasattr(self, "sweep_generator"):
                        self.sweep_generator.update_params(sector_deg=self.config.sweep_sector_deg)
                if "scan_tilt_deg" in data:
                    # SORUN A/D DUZELTMESI: sahada gozlemlenen gercek hedef
                    # irtifasi (mekanik AutoHome merkezinden bagimsiz). Bkz.
                    # tracking_loop.py:_scan_tilt_deg().
                    self.config.scan_tilt_deg = float(data["scan_tilt_deg"])
        except Exception:
            pass

    def _persist_config(self) -> None:
        """Koridor açılarını ve dwell süresini diske kalıcı olarak kaydeder."""
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "path1_deg": self.config.path1_deg,
                "path2_deg": self.config.path2_deg,
                "path3_deg": self.config.path3_deg,
                "dwell_time_s": self.config.dwell_time_s,
                "strategy": getattr(self.config, "strategy", "CORRIDOR_HOP"),
                "sweep_speed_dps": getattr(self.config, "sweep_speed_dps", 9.375),
                "sweep_sector_deg": getattr(self.config, "sweep_sector_deg", 30.0),
                "scan_tilt_deg": getattr(self.config, "scan_tilt_deg", 30.0),
            }
            self.persistence_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def start_patrol(self, force_reset: bool = False, strategy: str | None = None) -> dict[str, Any]:
        """Devriyeyi başlatır. Zaten aktifse ve force_reset=False ise kolu Kol 2'ye resetlemez (idempotent)."""
        if strategy is not None:
            self.config.strategy = str(strategy).upper().strip()
        already_enabled = self.config.patrol_enabled
        self.config.patrol_enabled = True
        if not already_enabled or force_reset or self._state == "IDLE":
            strategy_str = str(getattr(self.config, "strategy", "CORRIDOR_HOP")).upper().strip()
            if strategy_str == "CENTER_DWELL":
                self.patrol_sequence = [2]
                self.config.active_path = 2
                self._state = "INSPECTING"
                self._on_path = False
            elif strategy_str == "SMOOTH_SWEEP":
                self.patrol_sequence = [2]
                self.config.active_path = 2
                # KOK NEDEN #2/#4 DUZELTMESI: dogrudan "SWEEPING"e degil, once
                # "SLEWING_TO_CENTER"e gecilir. Taret zaten merkezdeyse
                # tracking_loop.py bunu bir sonraki tick'te aninda algilayip
                # notify_centered() ile "SWEEPING"e gecirir (goze gorunur
                # gecikme olmaz); merkezden uzaktaysa (or. manuel sonrasi
                # 117.98 derece) once yumusak sekilde merkeze cekilir.
                self._state = "SLEWING_TO_CENTER"
                self._on_path = False
                # _slewing=True: tick()'in en ustundeki "if self._slewing:
                # return" koruyucusunu tetikler, boylece asagidaki
                # "strategy == SMOOTH_SWEEP -> state = SWEEPING" kisayolu bu
                # devri STOMP edip erken SWEEPING'e dondurmez.
                self._slewing = True
                if hasattr(self, "sweep_generator"):
                    self.sweep_generator.reset_handoff()
            else:
                self.patrol_sequence = [2, 3, 2, 1]
                self._state = "PATROLLING"
                self._on_path = False
            self._sequence_index = 0
            self.config.active_path = self.patrol_sequence[0] if self.patrol_sequence else 2
            self._latched_path = None
            if self._state != "SLEWING_TO_CENTER":
                self._slewing = False
            self._dwell_started_mono = 0.0
            self._last_switch_mono = time.monotonic()
            self._engaging_lost_frames = 0
            self._target_lost_since_mono = 0.0
        return self.status()

    def stop_patrol(self) -> dict[str, Any]:
        self.config.patrol_enabled = False
        self._state = "IDLE"
        self._latched_path = None
        self._on_path = False
        self._slewing = False
        self._dwell_started_mono = 0.0
        self._engaging_lost_frames = 0
        self._target_lost_since_mono = 0.0
        return self.status()

    def goto_path(self, path_number: int) -> dict[str, Any]:
        """Tareti doğrudan belirtilen kola yönlendirir."""
        if path_number not in (1, 2, 3):
            raise ValueError(f"Geçersiz yol: {path_number}. (1, 2 veya 3 olmalı)")
        self.config.active_path = path_number
        if path_number in self.patrol_sequence:
            self._sequence_index = self.patrol_sequence.index(path_number)
        self._latched_path = None
        self._on_path = False
        self._slewing = False
        self._dwell_started_mono = 0.0
        self._last_switch_mono = time.monotonic()
        return self.status()

    def notify_slewing(self) -> None:
        """Taret kol açısına doğru dönerken dwell sayacını durdurur."""
        self._slewing = True
        self._on_path = False
        self._state = "SLEWING"
        self._dwell_started_mono = 0.0

    def notify_settling(self) -> None:
        """Kola yaklaşıldığında (pan_err <= 1.5°) aktif frenleme ve sönümlenme evresine geçer."""
        self._slewing = False
        self._on_path = False
        self._latched_path = self.config.active_path
        if self._state != "SETTLING":
            self._state = "SETTLING"
            self._settle_started_mono = time.monotonic()
        self._dwell_started_mono = 0.0

    def notify_centered(self, current_pan_deg: float | None = None) -> None:
        """Taret merkeze (Pan 135.0 / Tilt = tracking_loop._scan_tilt_deg(),
        <= SWEEP_CENTER_TOLERANCE_DEG hata) ulastiginda tracking_loop.py
        tarafindan cagrilir.

        current_pan_deg verilirse SweepGenerator fazi taretin gercek fiziksel
        acisiyla senkronize edilir (sync_phase_to_angle), boylece sinusoidal
        süpürme tam olarak 135.0° sektörü etrafında pürüzsüz baslar.
        """
        self._slewing = False
        if hasattr(self, "sweep_generator"):
            if current_pan_deg is not None and not math.isnan(current_pan_deg):
                self.sweep_generator.sync_phase_to_angle(float(current_pan_deg), direction=1.0)
            else:
                self.sweep_generator.phase = 0.0
                self.sweep_generator.direction = 1.0
            self.sweep_generator.reset_handoff()
        self._state = "SWEEPING"
        self._on_path = True
        self._dwell_started_mono = 0.0

    def notify_on_path(self) -> None:
        """Taret sönümlenip durduğunda kola varılmış sayılır, dwell ve inceleme başlar."""
        now = time.monotonic()
        self._slewing = False
        self._latched_path = self.config.active_path
        if not self._on_path:
            self._on_path = True
            self._dwell_started_mono = now
        self._state = "INSPECTING"

    def update_angles(
        self,
        path1_deg: float | None = None,
        path2_deg: float | None = None,
        path3_deg: float | None = None,
        dwell_time_s: float | None = None,
        strategy: str | None = None,
        sweep_speed_dps: float | None = None,
        sweep_sector_deg: float | None = None,
        scan_tilt_deg: float | None = None,
    ) -> PathAngleConfig:
        if path1_deg is not None:
            self.config.path1_deg = float(path1_deg)
        if path2_deg is not None:
            self.config.path2_deg = float(path2_deg)
            if hasattr(self, "sweep_generator"):
                self.sweep_generator.update_params(center_deg=self.config.path2_deg)
        if path3_deg is not None:
            self.config.path3_deg = float(path3_deg)
        if dwell_time_s is not None:
            self.config.dwell_time_s = max(0.2, float(dwell_time_s))
        if strategy is not None:
            self.config.strategy = str(strategy).upper().strip()
            if self.config.strategy == "CENTER_DWELL":
                self.patrol_sequence = [2]
                self.config.active_path = 2
            elif self.config.strategy == "CORRIDOR_HOP":
                self.patrol_sequence = [2, 3, 2, 1]
        if sweep_speed_dps is not None:
            self.config.sweep_speed_dps = max(1.0, min(25.0, float(sweep_speed_dps)))
            if hasattr(self, "sweep_generator"):
                self.sweep_generator.update_params(speed_dps=self.config.sweep_speed_dps)
        if sweep_sector_deg is not None:
            self.config.sweep_sector_deg = max(5.0, min(60.0, float(sweep_sector_deg)))
            if hasattr(self, "sweep_generator"):
                self.sweep_generator.update_params(sector_deg=self.config.sweep_sector_deg)
        if scan_tilt_deg is not None:
            # SORUN A/D DUZELTMESI: devriye/arama tilt hedefi artik saha
            # kalibrasyonuna gore (koda dokunmadan) buradan ayarlanabilir.
            # Mekanik AutoHome merkezinden (HOME_TILT_DEG=30.0, tracking_loop.py)
            # BAGIMSIZDIR - bkz. tracking_loop.py:_scan_tilt_deg().
            self.config.scan_tilt_deg = float(scan_tilt_deg)
        self._persist_config()
        return self.config

    def capture_angle(self, path_number: int, current_angle_deg: float) -> float:
        """Taretin o anki açısını verilen yola atar (1, 2 veya 3)."""
        angle = round(float(current_angle_deg), 1)
        if path_number == 1:
            self.config.path1_deg = angle
        elif path_number == 2:
            self.config.path2_deg = angle
        elif path_number == 3:
            self.config.path3_deg = angle
        else:
            raise ValueError(f"Geçersiz yol numarası: {path_number}. (1, 2 veya 3 olmalı)")
        self._persist_config()
        return angle

    def get_path_angle(self, path_number: int) -> float:
        if path_number == 1:
            return self.config.path1_deg
        elif path_number == 2:
            return self.config.path2_deg
        elif path_number == 3:
            return self.config.path3_deg
        return self.config.path2_deg

    def get_active_path_angle(self) -> float:
        return self.get_path_angle(self.config.active_path)

    def tick(self, has_active_target: bool) -> dict[str, Any]:
        """Her döngüde veya periyodik kontrolde çağrılır."""
        if not self.config.patrol_enabled:
            self._state = "IDLE"
            self._engaging_lost_frames = 0
            self._target_lost_since_mono = 0.0
            return self.status()

        now = time.monotonic()

        # TARGET_ENGAGING durumunda hedef kontrolü ve histerezis
        if self._state == "TARGET_ENGAGING":
            if has_active_target:
                self._engaging_lost_frames = 0
                self._target_lost_since_mono = 0.0
                return self.status()
            else:
                self._engaging_lost_frames += 1
                if self._target_lost_since_mono == 0.0:
                    self._target_lost_since_mono = now

                # Kullanıcı kuralı: Hedef 1 anlığına kaçırılsa bile geri radara dönme!
                # En az target_loss_hold_s (0.50 sn) kesintisiz kayıp olmadan kol atlama!
                loss_elapsed = now - self._target_lost_since_mono
                if self.target_loss_hold_s > 0.0:
                    if loss_elapsed < self.target_loss_hold_s:
                        return self.status()
                elif self._engaging_lost_frames < self.lost_frames_required:
                    return self.status()

                # Hedef gerçekten kayboldu / vuruldu (bekleme süresi doldu)
                self._engaging_lost_frames = 0
                self._target_lost_since_mono = 0.0
                self._pop_blind_until_mono = now + self.pop_blind_s
                is_sweep = getattr(self.config, "strategy", "CORRIDOR_HOP") == "SMOOTH_SWEEP"
                if is_sweep:
                    # KOK NEDEN #4 (devam): hedef vurulup/kaybolup PID
                    # takipten cikildiginda taret PID'in birakti"gi keyfi
                    # acidadir - dogrudan SWEEPING'e degil, once yine
                    # SLEWING_TO_CENTER'a gecilir (start_patrol ile ayni yol).
                    self.config.active_path = 2
                    self._state = "SLEWING_TO_CENTER"
                    self._on_path = False
                    self._slewing = True
                else:
                    self._sequence_index = (self._sequence_index + 1) % len(self.patrol_sequence)
                    if self._sequence_index == 0:
                        self._rounds_completed += 1
                    self.config.active_path = self.patrol_sequence[self._sequence_index]
                    self._state = "PATROLLING"
                    self._on_path = False
                self._latched_path = None
                if self._state != "SLEWING_TO_CENTER":
                    self._slewing = False
                self._dwell_started_mono = 0.0
                return self.status()

        # Hedef tespit edildiğinde doğrudan TARGET_ENGAGING
        if has_active_target:
            self._state = "TARGET_ENGAGING"
            self._engaging_lost_frames = 0
            self._target_lost_since_mono = 0.0
            self._on_path = False
            self._slewing = False
            self._dwell_started_mono = 0.0
            return self.status()

        # SETTLING durumunda sönümlenme süresini kontrol et
        if self._state == "SETTLING":
            if (now - self._settle_started_mono) >= self.settle_time_s:
                self.notify_on_path()
            return self.status()

        # SLEWING durumunda dwell işletme, intikal devam ediyor
        if self._slewing:
            return self.status()

        # SMOOTH_SWEEP modunda yollar arasında atlama yapma, sürekli süpürme durumunu koru
        if getattr(self.config, "strategy", "CORRIDOR_HOP") == "SMOOTH_SWEEP" and self._state != "SLEWING_TO_CENTER":
            self._state = "SWEEPING"
            self._slewing = False
            self._on_path = True
            return self.status()

        # CENTER_DWELL modunda yollar arasında atlama yapma, daima merkez hatta (path 2 @ 135 deg) pusuya yat
        if getattr(self.config, "strategy", "CORRIDOR_HOP") == "CENTER_DWELL":
            self.config.active_path = 2
            self._state = "INSPECTING" if self._on_path else "PATROLLING"
            return self.status()

        self._state = "INSPECTING" if self._on_path else "PATROLLING"

        # INSPECTING veya PATROLLING durumunda dwell süresini kontrol et
        is_dwelling = self._on_path and self._dwell_started_mono > 0
        dwell_elapsed = (now - self._dwell_started_mono) if is_dwelling else (now - self._last_switch_mono if not self._slewing else 0.0)

        if dwell_elapsed >= self.config.dwell_time_s:
            # Sıradaki yola geç
            self._sequence_index = (self._sequence_index + 1) % len(self.patrol_sequence)
            if self._sequence_index == 0:
                self._rounds_completed += 1
            self.config.active_path = self.patrol_sequence[self._sequence_index]
            self._latched_path = None
            self._on_path = False
            self._slewing = False
            self._dwell_started_mono = 0.0
            self._last_switch_mono = now
            self._state = "PATROLLING"

        return self.status()

