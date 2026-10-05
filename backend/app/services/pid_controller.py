"""
PID Controller — Eski sistemden (eski_sistem_arayüz/python/pid_controller.py) uyarlandı.

Anti-windup PID controller. Error: piksel, output: motor hız (steps/s).
X ve Y eksenleri için bağımsız PID parametreleri.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PIDController:
    """
    Tek eksen PID kontrolcüsü.

    Eski sistemdeki ``PIDController`` sınıfının birebir karşılığı.
    Anti-windup: integral clamp.
    """

    kp: float
    ki: float
    kd: float
    min_out: float
    max_out: float
    integral_max: float = 100.0

    _prev_error: float = field(default=0.0, init=False, repr=False)
    _integral: float = field(default=0.0, init=False, repr=False)

    def reset(self) -> None:
        """Integral ve prev_error sıfırla (yeni hedefte)."""
        self._integral = 0.0
        self._prev_error = 0.0

    def compute(self, error: float, dt: float) -> float:
        """
        PID hesapla: P + I + D.

        Parameters
        ----------
        error : piksel cinsinden hedef sapması
        dt    : son frame'den bu yana geçen süre (saniye)

        Returns
        -------
        float : clamp'lenmiş motor hız komutu
        """
        # Proportional
        p_term = self.kp * error

        # Integral (dt ile)
        self._integral += error * dt
        self._integral = max(-self.integral_max, min(self.integral_max, self._integral))
        i_term = self.ki * self._integral

        # Derivative
        derivative = (error - self._prev_error) / dt if dt > 0 else 0.0
        d_term = self.kd * derivative
        self._prev_error = error

        # Toplam output clamp
        output = p_term + i_term + d_term
        return max(self.min_out, min(self.max_out, output))


@dataclass
class DualPID:
    """
    X ve Y eksenleri için ayrı PID kontrolcüleri.

    Eski sistemdeki ``DualPID`` sınıfının birebir karşılığı.
    """

    pid_x: PIDController
    pid_y: PIDController

    def reset(self) -> None:
        self.pid_x.reset()
        self.pid_y.reset()

    def compute(self, error_x: float, error_y: float, dt: float) -> tuple[float, float]:
        speed_x = self.pid_x.compute(error_x, dt)
        speed_y = self.pid_y.compute(error_y, dt)
        return speed_x, speed_y


def create_dual_pid(
    kp_x: float = 8.0,
    ki_x: float = 0.01,
    kd_x: float = 0.50,
    kp_y: float = 4.0,
    ki_y: float = 0.002,
    kd_y: float = 0.30,
    output_min: float = -1000.0,
    output_max: float = 1000.0,
    integral_max: float = 25000.0,
) -> DualPID:
    """Varsayılan PID parametreleri ile DualPID oluştur (eski sistem değerleri)."""
    return DualPID(
        pid_x=PIDController(kp=kp_x, ki=ki_x, kd=kd_x, min_out=output_min, max_out=output_max, integral_max=integral_max),
        pid_y=PIDController(kp=kp_y, ki=ki_y, kd=kd_y, min_out=output_min, max_out=output_max, integral_max=integral_max),
    )
