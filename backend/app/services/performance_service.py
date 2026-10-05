import os
import subprocess
import time
from pathlib import Path
from typing import Any

from app.schemas.performance import PerformanceMetric, PerformanceStatus


class PerformanceService:
    def __init__(self) -> None:
        self._last_proc_cpu: tuple[float, float] | None = None
        self._last_total_cpu: tuple[int, int] | None = None
        self._gpu_cache_until = 0.0
        self._gpu_cache: tuple[float | None, float | None] = (None, None)

    def status(self, runtime: Any, vision_event: Any | None = None) -> PerformanceStatus:
        now = time.time()
        # The caller may already own the current vision event (the WebSocket
        # publisher does).  Re-running YOLO here doubled GPU work and blocked
        # the async event loop whenever two models were active.
        vision_event = vision_event or runtime.vision_pipeline.latest()
        vision_status = runtime.vision_pipeline.status()
        serial_status = runtime.serial.status()
        hardware_status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        tracking_update = runtime.tracking_loop.last_update

        camera_frame_age_ms = None
        if vision_event.timestamp_ms:
            camera_frame_age_ms = max(0, int(now * 1000 - vision_event.timestamp_ms))

        tracking_loop_ms = None
        if tracking_update is not None:
            tracking_loop_ms = round(float(tracking_update.dt) * 1000, 2)

        vision_total_ms = float(vision_event.total_latency_ms or vision_status.latest_latency_ms or 0.0)
        total_pipeline_ms = vision_total_ms + (serial_status.last_command_rtt_ms or 0)

        metrics = {
            "camera_frame_age": self._metric(camera_frame_age_ms, "ms", 80, 180, "Kamera frame yaşı"),
            "yolo_inference": self._metric(vision_event.inference_ms, "ms", 70, 150, "YOLO inference"),
            "tracking_loop": self._metric(tracking_loop_ms, "ms", 20, 60, "Tracking loop"),
            "serial_ack": self._metric(serial_status.last_command_rtt_ms, "ms", 30, 100, "Serial ACK RTT"),
            "pico_heartbeat": self._metric(hardware_status.telemetry.heartbeat_age_ms, "ms", 300, 1000, "Pico heartbeat"),
            "total_pipeline": self._metric(total_pipeline_ms, "ms", 150, 350, "Toplam gecikme"),
            "tx_queue": self._metric(serial_status.command_queue_depth, "cmd", 1, 4, "TX queue"),
        }

        warnings = [key for key, metric in metrics.items() if metric.tone == "bad"]
        primary_bottleneck, bottleneck_summary, recommended_actions = self._diagnose(metrics, vision_status.running, serial_status.last_command_ack_state)
        return PerformanceStatus(
            cpu_percent=self._system_cpu_percent(),
            process_cpu_percent=self._process_cpu_percent(),
            memory_percent=self._memory_percent(),
            process_rss_mb=self._process_rss_mb(),
            load_avg_1m=self._load_avg(),
            gpu_util_percent=self._gpu_metrics()[0],
            gpu_memory_percent=self._gpu_metrics()[1],
            camera_frame_age_ms=camera_frame_age_ms,
            camera_fps=vision_status.camera_fps or vision_status.fps,
            dropped_frames=runtime.camera_runtime.status().dropped_frames,
            preprocess_ms=vision_event.preprocess_ms,
            inference_ms=vision_event.inference_ms,
            postprocess_ms=vision_event.postprocess_ms,
            vision_total_ms=vision_total_ms,
            tracking_loop_ms=tracking_loop_ms,
            serial_ack_rtt_ms=serial_status.last_command_rtt_ms,
            serial_pending_ack_count=serial_status.pending_ack_count,
            serial_queue_depth=serial_status.command_queue_depth,
            pico_heartbeat_age_ms=hardware_status.telemetry.heartbeat_age_ms,
            total_pipeline_ms=round(total_pipeline_ms, 2),
            metrics=metrics,
            warnings=warnings,
            primary_bottleneck=primary_bottleneck,
            bottleneck_summary=bottleneck_summary,
            recommended_actions=recommended_actions,
            updated_at=now,
        )

    @staticmethod
    def _metric(value: float | int | None, unit: str, green: float, yellow: float, label: str) -> PerformanceMetric:
        if value is None:
            tone = "neutral"
        elif value <= green:
            tone = "good"
        elif value <= yellow:
            tone = "warn"
        else:
            tone = "bad"
        return PerformanceMetric(value=value, unit=unit, green_max=green, yellow_max=yellow, tone=tone, label=label)

    @staticmethod
    def _diagnose(metrics: dict[str, PerformanceMetric], vision_running: bool, ack_state: str | None) -> tuple[str, str, list[str]]:
        priority = [
            ("camera_frame_age", "Kamera frame akışı gecikiyor veya donuyor.", ["Kamera portunu doğrula.", "FPS/çözünürlüğü düşür.", "USB kablo/port değişimini kontrol et."]),
            ("yolo_inference", "YOLO inference bütçeyi aşıyor.", ["imgsz veya frame_skip ayarını düşür.", "GPU seçimini kontrol et.", "Model/preset benchmark çalıştır."]),
            ("tracking_loop", "Tracking döngüsü yavaşlıyor.", ["Komut Hz ve PID değerlerini düşür.", "Overlay/hedef seçimini sadeleştir.", "Tracking loop loglarını kontrol et."]),
            ("serial_ack", "Pico ACK gecikiyor.", ["Pico portunu yeniden bağla.", "Pending ACK ve serial loglarını kontrol et.", "Eski komut birikimini temizlemek için stop/disarm gönder."]),
            ("pico_heartbeat", "Pico heartbeat sağlıksız.", ["Pico USB bağlantısını kontrol et.", "Doğru portu seç.", "Firmware ve baudrate uyumunu doğrula."]),
            ("tx_queue", "Serial komut kuyruğu birikiyor.", ["Tracking komut Hz değerini azalt.", "Aynı anda manuel ve otomatik komut göndermeyi durdur.", "Pico ACK dönene kadar yeni atış/motor komutu verme."]),
            ("total_pipeline", "Toplam kamera-karar-Pico gecikmesi yüksek.", ["Darboğaz kartlarında kırmızı metriği önce düzelt.", "YOLO ve serial gecikmesini ayrı ayrı kontrol et."]),
        ]
        if not vision_running:
            return "vision_stopped", "Vision pipeline çalışmıyor.", ["Vision başlat.", "Kamera profilini ve model adapterini doğrula."]
        if ack_state == "timeout":
            return "serial_ack", "Son Pico komutu timeout oldu.", ["Pico bağlantısını ve port seçimini kontrol et.", "Komut kuyruğunu boşaltmak için sistemi güvenli duruma al."]
        for key, summary, actions in priority:
            metric = metrics.get(key)
            if metric and metric.tone == "bad":
                return key, summary, actions
        for key, summary, actions in priority:
            metric = metrics.get(key)
            if metric and metric.tone == "warn":
                return key, summary.replace(".", " sınırda."), actions[:2]
        return "none", "Akış normal.", ["Yarışma öncesi kısa self-test ve hedef seçimi doğrulaması yap."]

    def _system_cpu_percent(self) -> float | None:
        stat = self._read_cpu_stat()
        if stat is None:
            return None
        idle, total = stat
        previous = self._last_total_cpu
        self._last_total_cpu = stat
        if previous is None:
            return None
        idle_delta = idle - previous[0]
        total_delta = total - previous[1]
        if total_delta <= 0:
            return None
        return round(100.0 * (1.0 - idle_delta / total_delta), 1)

    @staticmethod
    def _read_cpu_stat() -> tuple[int, int] | None:
        try:
            parts = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
            values = [int(item) for item in parts]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return idle, sum(values)
        except Exception:
            return None

    def _process_cpu_percent(self) -> float | None:
        try:
            now = time.monotonic()
            clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8").split()
            proc_seconds = (int(stat[13]) + int(stat[14])) / clk_tck
            previous = self._last_proc_cpu
            self._last_proc_cpu = (now, proc_seconds)
            if previous is None:
                return None
            elapsed = now - previous[0]
            if elapsed <= 0:
                return None
            return round(100.0 * (proc_seconds - previous[1]) / elapsed, 1)
        except Exception:
            return None

    @staticmethod
    def _memory_percent() -> float | None:
        try:
            data: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, raw = line.split(":", 1)
                data[key] = int(raw.strip().split()[0])
            total = data["MemTotal"]
            available = data["MemAvailable"]
            return round(100.0 * (1.0 - available / total), 1)
        except Exception:
            return None

    @staticmethod
    def _process_rss_mb() -> float | None:
        try:
            pages = int(Path(f"/proc/{os.getpid()}/statm").read_text(encoding="utf-8").split()[1])
            return round(pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 1)
        except Exception:
            return None

    @staticmethod
    def _load_avg() -> float | None:
        try:
            return round(os.getloadavg()[0], 2)
        except Exception:
            return None

    def _gpu_metrics(self) -> tuple[float | None, float | None]:
        now = time.monotonic()
        if now < self._gpu_cache_until:
            return self._gpu_cache
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.4,
            )
            line = result.stdout.strip().splitlines()[0]
            util, used, total = [float(part.strip()) for part in line.split(",")[:3]]
            memory = round(100.0 * used / total, 1) if total else None
            self._gpu_cache = (round(util, 1), memory)
        except Exception:
            self._gpu_cache = (None, None)
        self._gpu_cache_until = now + 2.0
        return self._gpu_cache
