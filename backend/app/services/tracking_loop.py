"""
TrackingLoop — Bağımsız asyncio.Task olarak çalışan kapalı çevrim takip döngüsü.

WebSocket'in 200ms döngüsünden bağımsız, Light General/A2/A3 referans
cadencesinde (20/15 ms) çalışır; legacy çağrılar eski interval'i korur.
Vision → AutoTracker → Serial motor komutu zincirini yönetir.

Eski sistemde bu döngü ``main.py.process_step()`` içinde senkron olarak çalışıyordu.
Yeni sistemde asyncio task olarak çalışarak backend event loop'u bloklamaz.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time

from app.schemas.log import LogLevel
from app.schemas.tracking import TrackingState, TrackingUpdate
from app.services.auto_tracker_service import AutoTrackerService
from app.services.command_gateway import CommandGateway
from app.services.log_service import JsonlLogService
from app.services.safety_timing import MAX_VISION_EVENT_AGE_S
from app.services.safety_zone_service import ExclusionZoneManager
from app.services.serial_service import SerialService
from app.services.vision_pipeline import VisionPipeline
from app.services import stage3_roi
from app.services import yarisma_bayraklari as yb

FIRE_ZONE_RADIUS_RATIO = 0.25  # legacy telemetry radius (half of the 50% inner box)
FIRE_INNER_BBOX_RATIO = 0.30
# TrackingLoop fiziksel ateş yetkisine sahip değildir. Bu eşik yalnız
# CommandGateway'e ileride verilecek dry-run fire-adayı telemetrisi içindir.
FIRE_REQUIRED_FRAMES = 1
TRANSIENT_TARGET_LOSS_HOLD_S = 1.5


class EngagementDebouncer:
    """Radar → takip geçiş hakemi: N ardışık kare doğrulaması (glitch koruması).

    Tek karelik sahte tespitler radarı bozmaz: sayaç yalnızca kesintisiz
    aday karelerde artar, bir boş karede sıfırlanır. Onay (True) yalnızca
    ``required`` ardışık kareden sonra ve tek seferlik döner.
    """

    def __init__(self, required: int = 5) -> None:
        self.required = max(1, int(required))
        self.streak = 0

    def observe(self, candidate_seen: bool) -> bool:
        """Bir kareyi gözlemle; onay sağlandıysa True döner ve sayaç sıfırlanır."""
        if candidate_seen:
            self.streak += 1
        else:
            self.streak = 0
        if self.streak >= self.required:
            self.streak = 0
            return True
        return False

    def reset(self) -> None:
        self.streak = 0


class TrackingLoop:
    """
    Kapalı çevrim takip döngüsü.

    Lifecycle:
        loop = TrackingLoop(...)
        await loop.start()    # asyncio.Task başlatır
        ...
        await loop.stop()     # Task'ı durdurur

    Her iterasyonda:
        1. VisionPipeline'dan son frame al
        2. AutoTrackerService.update() ile PID hesapla
    3. CommandGateway ile preflight sonrası Pico'ya hız komutu gönder
    """

    # DEPRECATED: SCAN_TILT_DEG artik kullanilmiyor (bkz. __init__ icindeki
    # FIELD_SCAN_TILT_DEG / HOME_TILT_DEG ayrimi ve _scan_tilt_deg()).
    # Sinif seviyesinde sadece geriye-donuk uyumluluk icin birakildi.
    SCAN_TILT_DEG: float = 30.0
    TARGET_LOSS_TILT_FREEZE_S: float = 0.35

    def __init__(
        self,
        auto_tracker: AutoTrackerService,
        vision_pipeline: VisionPipeline,
        serial: SerialService,
        gateway: CommandGateway,
        logger: JsonlLogService,
        frame_width: int = 1920,
        frame_height: int = 1080,
        interval_ms: float = 12.0,
        tuning=None,
    ) -> None:
        self.auto_tracker = auto_tracker
        self.vision_pipeline = vision_pipeline
        self.serial = serial
        self.gateway = gateway
        self.logger = logger
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.interval_s = interval_ms / 1000.0
        self.tuning = tuning
        self._task: asyncio.Task | None = None
        self._running = False
        # When stop() cancels the task, stop() owns the one-and-only serial
        # cleanup.  An unexpected task cancellation (including process/event
        # loop shutdown) still executes the original full-safe cleanup in
        # _run(), preserving existing shutdown/fault behaviour.
        self._stop_cleanup_owned_by_caller = False
        self.last_update: TrackingUpdate | None = None
        self.loop_count = 0
        self.errors = 0
        self.fire_target_frames = 0
        self.is_firing = False
        self._fire_candidate_active = False
        self._fire_block_reason: str | None = None
        self._last_stale_vision_log_at = 0.0
        self._last_timebase_log_at = 0.0
        self._last_timebase_log_key: tuple[object, ...] | None = None
        self._last_speed_sent_at = 0.0
        self._last_speed_sent: tuple[int, int] | None = None
        self._target_loss_safed = False
        self._target_loss_started_at: float | None = None
        # Radar → takip geçiş debounce'u: patrol_radar.consecutive_detections_required
        # üzerinden _run içinde her karede tazelenir (varsayılan 5 kare).
        self._engage_debouncer = EngagementDebouncer(required=5)
        self._target_loss_soft_stopped = False
        self._events: list[tuple[str, dict]] = []
        self._lock_start_time: float | None = None
        self._last_fire_time: float = 0.0
        self._lock_confirmation_s: float = 0.15  # Aggressive snap fire (reduced from 0.35s)
        self._fire_cooldown_s: float = 0.50  # Synchronized with Pico ~430ms servo stroke (tak-tak rapid fire)
        self._last_shot_accepted: bool = False
        if yb.Y3_ATIS_ZAMANLAMA:
            # Y3: 26 Eylül'de test edilen 0.60 s (Pico 580 ms servo stroku, FIRE_PULSE_ACTIVE çakışmasını önler)
            self._fire_cooldown_s = yb.Y3_BEKLEME_S
        self._lock_lost_frames: int = 0
        # 500ms lightweight friendly body safety memory for Stage 3 friendly fire prevention
        self._recent_friendly_shields: list[tuple[float, Any]] = []

        # Dead-reckoning step integrator & smooth return-to-home
        self.STEPS_PER_DEG_PAN: float = 133.333
        self.STEPS_PER_DEG_TILT: float = 88.889
        self.HOME_PAN_DEG: float = 135.0
        # HOME_TILT_DEG: SADECE AutoHome'un mekanik merkezi (Pico limit switch'lerine
        # gore fiziksel 30.0 derece). Yalnizca dead-reckoning'i AutoHome sonrasi
        # sifirlamak/dogrulamak icin kullanilir; devriye/arama hedef tilt'i ARTIK
        # BUNA ESITLENMEZ (bkz. asagidaki not ve _scan_tilt_deg()).
        self.HOME_TILT_DEG: float = 30.0
        # SORUN A/D KOK NEDEN: Onceki "SORUN #1 DUZELTMESI" SCAN_TILT_DEG'i 15.0'dan
        # 30.0'a (yani dogrudan HOME_TILT_DEG'e) esitleyerek Sorun A'yi (AutoHome
        # sonrasi ofset) gizlemeye calismisti - ama bu, mekanik merkez (30 derece)
        # ile sahadaki GERCEK balon/hat irtifasini (22-24 derece, bkz. saha raporu
        # Sorun D) AYNI degiskende birlestirdigi icin Sorun D'yi (tarama<->kilit
        # arasi surekli 6-8 derecelik "bobbing") dogrudan yaratiyordu. Saha
        # loglarindaki tilt_position_deg telemetrisi de bunu dogruluyor: semantik
        # tilt -2.9..+10.2 derece arasinda sallaniyor, yani fiziksel tilt ~33
        # derece (mekanik merkeze yakin) ile ~20 derece (gercek balon irtifasi)
        # arasinda gidip geliyor.
        #
        # Duzeltme: iki kavram artik AYRI degiskenler.
        #   HOME_TILT_DEG        -> SADECE AutoHome/dead-reckoning referansi.
        #   FIELD_SCAN_TILT_DEG  -> devriye/SLEWING_TO_CENTER hedef tilt'i; sahada
        #                           gozlemlenen gercek hedef irtifasina (22-24
        #                           derecenin ortasi = 23.0) kalibre edilir ve
        #                           kod degistirmeden patrol_radar.config.scan_tilt_deg
        #                           uzerinden operator tarafindan ayarlanabilir
        #                           (bkz. _scan_tilt_deg() ve patrol_radar_service.py
        #                           update_angles()).
        self.FIELD_SCAN_TILT_DEG: float = 30.0
        self.TARGET_LOSS_TILT_FREEZE_S: float = 0.35
        self.MAX_RETURN_SPEED_SPS: float = 6000.0
        self.MAX_RETURN_ACCEL_SPS2: float = 20000.0
        self.HOME_TOLERANCE_DEG: float = 0.05  # ~6.6 steps deadband
        self._dead_reckon_pan_deg: float = 135.0
        self._dead_reckon_tilt_deg: float = 30.0
        self._last_locked_tilt_deg: float = 30.0
        self._poz_query_in_progress: bool = False
        self._last_background_poz_mono: float = 0.0
        self._current_pan_speed_sps: float = 0.0
        self._current_tilt_speed_sps: float = 0.0
        self._last_dead_reckon_time: float = time.monotonic()

        # Atışa Yasak / Güvenlik Alanı Yöneticisi (Exclusion Zone Manager)
        self.exclusion_zone_manager = ExclusionZoneManager()

    async def start(self) -> None:
        """Tracking döngüsünü başlat.

        KOK NEDEN #1 DUZELTMESI (Dead-Reckoning Kopuklugu): MANUEL modda
        TrackingLoop kapaliyken _dead_reckon_pan_deg/_tilt_deg guncellenmiyordu.
        Operator OTONOM'a bastigi TAM O ANDA taret kesin olarak duruyordur
        (joystick birakilmis / manuel mod kapatilmistir) - yani bu, "sadece
        durus aninda POZ ile kalibre et" kuralinin uygulanmasi gereken TAM
        dogru zamandir. Gorevi baslatmadan hemen once senkron bir POZ okumasi
        yapip dead-reckoning'i donanimin gercek konumuyla (117.98 derece gibi)
        esitliyoruz; boylece asagidaki SweepGenerator/SLEWING_TO_CENTER
        mantigi ilk tick'ten itibaren doğru hata (error) degeriyle calisir.
        """
        if self._running and self._task is not None and not self._task.done():
            return
        runtime = self.gateway.runtime
        if runtime is not None:
            motion_st = getattr(runtime, "motion", None)
            if motion_st is not None and hasattr(motion_st, "status"):
                st = motion_st.status()
                if st.physical_pan_deg is not None and not math.isnan(st.physical_pan_deg):
                    self._dead_reckon_pan_deg = float(st.physical_pan_deg)
                    self._current_pan_speed_sps = 0.0
                if st.physical_tilt_deg is not None and not math.isnan(st.physical_tilt_deg):
                    self._dead_reckon_tilt_deg = float(st.physical_tilt_deg)
                    self._current_tilt_speed_sps = 0.0
            if hasattr(self.gateway, "get_position"):
                try:
                    pos = await asyncio.to_thread(self.gateway.get_position, runtime)
                    if pos:
                        if pos.pan_deg is not None and not math.isnan(pos.pan_deg):
                            self._dead_reckon_pan_deg = float(pos.pan_deg)
                            self._current_pan_speed_sps = 0.0
                        if pos.tilt_deg is not None and not math.isnan(pos.tilt_deg):
                            self._dead_reckon_tilt_deg = float(pos.tilt_deg)
                            self._current_tilt_speed_sps = 0.0
                except Exception:
                    pass  # POZ okunamazsa mevcut tahminle devam (fail-safe)
        self._stop_cleanup_owned_by_caller = False
        self._running = True
        self._task = asyncio.create_task(self._run())
        self.logger.emit(LogLevel.INFO, "TRACKING_LOOP", "Tracking loop started", {"interval_ms": self.interval_s * 1000, "yarisma_bayraklari": yb.durum()})

    async def stop(
        self,
        *,
        preserve_actuator_arm: bool = True,
        reason: str = "explicit_full_safe_stop",
    ) -> None:
        """Stop tracking output without conflating it with trigger permission.

        A target-policy/control-mode handoff or normal stop must cancel velocity
        and active motion, but preserves the operator's visible FIRE toggle
        and driver authority unless explicitly requested by emergency shutdown.
        """
        self._engage_debouncer.reset()
        serial = self.gateway.serial
        pre_connection_state = getattr(getattr(serial, "connection_state", None), "value", getattr(serial, "connection_state", None))
        pre_last_error = getattr(serial, "last_error", None)
        pre_log_count = len(getattr(serial, "logs", []))
        self._running = False
        self._stop_cleanup_owned_by_caller = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.gateway.runtime is not None:
            serial.gateway_safe_stop()
            # The former second CommandGateway.stop_motion() call also cleared
            # these in-memory estimates/leases. Preserve that bookkeeping
            # without emitting its duplicate STP/DRV,0 serial sequence.
            self.gateway.cancel_manual_motion_lease()
            self.gateway._set_home_in_progress(False)
            self.gateway._stop_pose_estimate(
                self.gateway.runtime,
                command="tracking_loop_stop",
            )
            # Operator trigger arm intent is durable; never auto-disarm or disable fire permission on tracking stop.
            pass
        self.is_firing = False
        self._reset_fire_candidate()
        post_connection_state = getattr(getattr(serial, "connection_state", None), "value", getattr(serial, "connection_state", None))
        post_last_error = getattr(serial, "last_error", None)
        post_log_count = len(getattr(serial, "logs", []))
        self._stop_cleanup_owned_by_caller = False
        self.logger.emit(
            LogLevel.INFO if post_connection_state == pre_connection_state else LogLevel.WARN,
            "TRACKING_LOOP",
            "Tracking loop stopped",
            {
                "reason": reason,
                "preserve_actuator_arm": preserve_actuator_arm,
                "total_loops": self.loop_count,
                "serial_command_count_delta": max(0, post_log_count - pre_log_count),
                "serial_state_before": pre_connection_state,
                "serial_state_after": post_connection_state,
                "serial_error_before": pre_last_error,
                "serial_error_after": post_last_error,
            },
        )

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def drain_events(self) -> list[tuple[str, dict]]:
        events = self._events
        self._events = []
        return events

    def reset_fire_candidate(self) -> None:
        """Invalidate lock/FIRE carry-over after an operator state change."""
        self._reset_fire_candidate()

    def _scan_tilt_deg(self) -> float:
        """Devriye/arama sirasinda hedeflenecek FIZIKSEL tilt (derece).

        SORUN A/D DUZELTMESI: Bu artik AutoHome'un mekanik merkezinden
        (HOME_TILT_DEG=30.0) BAGIMSIZDIR. patrol_radar.config.scan_tilt_deg
        sahada (koda dokunmadan, API/arayuzden) ayarlanabilir; tanimli
        degilse FIELD_SCAN_TILT_DEG varsayilanina (23.0) duser. Boylece:
          - AutoHome sonrasi dead-reckoning hala 30.0'a gore kalibre olur
            (Sorun A'nin "beklenmeyen sifir noktasi" kismi cozulur),
          - devriye/SLEWING_TO_CENTER GERCEKTEN hedeflerin goruldugu
            irtifada arar, boylece kilit anindaki PID tilt hareketi
            kucuk kalir ve "bobbing" (Sorun D) ortadan kalkar.
        """
        runtime = self.gateway.runtime
        patrol_radar = getattr(runtime, "patrol_radar", None) if runtime is not None else None
        if patrol_radar is not None:
            configured = getattr(patrol_radar.config, "scan_tilt_deg", None)
            try:
                if configured is not None and not math.isnan(float(configured)):
                    return float(configured)
            except (TypeError, ValueError):
                pass
        return self.FIELD_SCAN_TILT_DEG

    def _update_dead_reckoning(self, commanded_pan_sps: int | float, dt: float) -> float:
        """Updates estimated pan angle using software step integration: theta += v * dt / 133.333."""
        deg_delta = (float(commanded_pan_sps) * dt) / self.STEPS_PER_DEG_PAN
        self._dead_reckon_pan_deg += deg_delta
        self._current_pan_speed_sps = float(commanded_pan_sps)
        return self._dead_reckon_pan_deg

    def _calibrate_dead_reckoning(self, measured_pan_deg: float) -> None:
        """Calibrates dead-reckoning estimate only when axis is at rest (speed == 0)."""
        if abs(self._current_pan_speed_sps) < 50.0 and not math.isnan(measured_pan_deg):
            self._dead_reckon_pan_deg = float(measured_pan_deg)

    def _update_dead_reckoning_tilt(self, commanded_tilt_sps: int | float, dt: float) -> float:
        """Updates estimated tilt angle using software step integration.
        In semantic coordinates, +Y tilts UP, which DECREASES physical tilt (physical_tilt = 30 - tilt_semantic).
        """
        deg_delta = (float(commanded_tilt_sps) * dt) / self.STEPS_PER_DEG_TILT
        self._dead_reckon_tilt_deg -= deg_delta
        self._current_tilt_speed_sps = float(commanded_tilt_sps)
        return self._dead_reckon_tilt_deg

    def _calibrate_dead_reckoning_tilt(self, measured_tilt_deg: float) -> None:
        """Calibrates dead-reckoning tilt estimate only when axis is at rest (speed == 0)."""
        if abs(self._current_tilt_speed_sps) < 50.0 and not math.isnan(measured_tilt_deg):
            self._dead_reckon_tilt_deg = float(measured_tilt_deg)

    def _compute_smooth_return_to_home_speed(self, target_pan_deg: float, dt: float) -> int:
        """Computes chatter-free velocity profile using CNC stopping distance: v^2 = 2 * a * d.

        Eliminates the start-stop motor chatter ('tık tık') and smoothly converges to target_pan_deg.
        """
        error_deg = target_pan_deg - self._dead_reckon_pan_deg
        max_dv = self.MAX_RETURN_ACCEL_SPS2 * dt

        if abs(error_deg) < self.HOME_TOLERANCE_DEG and abs(self._current_pan_speed_sps) <= max_dv:
            self._current_pan_speed_sps = 0.0
            return 0

        error_steps = error_deg * self.STEPS_PER_DEG_PAN
        v_stop = math.sqrt(2.0 * self.MAX_RETURN_ACCEL_SPS2 * max(0.0, abs(error_steps)))
        v_target = math.copysign(min(self.MAX_RETURN_SPEED_SPS, v_stop), error_steps)

        dv = max(-max_dv, min(max_dv, v_target - self._current_pan_speed_sps))
        self._current_pan_speed_sps += dv

        return int(round(self._current_pan_speed_sps))

    def _compute_smooth_return_to_tilt_speed(self, target_tilt_deg: float, dt: float) -> int:
        """Computes chatter-free velocity profile for tilt using CNC stopping distance: v^2 = 2 * a * d.
        target_tilt_deg and _dead_reckon_tilt_deg are in physical degrees (0..60).
        To decrease physical tilt (tilt up towards target), positive speed_y is commanded.
        """
        error_deg = self._dead_reckon_tilt_deg - target_tilt_deg
        max_dv = self.MAX_RETURN_ACCEL_SPS2 * dt

        if abs(error_deg) < self.HOME_TOLERANCE_DEG and abs(self._current_tilt_speed_sps) <= max_dv:
            self._current_tilt_speed_sps = 0.0
            return 0

        error_steps = error_deg * self.STEPS_PER_DEG_TILT
        v_stop = math.sqrt(2.0 * self.MAX_RETURN_ACCEL_SPS2 * max(0.0, abs(error_steps)))
        v_target = math.copysign(min(self.MAX_RETURN_SPEED_SPS, v_stop), error_steps)

        dv = max(-max_dv, min(max_dv, v_target - self._current_tilt_speed_sps))
        self._current_tilt_speed_sps += dv

        return int(round(self._current_tilt_speed_sps))

    async def _async_sync_hardware_position(self, runtime: Any, *, force: bool = False) -> None:
        """Arka planda donanim POZ sorgusunu calistirir ve dead-reckoning'i kalibre eder.
        
        POZ sorgularinin sonucunu dogrudan _dead_reckon_pan_deg ve _dead_reckon_tilt_deg'e
        uygulayarak acik cevrim integrasyon kaymasini sifirlar.
        """
        if getattr(self, "_poz_query_in_progress", False):
            return
        self._poz_query_in_progress = True
        try:
            if runtime is not None and hasattr(self.gateway, "get_position"):
                pos = await asyncio.to_thread(self.gateway.get_position, runtime)
                if pos:
                    if pos.pan_deg is not None and not math.isnan(pos.pan_deg):
                        self._dead_reckon_pan_deg = float(pos.pan_deg)
                    if pos.tilt_deg is not None and not math.isnan(pos.tilt_deg):
                        self._dead_reckon_tilt_deg = float(pos.tilt_deg)
        except Exception:
            pass
        finally:
            self._poz_query_in_progress = False

    async def _sync_position_at_rest(self, runtime: Any) -> None:
        """Asynchronously syncs position via POZ when motor is stopped."""
        await self._async_sync_hardware_position(runtime, force=True)

    async def _get_synced_pan(self, runtime: Any, force_fresh: bool = False, *, moving: bool = False) -> float:
        """Taret acisi: hareket halinde dead-reckoning step entegrasyonu (0 ms), periyodik arka plan POZ.

        - moving=True: Aci anlik olarak yazilimsal integrator (_dead_reckon_pan_deg)'den okunur;
          arka planda ~5 Hz (0.20s) korumali POZ sorgusu ile acik cevrim kaymasi onlenir.
        - moving=False: Durus aninda (hiz=0) dogrudan POZ ile kalibre edilir.
        """
        now = time.monotonic()
        if not moving or force_fresh:
            sync_interval = 0.20
            if (now - getattr(self, "_last_pan_sync_mono", 0.0)) >= sync_interval or force_fresh:
                if hasattr(self.gateway, "get_position"):
                    try:
                        pos = await asyncio.to_thread(self.gateway.get_position, runtime)
                        if pos:
                            if pos.pan_deg is not None and not math.isnan(pos.pan_deg):
                                self._last_pan_sync_mono = now
                                self._dead_reckon_pan_deg = float(pos.pan_deg)
                            if pos.tilt_deg is not None and not math.isnan(pos.tilt_deg):
                                self._dead_reckon_tilt_deg = float(pos.tilt_deg)
                            if pos.pan_deg is not None:
                                return float(pos.pan_deg)
                    except Exception:
                        pass
        elif moving and runtime is not None:
            # Hareket halinde arka planda ~5 Hz POZ duzeltmesi (USB buffer sisirmeden kaymayi sifirlar)
            last_bg = getattr(self, "_last_background_poz_mono", 0.0)
            if last_bg > 0.0 and (now - last_bg) >= 0.20:
                self._last_background_poz_mono = now
                asyncio.create_task(self._async_sync_hardware_position(runtime, force=True))
            elif last_bg == 0.0:
                self._last_background_poz_mono = now

        return float(getattr(self, "_dead_reckon_pan_deg", 135.0))

    def _patrol_candidate_visible(self, vision_event, frame_width: int) -> bool:
        """Devriye sırasında bu karede kapı içinde aday balon var mı?

        Yalnızca görünürlük kontrolüdür: hedef seçimi ve motor komutu
        debounce onayından sonra auto_tracker.update() içinde yapılır.
        FOV angajman kapısı (varsa) aday filtresinde de uygulanır.
        Dost balonlar devriye adaylığına alınmaz (hedef kilitlenmesi engellenir).
        """
        if vision_event is None:
            return False
        balloons = getattr(vision_event, "balloon_detections", None) or []
        if not balloons:
            return False

        # ASAMA BIRLESTIRME KARARI: Onceden Asama 2/BALLOON burada erken "return
        # True" ile Asama 3'un dost-balon disleme mantigini (asagida) TAMAMEN
        # atliyordu ("BALLOON_ONLY" ozel yolu, active_stage/target_policy'ye
        # bakarak). Karar geregi artik TEK motor (Asama 3) her aşamada aynı
        # sekilde calisir: Asama 2 parkurunda dost balon hic olmadigi icin
        # asagidaki dislama kontrolu zaten hicbir seyi elemeyecek ve sonuc
        # ozdes olacaktir - ama artik iki ayri kod yolu yerine TEK, sahada
        # kanitlanmis yol var. Aşama/politika bilgisi hala debounce (bkz.
        # _run icindeki is_stage2/is_transiting) ve telemetri/skor
        # etiketlemesi icin baska yerlerde okunuyor; sadece burada gerek yok.

        # Aşama 3 Dost balon koruması: kokpit kararlarında veya dost gövdeler altında olanları hariç tut
        friend_detection_ids = set()
        if hasattr(vision_event, "target_verdicts") and vision_event.target_verdicts:
            for v in vision_event.target_verdicts:
                if getattr(v, "kind", "") == "balloon" and (
                    str(getattr(v, "target_team", "")).lower() in {"friend", "dost"}
                    or getattr(v, "verdict_state", "") == "FRIEND_LOCKED"
                    or (hasattr(v, "label_tr") and "DOST" in str(v.label_tr).upper())
                ):
                    if getattr(v, "detection_id", None) is not None:
                        friend_detection_ids.add(v.detection_id)

        friendly_bodies = [
            b for b in getattr(vision_event, "body_detections", [])
            if str(getattr(b, "target_team", "")).lower() in {"friend", "dost"}
        ]

        center_x = float(frame_width) / 2.0
        gate = getattr(self.auto_tracker, "inside_engagement_gate", None)
        for item in balloons:
            if getattr(item, "id", None) in friend_detection_ids:
                continue
            try:
                cx = float(item.center_x)
                cy = float(item.center_y)
            except (TypeError, ValueError, AttributeError):
                continue
            if any(stage3_roi.attachment_roi_contains(fb.bbox, cx, cy) for fb in friendly_bodies):
                continue
            if gate is None or gate(cx, center_x, is_already_locked=False):
                return True
        return False

    async def _run(self) -> None:
        """Ana tracking döngüsü."""
        while self._running:
            try:
                t0 = time.time()
                if self.gateway.runtime is not None:
                    self.gateway.tick(self.gateway.runtime)

                # Tracker aktif değilse bekle
                if not self.auto_tracker.tracking_active:
                    await asyncio.sleep(0.1)
                    continue

                # 1. Son vision event'i al
                vision_event = self.vision_pipeline.latest()
                # Y17: Asama3+BALON gosterim govdeleri takip/IFF/atis yoluna GIRMEZ.
                if vision_event is not None and hasattr(self.vision_pipeline, "y17_kontrol_olayi"):
                    vision_event = self.vision_pipeline.y17_kontrol_olayi(vision_event)
                frame_width, frame_height = self._frame_size()
                if not self._vision_event_is_fresh(vision_event):
                    self._handle_stale_vision(vision_event)
                    vision_event = None

                # 2. Perception identity is resolved before PID selection.
                runtime = self.gateway.runtime
                patrol_radar = getattr(runtime, "patrol_radar", None) if runtime is not None else None
                patrol_enabled = patrol_radar is not None and getattr(patrol_radar.config, "patrol_enabled", False)
                if hasattr(self.auto_tracker, "patrol_gate_active"):
                    radar_state = getattr(patrol_radar, "state", "IDLE") if patrol_radar else "IDLE"
                    radar_strategy = getattr(patrol_radar.config, "strategy", "CORRIDOR_HOP") if patrol_radar else "CORRIDOR_HOP"
                    self.auto_tracker.patrol_gate_active = bool(
                        patrol_enabled
                        and radar_strategy == "CORRIDOR_HOP"
                        and radar_state != "TARGET_ENGAGING"
                    )

                if patrol_enabled and getattr(patrol_radar, "state", "IDLE") != "TARGET_ENGAGING":
                    strategy = getattr(patrol_radar.config, "strategy", "CORRIDOR_HOP")
                    active_stage = getattr(runtime.mission.state, "active_stage", "") if runtime and hasattr(runtime, "mission") else ""
                    target_policy = getattr(runtime.operation.state(), "target_policy", None) if runtime and hasattr(runtime, "operation") else None
                    target_policy_val = target_policy.value if hasattr(target_policy, "value") else str(target_policy or "")
                    is_stage2 = active_stage in {"stage1", "stage2"} or target_policy_val in {"BALLOON", "STAGE1_INDEPENDENT"}

                    # SORUN C KOK NEDEN DUZELTMESI: Onceki kod, Asama 2/1 icin
                    # debounce esigini HER ZAMAN 2 kareye ("anında") sabitliyordu
                    # - taret SLEWING_TO_CENTER ile merkeze donerken (kamera
                    # hareket halinde, saha raporundaki "kadrajdan gecen isik/
                    # yansima/dost 2 kare (30ms) gorulunce intikali kesiyor")
                    # DAHIL. Simdi esik radar DURUMUNA gore secilir:
                    #   - Kamera hareket halindeyken (SLEWING_TO_CENTER / SLEWING /
                    #     PATROLLING): STAGE'DEN BAGIMSIZ, yuksek bir esik
                    #     (>=5 kare) zorunlu - gecici parazit intikali kesemez.
                    #   - Kamera sabit tarama/inceleme yapiyorken (SWEEPING /
                    #     INSPECTING / SETTLING-sonrasi): Asama 2/1 icin hizli
                    #     (2 kare) kilitlenme korunur (statik balonlar icin
                    #     onemli), Asama 3 icin 2-3 kare.
                    radar_state_now = getattr(patrol_radar, "state", "SWEEPING")
                    is_transiting = radar_state_now in {"SLEWING_TO_CENTER", "SLEWING", "PATROLLING"}
                    if is_transiting:
                        debounce_target = max(5, int(getattr(patrol_radar, "consecutive_detections_required", 5) or 5))
                    else:
                        debounce_target = 2 if is_stage2 else max(2, min(3, int(getattr(patrol_radar, "consecutive_detections_required", 2) or 2)))
                    self._engage_debouncer.required = debounce_target
                    candidate_seen = self._patrol_candidate_visible(vision_event, frame_width)
                    if strategy == "SMOOTH_SWEEP":
                        confirmed = self._engage_debouncer.observe(candidate_seen)
                        if confirmed:
                            # Onaylandı: radar TARGET_ENGAGING'e geçer, bu kare
                            # aşağıda auto_tracker.update() ile takibe düşer.
                            patrol_radar.tick(has_active_target=True)
                        else:
                            radar_state = getattr(patrol_radar, "state", "SWEEPING")

                            if radar_state == "SLEWING_TO_CENTER":
                                # Donanim gercek konumunu ~10 Hz (100 ms) kadansla sorgulayarak
                                # acik cevrim integratorunun donanimin onune gecmesini onle:
                                now_mono = time.monotonic()
                                if (now_mono - getattr(self, "_last_slew_poz_mono", 0.0)) >= 0.10:
                                    self._last_slew_poz_mono = now_mono
                                    if runtime is not None and hasattr(self.gateway, "get_position"):
                                        try:
                                            pos = await asyncio.to_thread(self.gateway.get_position, runtime)
                                            if pos:
                                                if pos.pan_deg is not None and not math.isnan(pos.pan_deg):
                                                    self._dead_reckon_pan_deg = float(pos.pan_deg)
                                                if pos.tilt_deg is not None and not math.isnan(pos.tilt_deg):
                                                    self._dead_reckon_tilt_deg = float(pos.tilt_deg)
                                        except Exception:
                                            pass

                                current_pan = float(self._dead_reckon_pan_deg)
                                pan_err = self.HOME_PAN_DEG - current_pan
                                tilt_err = self._scan_tilt_deg() - self._dead_reckon_tilt_deg

                                centering_speed = self._compute_smooth_return_to_home_speed(self.HOME_PAN_DEG, dt=0.015)
                                centering_tilt = self._compute_smooth_return_to_tilt_speed(self._scan_tilt_deg(), dt=0.015)
                                self.gateway.send_motion(runtime, centering_speed, centering_tilt, "slew_to_center")
                                self._update_dead_reckoning(centering_speed, dt=0.015)
                                self._update_dead_reckoning_tilt(centering_tilt, dt=0.015)
                                self._reset_fire_candidate()

                                center_pan_tol = getattr(patrol_radar, "SWEEP_CENTER_TOLERANCE_DEG", 0.75)
                                center_tilt_tol = 1.5
                                if abs(pan_err) <= center_pan_tol and abs(tilt_err) <= center_tilt_tol:
                                    # Donanim gercek konumunu Pico'dan son kez senkron teyit et
                                    _y12_teyit = False
                                    if runtime is not None and hasattr(self.gateway, "get_position"):
                                        try:
                                            pos = await asyncio.to_thread(self.gateway.get_position, runtime)
                                            if pos:
                                                if pos.pan_deg is not None and not math.isnan(pos.pan_deg):
                                                    self._dead_reckon_pan_deg = float(pos.pan_deg)
                                                    _y12_teyit = bool(getattr(pos, "accepted", True))
                                                if pos.tilt_deg is not None and not math.isnan(pos.tilt_deg):
                                                    self._dead_reckon_tilt_deg = float(pos.tilt_deg)
                                        except Exception:
                                            pass

                                    real_pan_err = abs(self.HOME_PAN_DEG - self._dead_reckon_pan_deg)
                                    real_tilt_err = abs(self._scan_tilt_deg() - self._dead_reckon_tilt_deg)
                                    _y12_izin = True
                                    if yb.Y12_RADAR_POZ_TEYIT and not _y12_teyit and real_pan_err <= center_pan_tol and real_tilt_err <= center_tilt_tol:
                                        # Y12: merkez yalniz TAHMINE gore; gercek POZ teyidi yok. 3 s bekle, sonra uyar ve devam et.
                                        _y12_simdi = time.monotonic()
                                        if not getattr(self, "_y12_red_bas", 0.0):
                                            self._y12_red_bas = _y12_simdi
                                        _y12_izin = (_y12_simdi - self._y12_red_bas) >= 3.0
                                        if _y12_izin:
                                            self.logger.emit(LogLevel.WARN, "TRACKING_LOOP", "Radar merkezi POZ ile teyit edilemedi; tahmine gore supuruluyor", {"pan_tahmin": self._dead_reckon_pan_deg, "tilt_tahmin": self._dead_reckon_tilt_deg})
                                    if real_pan_err <= center_pan_tol and real_tilt_err <= center_tilt_tol and _y12_izin:
                                        self._y12_red_bas = 0.0
                                        self.gateway.send_motion(runtime, 0, 0, "center_reached")
                                        self._current_pan_speed_sps = 0.0
                                        self._current_tilt_speed_sps = 0.0
                                        if hasattr(patrol_radar, "notify_centered"):
                                            patrol_radar.notify_centered(self._dead_reckon_pan_deg)

                                patrol_radar.tick(has_active_target=False)
                                self.loop_count += 1
                                controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                                self.last_update = TrackingUpdate(
                                    state=TrackingState.SEARCHING,
                                    frame_center_x=frame_width / 2.0,
                                    frame_center_y=frame_height / 2.0,
                                    frame_id=int(vision_event.frame_id) if vision_event is not None else 0,
                                    dt=0.015,
                                    target_lost_frames=0,
                                    controller_mode=controller_mode,
                                )
                                await asyncio.sleep(0.015)
                                continue

                            # --- Zaten merkezde: normal sinusoidal sweep (degismedi) ---
                            # Option B: Continuous smooth sweep across 30 deg sector.
                            # Aday görüldüğünde süpürme yumuşakça yavaşlar (asla
                            # durmaz/geri dönmez): onay kareleri netleşir, balon
                            # FOV'da tutulur, Y ekseni 0'da kilitli kalır.
                            decel = 1.0
                            if self._engage_debouncer.streak > 0:
                                decel = max(0.25, 1.0 - 0.25 * self._engage_debouncer.streak)
                            # Sweep hareket halindedir: açık çevrim açı + 1 Hz POZ düzeltmesi.
                            current_pan = await self._get_synced_pan(runtime, moving=True)
                            sweep_gen = getattr(patrol_radar, "sweep_generator", None)
                            omega_dps = (sweep_gen.velocity_dps(dt=0.015, current_deg=current_pan) if sweep_gen else 7.5) * decel
                            pan_scale = float(runtime.config.motion.pan_steps_per_degree) if (runtime and hasattr(runtime, "config")) else 133.333
                            sweep_speed = int(round(omega_dps * pan_scale))
                            # Arama/süpürme fazında gateway tilt'i koşulsuz kilitler (speed_y=0);
                            # bu yüzden tilt hızı 0 olarak gönderilir ve dead reckoning kaydırılmaz.
                            self.gateway.send_motion(runtime, sweep_speed, 0, "sweep")
                            self._update_dead_reckoning(sweep_speed, dt=0.015)
                            self._update_dead_reckoning_tilt(0, dt=0.015)
                            self._reset_fire_candidate()
                            patrol_radar.tick(has_active_target=False)
                            self.loop_count += 1
                            controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                            self.last_update = TrackingUpdate(
                                state=TrackingState.SEARCHING,
                                frame_center_x=frame_width / 2.0,
                                frame_center_y=frame_height / 2.0,
                                frame_id=int(vision_event.frame_id) if vision_event is not None else 0,
                                dt=0.015,
                                target_lost_frames=0,
                                controller_mode=controller_mode,
                            )
                            await asyncio.sleep(0.015)
                            continue
                    else:
                        active_path = getattr(patrol_radar.config, "active_path", 2)
                        latched_path = getattr(patrol_radar, "latched_path", None)
                        radar_state = getattr(patrol_radar, "state", "")

                        if latched_path == active_path:
                            # Bu kola zaten varıldı ve kilitlendi (Histerezis avlanması / tık-tık önlendi).
                            is_settle_complete = getattr(patrol_radar, "is_settle_complete", True)
                            if not is_settle_complete or radar_state == "SETTLING":
                                # 0.5 saniyelik mekanik sönümlenme evresi: motorlar kesin 0, görüntü işleme/hedef arama kapalı!
                                self._engage_debouncer.reset()  # sarsıntılı kareler aday sayılmaz
                                if is_settle_complete:
                                    if hasattr(patrol_radar, "notify_on_path"):
                                        patrol_radar.notify_on_path()
                                else:
                                    if radar_state != "SETTLING" and hasattr(patrol_radar, "notify_settling"):
                                        patrol_radar.notify_settling()
                                    slew_tilt = self._compute_smooth_return_to_tilt_speed(self._scan_tilt_deg(), dt=0.015)
                                    self.gateway.send_motion(runtime, 0, slew_tilt, "tracking")
                                    self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
                                    self._reset_fire_candidate()
                                    patrol_radar.tick(has_active_target=False)
                                    self.loop_count += 1
                                    controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                                    self.last_update = TrackingUpdate(
                                        state=TrackingState.SEARCHING,
                                        frame_center_x=frame_width / 2.0,
                                        frame_center_y=frame_height / 2.0,
                                        frame_id=int(vision_event.frame_id) if vision_event is not None else 0,
                                        dt=0.015,
                                        target_lost_frames=0,
                                        controller_mode=controller_mode,
                                    )
                                    await asyncio.sleep(0.015)
                                    continue
                            # Sönümlenme bitti -> INSPECTING (1.0 sn inceleme/arama).
                            # Aday hedef debounce ile doğrulanır: N ardışık kare
                            # görülmeden takibe devredilmez; aday evresinde motorlar
                            # durur (Y kilitli), radar ve dwell sayacı kesintisiz işler.
                            confirmed = self._engage_debouncer.observe(candidate_seen)
                            if confirmed:
                                patrol_radar.tick(has_active_target=True)
                                # Onaylandı: döngü aşağı düşer ve takip başlar.
                            elif self._engage_debouncer.streak > 0:
                                # Aday evresi (1..N-1 kareler): kilitlenme yok, radar sürüyor.
                                slew_tilt = self._compute_smooth_return_to_tilt_speed(self._scan_tilt_deg(), dt=0.015)
                                self.gateway.send_motion(runtime, 0, slew_tilt, "tracking")
                                self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
                                self._reset_fire_candidate()
                                patrol_radar.tick(has_active_target=False)
                                self.loop_count += 1
                                controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                                self.last_update = TrackingUpdate(
                                    state=TrackingState.SEARCHING,
                                    frame_center_x=frame_width / 2.0,
                                    frame_center_y=frame_height / 2.0,
                                    frame_id=int(vision_event.frame_id) if vision_event is not None else 0,
                                    dt=0.015,
                                    target_lost_frames=0,
                                    controller_mode=controller_mode,
                                )
                                await asyncio.sleep(0.015)
                                continue
                            # Aday yok: mevcut davranış — döngü aşağıya devam eder,
                            # auto_tracker.update() SEARCHING döner ve radar işler.
                        else:
                            # Henüz bu kola varılmadı, intikal (slew) veya ilk frenleme:
                            self._engage_debouncer.reset()  # intikal kareleri aday sayılmaz
                            target_pan_deg = float(patrol_radar.get_active_path_angle())
                            # İntikal (slew) sırasında açık çevrim açı; settling/duruşta taze POZ.
                            radar_state = str(getattr(patrol_radar, "state", ""))
                            transit_moving = radar_state in {"PATROLLING", "SLEWING", "SWEEPING"}
                            current_pan = await self._get_synced_pan(runtime, moving=transit_moving)
                            pan_err = target_pan_deg - current_pan

                            if abs(pan_err) > 2.0:
                                # Kollar arası yüksek hızlı intikal (slew): Maks 7500 sps!
                                # Kullanıcı kuralı: Geçerken görüntü işleme yapmayacak, takip ve tetik olmayacak!
                                if hasattr(patrol_radar, "notify_slewing"):
                                    patrol_radar.notify_slewing()
                                slew_speed = int(math.copysign(min(max(abs(pan_err) * 350.0, 1200.0), 7500.0), pan_err))
                                slew_tilt = self._compute_smooth_return_to_tilt_speed(self._scan_tilt_deg(), dt=0.015)
                                self.gateway.send_motion(runtime, slew_speed, slew_tilt, "tracking")
                                self._update_dead_reckoning(slew_speed, dt=0.015)
                                self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
                                self._reset_fire_candidate()
                                patrol_radar.tick(has_active_target=False)
                                self.loop_count += 1
                                controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                                self.last_update = TrackingUpdate(
                                    state=TrackingState.SEARCHING,
                                    frame_center_x=frame_width / 2.0,
                                    frame_center_y=frame_height / 2.0,
                                    frame_id=int(vision_event.frame_id) if vision_event is not None else 0,
                                    dt=0.015,
                                    target_lost_frames=0,
                                    controller_mode=controller_mode,
                                )
                                await asyncio.sleep(0.015)
                                continue
                            else:
                                # Kola 2.0 dereceden az kaldı: VARILDI & AKTİF FREN (SETTLING BAŞLAR)
                                if hasattr(patrol_radar, "notify_settling"):
                                    patrol_radar.notify_settling()
                                slew_tilt = self._compute_smooth_return_to_tilt_speed(self._scan_tilt_deg(), dt=0.015)
                                self.gateway.send_motion(runtime, 0, slew_tilt, "tracking")
                                self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
                                self._reset_fire_candidate()
                                patrol_radar.tick(has_active_target=False)
                                self.loop_count += 1
                                controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                                self.last_update = TrackingUpdate(
                                    state=TrackingState.SEARCHING,
                                    frame_center_x=frame_width / 2.0,
                                    frame_center_y=frame_height / 2.0,
                                    frame_id=int(vision_event.frame_id) if vision_event is not None else 0,
                                    dt=0.015,
                                    target_lost_frames=0,
                                    controller_mode=controller_mode,
                                )
                                await asyncio.sleep(0.015)
                                continue

                control_event = vision_event
                tracks = None
                associations = None
                confirmations = None
                operation_state = None
                if runtime is not None:
                    # The single-cockpit operation contract is the source of
                    # truth for target type. The standard cockpit balloon
                    # policy maps to Light General; explicit A2/A3 stages
                    # remain separate.
                    operation = getattr(runtime, "operation", None)
                    operation_state = operation.state() if operation is not None and hasattr(operation, "state") else None
                    if operation_state is not None:
                        self.auto_tracker.set_target_policy(operation_state.target_policy.value)
                    current_mode = getattr(self.auto_tracker, "controller_mode", None)
                    op_stage = getattr(operation_state, "competition_stage", None) if operation_state is not None else None
                    op_stage_val = op_stage.value if hasattr(op_stage, "value") else str(op_stage or "").upper()
                    op_policy = getattr(operation_state, "target_policy", None) if operation_state is not None else None
                    op_policy_val = op_policy.value if hasattr(op_policy, "value") else str(op_policy or "").upper()
                    mission_stage = str(getattr(runtime.mission.state, "active_stage", "")).lower()

                    is_stage3 = (
                        mission_stage == "stage3"
                        or op_stage_val in {"STAGE_3", "STAGE3"}
                        or op_policy_val in {"BALLOON_AIRCRAFT", "STAGE_3"}
                    )

                    if is_stage3:
                        target_mode = "A3"
                    elif current_mode in {"GENERAL", "A2", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}:
                        target_mode = current_mode
                    else:
                        target_mode = self._controller_mode_for_stage(
                            runtime.mission.state.active_stage,
                            op_policy_val or getattr(self.auto_tracker, "target_policy", "BALLOON"),
                        )
                    self.auto_tracker.set_controller_mode(target_mode)
                    controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                    if controller_mode in {"GENERAL", "A2", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}:
                        # Light balloon tracking: bypass multi-target, association, evidence, and registry pipelines
                        control_event = vision_event
                    else:
                        tracks = runtime.auto_tracker.multi_target_tracker.update(vision_event)
                        associations = runtime.association.update(vision_event, tracks)
                        confirmations = runtime.hit_confirmation.update(vision_event, tracks)
                        runtime.target_registry.update(
                            vision_event,
                            tracks,
                            associations,
                            confirmations,
                            target_policy=operation_state.target_policy.value if operation_state is not None else None,
                        )
                        control_event = runtime.target_registry.filter_event_for_tracking(
                            vision_event,
                            tracks,
                            associations,
                            target_policy=operation_state.target_policy.value if operation_state is not None else "BALLOON",
                            selected_detection_id=operation_state.selected_detection_id if operation_state is not None else None,
                            selected_detection_kind=(operation_state.selected_detection_kind.value if operation_state is not None and operation_state.selected_detection_kind is not None else None),
                            selected_body_track_id=operation_state.selected_body_track_id if operation_state is not None else None,
                        )

                update = self.auto_tracker.update(
                    control_event,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    selection_event=control_event,
                    update_multi_target=runtime is None,
                )
                if vision_event is not None and getattr(vision_event, "target_verdicts", None):
                    update = update.model_copy(update={"target_verdicts": list(vision_event.target_verdicts)})
                self.last_update = update
                self._emit_timebase_trace(control_event, update)
                if self.tuning is not None:
                    self.tuning.observe(update)
                if runtime is not None and tracks is not None:
                    runtime.target_priority.update(
                        tracks,
                        associations,
                        frame_width,
                        frame_height,
                        update.frame_center_x,
                        update.frame_center_y,
                        allowed_body_detection_ids=(
                            {body.id for body in vision_event.body_detections if getattr(body, "target_team", "") == "enemy"}
                            if is_stage3 and vision_event is not None
                            else None
                        ),
                        target_registry=runtime.target_registry,
                    )
                    # In competition A2/A3 the tracker must converge on the
                    # ranked stable association, not the nearest raw balloon.
                    # It is an in-memory guidance preference only; physical
                    # motion still passes through CommandGateway below.
                    if (
                        is_stage3 or runtime.mission.state.active_stage in {"stage2", "stage3"}
                    ):
                        selected_id = runtime.target_priority.status().selected_track_id
                        selected = next((item for item in tracks.tracks if item.track_id == selected_id), None)
                        if selected is not None:
                            is_friendly = False
                            sel_assoc = next((a for a in associations.associations if a.balloon_track_id == selected.track_id), None) if associations else None
                            if sel_assoc and sel_assoc.body_detection_id is not None and vision_event:
                                linked_b = next((b for b in vision_event.body_detections if b.id == sel_assoc.body_detection_id), None)
                                if linked_b and str(getattr(linked_b, "target_team", "")).lower() in {"friend", "dost"}:
                                    is_friendly = True
                            if not is_friendly:
                                self.auto_tracker.preferred_target_x = selected.center_x
                                self.auto_tracker.preferred_target_y = selected.center_y
                    if not yb.Y1_KANIT_KAPALI:
                        # Y1 açıkken bu gözlemsel kayıt (manifest/JPEG/ikiz) kontrol döngüsünde HİÇ çalışmaz.
                        runtime.engagement_evidence.record_confirmation_status(confirmations)
                        runtime.engagement_evidence.observe_frame(
                            vision_event,
                            update,
                            tracks,
                            associations,
                            mission_stage=runtime.mission.state.active_stage,
                            command_profile=self.gateway.profile.value,
                        )
                        runtime.engagement_evidence.capture_active_camera_frame(runtime.camera_runtime)
                        runtime.engagement_evidence.capture_active_digital_twin_state(lambda: runtime.digital_twin.state(runtime))
                        runtime.engagement_evidence.finalize_due_recording()
                    if runtime.mission.state.active_stage == "stage2":
                        runtime.stage2_engagement.observe(confirmations, runtime.mission.state.stage2_round)
                    elif runtime.mission.state.active_stage == "stage3":
                        runtime.stage3_engagement.observe(
                            vision_event,
                            tracks,
                            confirmations,
                            runtime.mission.state.stage3_round,
                        )
                self.loop_count += 1

                # 3. Motor komutunu rate-limit ile gönder. USB CDC buffer'ı
                # şişerse fire komutu eski SPD paketlerinin arkasına düşer.
                target_absent = self._safe_stop_on_target_loss(update)
                if not target_absent and self._should_send_speed(update) and self.gateway.runtime is not None:
                    send_speed_x = update.speed_x
                    send_speed_y = update.speed_y
                    # Smooth handoff from sweep if active
                    patrol_radar = getattr(self.gateway.runtime, "patrol_radar", None)
                    sweep_gen = getattr(patrol_radar, "sweep_generator", None) if patrol_radar else None
                    if sweep_gen is not None and getattr(sweep_gen, "_blend_elapsed", None) is not None:
                        send_speed_x = sweep_gen.handoff_speed_sps(send_speed_x, dt=0.015, blend_time_s=0.20, pan_scale=self.STEPS_PER_DEG_PAN)

                    # Light protocol uses an asynchronous non-blocking queue (<0.005 ms),
                    # so sending directly in-thread avoids thread pool dispatch jitter.
                    if getattr(getattr(self.gateway, "serial", None), "light_protocol_enabled", False) is True:
                        self.gateway.send_motion(
                            self.gateway.runtime,
                            send_speed_x,
                            send_speed_y,
                            "tracking",
                        )
                    else:
                        await asyncio.to_thread(
                            self.gateway.send_motion,
                            self.gateway.runtime,
                            send_speed_x,
                            send_speed_y,
                            "tracking",
                        )
                    self._update_dead_reckoning(send_speed_x, dt=0.015)
                    self._update_dead_reckoning_tilt(send_speed_y, dt=0.015)
                    self._last_locked_tilt_deg = self._dead_reckon_tilt_deg
                    self._last_speed_sent_at = time.time()
                    self._last_speed_sent = (send_speed_x, send_speed_y)

                self._update_fire_zone(control_event, update, frame_width, frame_height)

                # 4. Light keeps each profile's reference cadence. General
                # sleeps 20 ms; A2/A3/OPT_* sleep 15 ms. Subtracting work time from
                # the legacy 12 ms interval creates a different command beat
                # and silently changes the field-tested controller dynamics.
                controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
                if controller_mode in {"A2", "A3", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}:
                    await asyncio.sleep(0.015)
                elif controller_mode in {"GENERAL"}:
                    await asyncio.sleep(0.020)
                else:
                    elapsed = time.time() - t0
                    sleep_time = max(0.001, self.interval_s - elapsed)
                    await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.errors += 1
                if not yb.Y8_HATA_KADANS:
                    self.logger.emit(LogLevel.ERROR, "TRACKING_LOOP", f"Loop error: {exc}")
                    await asyncio.sleep(0.1)  # Hata durumunda yavaşla
                else:
                    # Y8: tek bir istisna döngüyü 10 Hz'e düşürmesin; log diski doldurmasın
                    _y8 = getattr(self, "_y8_hata_sayisi", 0) + 1
                    self._y8_hata_sayisi = _y8
                    if _y8 <= 3 or _y8 % 200 == 0:
                        self.logger.emit(LogLevel.ERROR, "TRACKING_LOOP", f"Loop error: {exc}", {"hata_sayisi": _y8, "tip": type(exc).__name__})
                    await asyncio.sleep(0.015)

        if self.gateway.runtime is not None and not self._stop_cleanup_owned_by_caller:
            # Unexpected task exit retains the pre-existing fail-safe boundary.
            # Normal UI mode changes are cleaned once by stop() above.
            self.gateway.serial.gateway_safe_stop()
            self.gateway.driver_enabled = False
        self.is_firing = False

    def _emit_timebase_trace(self, vision_event, update: TrackingUpdate) -> dict[str, object]:
        """Write one read-only frame-domain row for every tracker update.

        The cockpit image is an independent MJPEG transport. A browser ``img``
        element does not expose each multipart frame header to application
        code, so its exact display id is deliberately ``None`` rather than a
        fabricated join. Stage 2 can use this evidence to repair that transport
        without changing the tracking/control path here.
        """
        camera_runtime = getattr(self.vision_pipeline, "camera_runtime", None)
        selected = None
        selected_class = None
        if vision_event is not None and update.selected_target_kind == "body":
            selected = next(
                (
                    item
                    for item in vision_event.body_detections
                    if (
                        update.selected_detection_id is not None
                        and item.id == update.selected_detection_id
                    )
                    or (
                        update.selected_track_id is not None
                        and item.track_id == update.selected_track_id
                    )
                ),
                None,
            )
            selected_class = getattr(selected, "class_name", None) or "aircraft"
        elif vision_event is not None and update.selected_target_kind == "balloon":
            selected = next(
                (
                    item
                    for item in vision_event.balloon_detections
                    if update.selected_detection_id is not None
                    and item.id == update.selected_detection_id
                ),
                None,
            )
            selected_class = "balloon"

        bbox = None
        if update.selected_bbox_x is not None:
            bbox = {
                "x": float(update.selected_bbox_x),
                "y": float(update.selected_bbox_y or 0.0),
                "w": float(update.selected_bbox_w or 0.0),
                "h": float(update.selected_bbox_h or 0.0),
            }
        row: dict[str, object] = {
            "camera_frame_id": int(getattr(camera_runtime, "frame_sequence", 0) or 0) or None,
            "detector_source_frame_id": (
                int(vision_event.camera_frame_sequence)
                if vision_event is not None and vision_event.camera_frame_sequence is not None
                else None
            ),
            "tracker_measurement_frame_id": int(update.frame_id) or None,
            "UI_display_frame_id": None,
            "UI_display_sync_status": "UNJOINED_MJPEG",
            "measurement_status": update.measurement_status,
            # VisionEvent.timestamp_ms is the only production timestamp carried
            # by the frozen schema. Keep its authority explicit; do not pretend
            # that it is an atomic browser-display timestamp.
            "capture_timestamp_ms": int(vision_event.timestamp_ms) if vision_event is not None else None,
            "capture_timestamp_authority": "VISION_EVENT_TIMESTAMP",
            "bbox": bbox,
            "class": selected_class,
            "confidence": (
                float(getattr(selected, "confidence"))
                if selected is not None
                else update.selected_target_confidence
            ),
            "track_id": update.selected_track_id,
            "detection_id": update.selected_detection_id,
            "track_age_frames": update.selected_track_age_frames,
            "track_last_seen_age_ms": update.selected_track_last_seen_age_ms,
            "auto_state": update.state.value,
            "no_physical_command_generated": True,
        }
        try:  # SIM_ROW
            _g = getattr(self.gateway, "_sim_son_hiz", None)
            row["sim"] = {"mono": round(time.monotonic(), 4), "sx": getattr(update, "speed_x", None), "sy": getattr(update, "speed_y", None), "ex": getattr(update, "error_x_px", None), "gw": [round(v, 4) for v in _g] if _g else None}
        except Exception:
            pass
        now = time.monotonic()
        log_key = (update.state.value, update.selected_track_id, update.measurement_status)
        if log_key != self._last_timebase_log_key or now - self._last_timebase_log_at >= 0.5:
            self.logger.emit(
                LogLevel.INFO,
                "TRACKING_TIMEBASE",
                "Tracker timebase update",
                row,
            )
            self._last_timebase_log_at = now
            self._last_timebase_log_key = log_key
        return row

    def _safe_stop_on_target_loss(self, update: TrackingUpdate) -> bool:
        """Stop on target loss, matching Light reference behaviour by protocol.

        Light protocol (``otonom_takip.py``):
          The reference never calls stop_motion() or disables the driver during
          tracking.  When the target is lost it sends ``X 0`` / ``Y 0`` every
          20 ms to keep the Pico's 300 ms serial watchdog (TIMEOUT_MS) alive.
          The driver stays active; MOTOR,ON is never re-sent mid-session.
          Calling stop_motion() here would clear driver_enabled, forcing a new
          MOTOR,ON handshake on reacquisition which fails due to stale buffer
          responses (OK,KOMUT_ALINDI) and breaks tracking entirely.

        Legacy protocol:
          Retains the original 1.5 s timeout + full stop_motion() behaviour
          for backward compatibility with non-Light firmware.
        """
        runtime = self.gateway.runtime
        if update.state != TrackingState.SEARCHING:
            self._target_loss_safed = False
            self._target_loss_started_at = None
            self._target_loss_soft_stopped = False
            if runtime is not None and hasattr(runtime, "patrol_radar"):
                runtime.patrol_radar.tick(has_active_target=True)
            return False
        now = time.monotonic()
        if self._target_loss_started_at is None:
            self._target_loss_started_at = now
            # Hedef az önce kayboldu / vuruldu! Arka planda gerçek POZ senkronizasyonunu zorla tetikle
            if runtime is not None:
                try:
                    asyncio.create_task(self._async_sync_hardware_position(runtime, force=True))
                except Exception:
                    pass
        runtime = self.gateway.runtime
        patrol_radar = getattr(runtime, "patrol_radar", None) if runtime is not None else None
        is_sweeping = bool(
            patrol_radar is not None
            and getattr(patrol_radar.config, "patrol_enabled", False)
            and getattr(patrol_radar.config, "strategy", "CORRIDOR_HOP") == "SMOOTH_SWEEP"
        )

        loss_elapsed = now - self._target_loss_started_at
        if loss_elapsed < self.TARGET_LOSS_TILT_FREEZE_S:
            tilt_hold_target_deg = getattr(self, "_last_locked_tilt_deg", self._scan_tilt_deg())
        else:
            tilt_hold_target_deg = self._scan_tilt_deg()

        if not self._target_loss_soft_stopped:
            self._target_loss_soft_stopped = True
            self._last_speed_sent = (0, 0)
            self._reset_fire_candidate()
            # If moving fast, let smooth decel handle it instead of abrupt 0,0 shock
            if not is_sweeping and abs(self._current_pan_speed_sps) < 50.0 and self.gateway.runtime is not None and self.gateway.driver_enabled:
                slew_tilt = self._compute_smooth_return_to_tilt_speed(tilt_hold_target_deg, dt=0.015)
                self.gateway.send_motion(self.gateway.runtime, 0, slew_tilt, "tracking")
                self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)

        if patrol_radar is not None and getattr(patrol_radar.config, "patrol_enabled", False):
            # Hedef yokken radar taraması dwell/tick kontrolü
            patrol_radar.tick(has_active_target=False)
            strategy = getattr(patrol_radar.config, "strategy", "SMOOTH_SWEEP")
            slew_tilt = self._compute_smooth_return_to_tilt_speed(tilt_hold_target_deg, dt=0.015)
            if strategy != "SMOOTH_SWEEP" and self.gateway.runtime is not None:
                target_pan = float(patrol_radar.get_active_path_angle())
                slew_speed = self._compute_smooth_return_to_home_speed(target_pan, dt=0.015)
                self.gateway.send_motion(self.gateway.runtime, slew_speed, slew_tilt, "tracking")
                self._update_dead_reckoning(slew_speed, dt=0.015)
                self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
                if slew_speed == 0 and slew_tilt == 0:
                    asyncio.create_task(self._sync_position_at_rest(self.gateway.runtime))
            elif strategy == "SMOOTH_SWEEP" and self.gateway.runtime is not None:
                radar_state = str(getattr(patrol_radar, "state", ""))
                if radar_state == "SWEEPING":
                    # Radar taramaya hazır: derhal sinüsoidal süpürme hızını gönder
                    sweep_gen = getattr(patrol_radar, "sweep_generator", None)
                    current_pan = float(self._dead_reckon_pan_deg)
                    omega_dps = sweep_gen.velocity_dps(dt=0.015, current_deg=current_pan) if sweep_gen else 9.375
                    pan_scale = float(self.gateway.runtime.config.motion.pan_steps_per_degree) if hasattr(self.gateway.runtime, "config") else 133.333
                    sweep_speed = int(round(omega_dps * pan_scale))
                    self.gateway.send_motion(self.gateway.runtime, sweep_speed, slew_tilt, "sweep")
                    self._update_dead_reckoning(sweep_speed, dt=0.015)
                    self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
                elif abs(self._current_pan_speed_sps) > 50.0:
                    # Hedef kayıp onay penceresi içinde: motor yüksek hızdaysa şoksuz CNC frenleme uygula
                    max_dv = self.MAX_RETURN_ACCEL_SPS2 * 0.015
                    dv = math.copysign(min(max_dv, abs(self._current_pan_speed_sps)), -self._current_pan_speed_sps)
                    self._current_pan_speed_sps += dv
                    send_spd = int(round(self._current_pan_speed_sps))
                    self.gateway.send_motion(self.gateway.runtime, send_spd, slew_tilt, "tracking")
                    self._update_dead_reckoning(send_spd, dt=0.015)
                    self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
            return True

        # When patrol radar is not enabled, smoothly decelerate to zero (no chatter)
        slew_tilt = self._compute_smooth_return_to_tilt_speed(tilt_hold_target_deg, dt=0.015)
        if abs(self._current_pan_speed_sps) > 50.0 and self.gateway.runtime is not None:
            max_dv = self.MAX_RETURN_ACCEL_SPS2 * 0.015
            dv = math.copysign(min(max_dv, abs(self._current_pan_speed_sps)), -self._current_pan_speed_sps)
            self._current_pan_speed_sps += dv
            send_spd = int(round(self._current_pan_speed_sps))
            self.gateway.send_motion(self.gateway.runtime, send_spd, slew_tilt, "tracking")
            self._update_dead_reckoning(send_spd, dt=0.015)
            self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
            return True

        # Light protocol: keep the driver alive and keep sending 0,slew_tilt every tick
        # so the Pico watchdog never fires.  No 1.5 s timeout, no stop_motion().
        if getattr(getattr(self.gateway, "serial", None), "light_protocol_enabled", False) is True:
            # Re-send 0,slew_tilt on every tick while searching so the 300 ms Pico
            # watchdog (TIMEOUT_MS in sistem.ino loop1) never expires.
            if self.gateway.runtime is not None:
                slew_tilt = self._compute_smooth_return_to_tilt_speed(tilt_hold_target_deg, dt=0.015)
                self.gateway.send_motion(self.gateway.runtime, 0, slew_tilt, "tracking")
                self._current_pan_speed_sps = 0.0
                self._update_dead_reckoning_tilt(slew_tilt, dt=0.015)
            return True

        # Legacy protocol: full stop after TRANSIENT_TARGET_LOSS_HOLD_S.
        if self._target_loss_safed or now - self._target_loss_started_at < TRANSIENT_TARGET_LOSS_HOLD_S:
            return True
        self._target_loss_safed = True
        if self.gateway.runtime is None or not self.gateway.driver_enabled:
            return True
        result = self.gateway.stop_motion()
        payload = {
            "frame_id": update.frame_id,
            "target_lost_frames": update.target_lost_frames,
            "accepted": result.accepted,
            "reason_codes": result.reason_codes,
            "detail": result.detail,
        }
        self._events.append(("tracking.target_lost_safe_stop", payload))
        self.logger.emit(
            LogLevel.INFO if result.accepted else LogLevel.ERROR,
            "TRACKING_LOOP",
            "Target lost; CommandGateway safe-stop applied",
            payload,
        )
        return True


    def _should_send_speed(self, update: TrackingUpdate) -> bool:
        controller_mode = getattr(self.auto_tracker, "controller_mode", "legacy")
        if (
            controller_mode in {"GENERAL", "A2", "A3", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}
            or getattr(getattr(self, "gateway", None), "serial", None) is not None
            and getattr(getattr(self.gateway, "serial", None), "light_protocol_enabled", False) is True
        ):
            # In Light protocol / A2/GENERAL modes, every control tick emits motor commands
            # without software rate limiter drops (matching Light's continuous stream).
            return True

        speed = (update.speed_x, update.speed_y)
        elapsed = time.time() - self._last_speed_sent_at
        rate_ceiling = 60.0
        command_rate_hz = max(1.0, min(rate_ceiling, float(getattr(self.auto_tracker, "command_rate_hz", 30.0) or 30.0)))
        if elapsed < 1.0 / command_rate_hz:
            return False
        if speed == self._last_speed_sent and elapsed < 0.25:
            return False
        return True

    def _frame_size(self) -> tuple[int, int]:
        camera_runtime = getattr(self.vision_pipeline, "camera_runtime", None)
        if camera_runtime is None:
            return self.frame_width, self.frame_height
        # This method runs on every control tick.  Building a full Pydantic
        # camera status and resolving the PnP identity here added measurable
        # overhead without changing the control dimensions.  The capture
        # worker publishes a replacement frame atomically, so reading its
        # shape is safe; fall back to the requested profile before first frame.
        frame = getattr(camera_runtime, "last_frame", None)
        profile = getattr(camera_runtime, "profile", None)
        width = int(frame.shape[1]) if frame is not None else int(getattr(profile, "width", self.frame_width) or self.frame_width)
        height = int(frame.shape[0]) if frame is not None else int(getattr(profile, "height", self.frame_height) or self.frame_height)
        self.frame_width = width
        self.frame_height = height
        return width, height

    @staticmethod
    def _controller_mode_for_stage(stage: str | None, target_policy: str | None) -> str:
        """Map explicit competition stages and the standard cockpit mode.

        Stage 3 retains its checked-in A3 profile. Balloon tracking uses
        the Light Aşama 2 (A2) controller matching asama2_otonom.py.
        Aircraft keeps the normalized legacy path until a body-specific Light
        profile is defined.
        """
        if stage == "stage3":
            return "A3"
        policy = str(target_policy or "BALLOON").upper()
        if policy == "AIRCRAFT":
            return "legacy"
        if stage == "stage2" and policy == "BALLOON":
            return "OPT_SINE_TRACK"
        return "A2"

    def _update_fire_zone(self, vision_event, update: TrackingUpdate, frame_width: int, frame_height: int) -> None:
        if update.target_center_x is None or update.target_center_y is None:
            self._reset_fire_candidate()
            return
        target_bbox = self._target_bbox_for_update(vision_event, update)
        if target_bbox is None:
            self._reset_fire_candidate()
            return

        now_mono = time.monotonic()
        runtime = getattr(self.gateway, "runtime", None)
        active_stage = getattr(runtime.mission.state, "active_stage", "") if runtime and hasattr(runtime, "mission") else ""
        target_policy = getattr(runtime.operation.state(), "target_policy", None) if runtime and hasattr(runtime, "operation") else None
        target_policy_val = target_policy.value if hasattr(target_policy, "value") else str(target_policy or "")
        is_stage2 = active_stage in {"stage1", "stage2"} or target_policy_val in {"BALLOON", "STAGE1_INDEPENDENT"}

        if not is_stage2:
            # CRITICAL SAFETY (Stage 3): Block fire candidate if target is friendly or fire is unauthorized
            if vision_event is not None and getattr(vision_event, "target_verdicts", None):
                for v in vision_event.target_verdicts:
                    is_match = False
                    if getattr(v, "detection_id", None) is not None:
                        preferred_id = getattr(self.auto_tracker, "preferred_target_detection_id", None)
                        if preferred_id is not None and v.detection_id == preferred_id:
                            is_match = True
                    if not is_match and v.bbox:
                        vx = v.bbox.get("x", 0) + v.bbox.get("w", 0) / 2
                        vy = v.bbox.get("y", 0) + v.bbox.get("h", 0) / 2
                        if math.hypot(vx - update.target_center_x, vy - update.target_center_y) < max(25.0, target_bbox.w * 0.75):
                            is_match = True
                    if is_match:
                        team = str(getattr(v, "target_team", "")).lower()
                        v_state = str(getattr(v, "verdict_state", "")).upper()
                        if team in {"friend", "dost"} or v_state == "FRIEND_LOCKED" or not getattr(v, "fire_authorized", True):
                            self._reset_fire_candidate()
                            return

            now_mono = time.monotonic()
            if vision_event is not None and getattr(vision_event, "body_detections", None):
                for b in vision_event.body_detections:
                    if str(getattr(b, "target_team", "")).lower() in {"friend", "dost"}:
                        self._recent_friendly_shields.append((now_mono, b.bbox))

            # Prune shields older than 0.50s (lightweight hysteresis prevents single-frame detector flicker bypass)
            self._recent_friendly_shields = [
                (t, fb_box) for (t, fb_box) in self._recent_friendly_shields
                if (now_mono - t) <= 0.50
            ]
            if any(stage3_roi.attachment_roi_contains(fb_box, update.target_center_x, update.target_center_y) for _, fb_box in self._recent_friendly_shields):
                self._reset_fire_candidate()
                return
        else:
            self._recent_friendly_shields = []

        fire_radius = min(target_bbox.w, target_bbox.h) * FIRE_ZONE_RADIUS_RATIO
        crosshair_x = update.frame_center_x
        crosshair_y = update.frame_center_y
        aim_target_x = update.lock_zone_center_x if update.lock_zone_center_x is not None else (
            update.predicted_target_center_x if update.predicted_target_center_x is not None else update.target_center_x
        )
        aim_target_y = update.lock_zone_center_y if update.lock_zone_center_y is not None else (
            update.predicted_target_center_y if update.predicted_target_center_y is not None else update.target_center_y
        )
        dist_lead = math.hypot(aim_target_x - crosshair_x, aim_target_y - crosshair_y)
        dist_balloon = math.hypot(update.target_center_x - crosshair_x, update.target_center_y - crosshair_y)
        is_light = (
            getattr(getattr(self.gateway, "serial", None), "light_protocol_enabled", False) is True
            or getattr(self.auto_tracker, "controller_mode", "legacy") in {
                "GENERAL", "A2", "A3", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"
            }
        )
        min_inner_radius = 18.0 if is_light else 3.0
        max_inner_radius = 26.0 if is_light else 12.0
        # In light mode (A2/A3), allow up to 0.65 ratio capped at 26.0px ceiling:
        effective_inner_ratio = 0.65 if is_light else FIRE_INNER_BBOX_RATIO
        inner_radius = min(max_inner_radius, max(min_inner_radius, min(target_bbox.w, target_bbox.h) * effective_inner_ratio / 2.0))

        # Directional Fire Gate (Koşu Yoluna Atış Kapısı):
        # Prevent firing when crosshair is trailing behind the moving target.
        # Direct balloon hits (dist_balloon <= inner_radius) are always allowed.
        # When aiming lead, allow generous -16.0px margin to avoid dropping lock while pursuing.
        vx = float(getattr(update, "target_velocity_x_px_s", 0.0) or 0.0)
        vy = float(getattr(update, "target_velocity_y_px_s", 0.0) or 0.0)
        target_speed = math.hypot(vx, vy)
        directional_ok = True
        directional_proj = 0.0
        if target_speed >= 5.0:
            if dist_balloon <= inner_radius:
                directional_ok = True
            else:
                dx = crosshair_x - update.target_center_x
                dy = crosshair_y - update.target_center_y
                directional_proj = (dx * vx + dy * vy) / target_speed
                directional_ok = (directional_proj >= -16.0)

        inside_inner_lock = ((dist_lead <= inner_radius) or (dist_balloon <= inner_radius)) and directional_ok
        if yb.Y5_KAPI_21EYLUL:
            # Y5: 21 Eylül'de zikzakta vuran kapı — r = max(10 px, 0.30*w/2), yön kapısı yok
            inner_radius = max(10.0 if is_light else 3.0, min(target_bbox.w, target_bbox.h) * FIRE_INNER_BBOX_RATIO / 2.0)
            directional_ok = True
            directional_proj = 0.0
            inside_inner_lock = (dist_lead <= inner_radius) or (dist_balloon <= inner_radius)

        # Exclusion Zone Safety Interlock (Atışa Yasak / Güvenlik Alanı):
        # Authoritative angular limits + screen polygon + 0.25s velocity lookahead + 0.50s debounce
        runtime = self.gateway.runtime
        decision_cfg = getattr(runtime.config, "decision", None) if runtime and hasattr(runtime, "config") else None
        zone_mgr = getattr(self, "exclusion_zone_manager", None)
        if zone_mgr is not None:
            if decision_cfg and hasattr(decision_cfg, "fire_forbidden_zones"):
                zone_mgr.update_from_angular_zones(decision_cfg.fire_forbidden_zones)

            motion_svc = getattr(runtime, "motion", None) if runtime else None
            ms = motion_svc.status() if motion_svc and hasattr(motion_svc, "status") else None
            curr_tilt = getattr(ms, "physical_tilt_deg", 30.0) if ms else 30.0

            trigger_ok = zone_mgr.is_trigger_allowed(
                pan_deg=getattr(self, "_dead_reckon_pan_deg", 135.0),
                tilt_deg=curr_tilt,
                target_x_px=update.target_center_x,
                target_y_px=update.target_center_y,
                vx_px_s=vx,
                vy_px_s=vy,
                now=now_mono,
            )
        else:
            trigger_ok = True
        if not trigger_ok:
            self._fire_block_reason = "EXCLUSION_ZONE_BLOCKED"
            self._reset_fire_candidate()
            return

        distance = min(dist_lead, dist_balloon)
        # An empty magazine is a command-local block. Keep tracking stable and
        # avoid repeated FIRE calls while empty. Once the operator reloads,
        # require a brand-new stability window before evaluating FIRE again.
        if (
            self._fire_block_reason == "MAGAZINE_EMPTY"
            and self.serial.magazine_remaining > 0
        ):
            self._reset_fire_candidate()
        now = time.time()
        if inside_inner_lock:
            self._lock_lost_frames = 0
            self.fire_target_frames += 1
            if self._lock_start_time is None:
                self._lock_start_time = now
        else:
            self._lock_lost_frames = getattr(self, "_lock_lost_frames", 0) + 1
            if yb.Y5_KAPI_21EYLUL or self._lock_lost_frames >= 3 or self._lock_start_time is None:
                self._reset_fire_candidate()
                return
            # Within 3-frame grace period (jitter/recoil debounce), hold lock active
            inside_inner_lock = True

        lock_duration = now - self._lock_start_time
        # For follow-up shots on an already engaged target within 1.5s,
        # eliminate lock confirmation delay (instant rapid fire "tak-tak"):
        is_follow_up_shot = (now - self._last_fire_time) <= 1.5 and (self._last_shot_accepted or not yb.Y3_ATIS_ZAMANLAMA)
        required_duration = 0.0 if is_follow_up_shot else (self._lock_confirmation_s if is_light else 0.0)
        cooldown_elapsed = (now - self._last_fire_time) >= self._fire_cooldown_s

        if (
            self.fire_target_frames >= FIRE_REQUIRED_FRAMES
            and lock_duration >= required_duration
            and cooldown_elapsed
            and not self._fire_candidate_active
        ):
            self._fire_candidate_active = True
            self._last_fire_time = now
            candidate = self._fire_event_payload(vision_event, update, distance, fire_radius) | {
                "inner_lock_width_ratio": FIRE_INNER_BBOX_RATIO,
                "inner_lock_height_ratio": FIRE_INNER_BBOX_RATIO,
                "inner_lock_radius_px": round(inner_radius, 2),
                "aim_inside_inner_lock": inside_inner_lock,
                "directional_ok": directional_ok,
                "directional_proj_px": round(directional_proj, 2),
                "lock_duration_s": round(lock_duration, 3),
            }
            self.logger.emit(
                LogLevel.WARN,
                "TRACKING_LOOP",
                "Fire candidate observed; forwarding to CommandGateway for preflight evaluation",
                candidate,
            )
            # Keep the established event shape stable; the richer inner-lock
            # fields are carried to Gateway/evidence via ``candidate``.
            self._events.append(("tracking.fire_candidate", self._fire_event_payload(vision_event, update, distance, fire_radius)))
            if self._physical_auto_fire_allowed():
                def _dispatch_fire(rt, cand):
                    try:
                        result = self.gateway.fire_from_tracking(rt, cand)
                        self.auto_tracker.record_fire_result(result)
                        if yb.Y3_ATIS_ZAMANLAMA:
                            # Y3: reddedilen aday tam bekleme süresini yakmasın; 0.10 s sonra yeniden denensin
                            self._last_shot_accepted = bool(result.accepted)
                            if not result.accepted:
                                self._last_fire_time = time.time() - self._fire_cooldown_s + yb.Y3_RED_SONRASI_TEKRAR_S
                        self._fire_block_reason = result.reason_codes[0] if not result.accepted and result.reason_codes else None
                        self._events.append(("tracking.fire_result", result.model_dump(mode="json")))
                    except Exception as exc:
                        self.logger.emit(LogLevel.ERROR, "TRACKING_LOOP", f"Fire dispatch error: {exc}")
                    finally:
                        self._fire_candidate_active = False

                threading.Thread(target=_dispatch_fire, args=(self.gateway.runtime, candidate), daemon=True).start()
            else:
                self._fire_candidate_active = False

    def _physical_auto_fire_allowed(self) -> bool:
        """Keep LIVE_TEST tracking physical but its FIRE action operator-driven.

        Competition stages A2/A3 own autonomous engagement. LIVE_TEST and
        VIDEO_DEMO still retain fully working physical FIRE through the visible
        operator/Gateway command, but a centered target cannot surprise-trigger
        or interrupt a motion-only tracking acceptance run.
        """
        runtime = self.gateway.runtime
        if runtime is None or self.gateway.profile.value == "DRY_RUN":
            return False
        operation_service = getattr(runtime, "operation", None)
        operation = operation_service.state() if operation_service is not None else None
        if operation is not None:
            return operation.control_mode.value == "AUTONOMOUS" and operation.fire_permission.value == "ENABLED"
        if self.gateway.profile.value != "COMPETITION":
            return False
        return runtime.mission.state.active_stage in {"stage2", "stage3"}

    def _vision_event_is_fresh(self, vision_event) -> bool:
        if vision_event is None:
            return False
        if getattr(getattr(self.gateway, "serial", None), "light_protocol_enabled", False) is True:
            return True
        event_timestamp_s = float(vision_event.timestamp_ms) / 1000.0
        age_s = time.time() - event_timestamp_s
        return -1.0 <= age_s <= MAX_VISION_EVENT_AGE_S

    def _handle_stale_vision(self, vision_event) -> None:
        now = time.time()
        if now - self._last_stale_vision_log_at >= 1.0:
            age_ms = None
            if vision_event is not None:
                age_ms = round(max(0.0, (now - float(vision_event.timestamp_ms) / 1000.0) * 1000.0), 1)
            self.logger.emit(
                LogLevel.WARN,
                "TRACKING_LOOP",
                "Stale or missing vision event; tracker is commanded to safe search",
                {"event_age_ms": age_ms, "max_age_ms": int(MAX_VISION_EVENT_AGE_S * 1000)},
            )
            self._last_stale_vision_log_at = now
        self._reset_fire_candidate()

    def _reset_fire_candidate(self) -> None:
        self.fire_target_frames = 0
        self._lock_start_time = None
        self._fire_candidate_active = False
        self._fire_block_reason = None
        self._lock_lost_frames = 0

    def _target_bbox_for_update(self, vision_event, update: TrackingUpdate):
        if vision_event is None:
            return None
        runtime = self.gateway.runtime
        policy = runtime.operation.state().target_policy.value if runtime is not None and hasattr(runtime, "operation") else "BALLOON"
        if policy == "AIRCRAFT":
            if not vision_event.body_detections:
                return None
            body = min(
                vision_event.body_detections,
                key=lambda det: (det.bbox.x + det.bbox.w / 2 - (update.target_center_x or 0)) ** 2
                + (det.bbox.y + det.bbox.h / 2 - (update.target_center_y or 0)) ** 2,
            )
            return body.bbox
        if not vision_event.balloon_detections:
            return None
        return min(
            vision_event.balloon_detections,
            key=lambda det: (det.center_x - (update.target_center_x or det.center_x)) ** 2
            + (det.center_y - (update.target_center_y or det.center_y)) ** 2,
        ).bbox

    def _fire_event_payload(self, vision_event, update: TrackingUpdate, distance: float, fire_radius: float) -> dict:
        aim_target_x = update.lock_zone_center_x if update.lock_zone_center_x is not None else (
            update.predicted_target_center_x if update.predicted_target_center_x is not None else update.target_center_x
        )
        aim_target_y = update.lock_zone_center_y if update.lock_zone_center_y is not None else (
            update.predicted_target_center_y if update.predicted_target_center_y is not None else update.target_center_y
        )
        payload = {
            "frame_id": update.frame_id,
            "distance_px": round(distance, 2),
            "fire_radius_px": round(fire_radius, 2),
            "target_center_x": update.target_center_x,
            "target_center_y": update.target_center_y,
            "aim_target_x": round(aim_target_x, 2) if aim_target_x is not None else None,
            "aim_target_y": round(aim_target_y, 2) if aim_target_y is not None else None,
            "momentum_shift_x": update.momentum_shift_x_px,
            "required_stable_frames": FIRE_REQUIRED_FRAMES,
            "physical_fire_generated": False,
        }
        target = self._target_bbox_for_update(vision_event, update)
        if target is None or vision_event is None:
            return payload
        runtime = self.gateway.runtime
        policy = runtime.operation.state().target_policy.value if runtime is not None and hasattr(runtime, "operation") else "BALLOON"
        if policy == "AIRCRAFT":
            body = min(
                vision_event.body_detections,
                key=lambda det: (det.bbox.x + det.bbox.w / 2 - (update.target_center_x or 0)) ** 2
                + (det.bbox.y + det.bbox.h / 2 - (update.target_center_y or 0)) ** 2,
            ) if vision_event.body_detections else None
            if body is None:
                return payload
            payload["body_detection_id"] = body.id
            payload["body_class"] = body.class_name
            payload["body_team"] = body.target_team
            payload["track_fresh"] = True
            payload["target_policy"] = policy
            return payload
        balloon = min(
            vision_event.balloon_detections,
            key=lambda det: (det.center_x - (update.target_center_x or det.center_x)) ** 2
            + (det.center_y - (update.target_center_y or det.center_y)) ** 2,
        )
        tracker_status = self.auto_tracker.status() if hasattr(self.auto_tracker, "status") else None
        tracks = getattr(getattr(tracker_status, "multi_target_tracker", None), "tracks", []) if tracker_status else []
        track = None
        if isinstance(tracks, (list, tuple)):
            track = next((item for item in tracks if item.detection_id == balloon.id and item.fresh), None)
        payload["balloon_detection_id"] = balloon.id
        payload["balloon_track_id"] = track.track_id if track is not None else balloon.id
        payload["target_policy"] = policy
        payload["track_fresh"] = track.fresh if track is not None else True
        if self.gateway.runtime is not None and track is not None:
            association = next(
                (item for item in self.gateway.runtime.association.status().associations if item.balloon_track_id == track.track_id),
                None,
            )
            if association is not None:
                payload["body_detection_id"] = association.body_detection_id
                payload["body_track_id"] = association.body_track_id
                payload["association_state"] = association.state
                body = next((item for item in vision_event.body_detections if item.id == association.body_detection_id), None)
                if body is not None:
                    payload["body_class"] = body.class_name
                    payload["body_team"] = body.target_team
                registry = getattr(self.gateway.runtime, "target_registry", None)
                logical_target = registry.target_for_balloon_track(track.track_id) if registry is not None else None
                if logical_target is not None:
                    payload["logical_target_id"] = logical_target.target_id
                    payload["logical_target_name"] = logical_target.display_name
                    payload["logical_target_state"] = logical_target.state.value
                    payload["balloon_target_id"] = logical_target.balloon_target_id
        return payload
