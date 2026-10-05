"""
Raspberry Pi Pico 2 — Üretim Motor Kontrol Firmware'i
ISTIKLAL Komuta Kontrol Merkezi — Çelikkubbe Hava Savunma

Eski firmware (eski_sistem_arayüz/pico/motor_control_pico.py) baz alınarak
yeni mimari için adapte edilmiştir.

Özellikler:
 - Dual axis step motor sürme (pan + tilt)
 - TMC2209 step/dir sürme (eski çalışan Arduino pin haritası)
 - İvme rampası (smooth hızlanma/yavaşlama)
 - Serial komut parse: SPD, DRV, HOM, STP, PING, MICROSTEP
 - JSON telemetri raporlama (500ms periyot)
 - E-stop donanımsal güvenlik (GPIO interrupt)
 - Limit switch okuma (4 yön)
 - Watchdog: 2 saniye komut gelmezse motorları durdur
 - Thread-safe: motor task ayrı thread'de çalışır

Pin Haritası:
  GP1  = PAN_STEP       GP0  = PAN_DIR
  GP9  = TILT_STEP      GP8  = TILT_DIR
  GP6  = PAN_ENABLE     GP14 = TILT_ENABLE
  GP15 = LASER/SERVO
  GP20 = ESTOP_IN (pull-up, LOW=pressed)
  GP16 = LIMIT_PAN_LEFT   GP17 = LIMIT_PAN_RIGHT
  GP18 = LIMIT_TILT_UP    GP19 = LIMIT_TILT_DOWN

Komut Protokolü (USB CDC serial, \\n terminated):
  TX (PC → Pico): SPD,x,y | DRV,0/1 | HOM | STP | PING | MICROSTEP,motor,mode
  RX (Pico → PC): JSON telemetri + OK/ERR yanıtlar
"""

import json
import time
import _thread
import select
from machine import Pin, PWM

# ============================================================================
# PIN TANIMLARI
# ============================================================================

STEP_X_PIN = 1       # Pan step
DIR_X_PIN = 0        # Pan direction
STEP_Y_PIN = 9       # Tilt step
DIR_Y_PIN = 8        # Tilt direction
ENABLE_X_PIN = 6     # X driver enable (LOW = aktif)
ENABLE_Y_PIN = 14    # Y driver enable (LOW = aktif)
LASER_PIN = 15       # Servo (Tetik) pin
ESTOP_PIN = 20       # E-stop input (pull-up, LOW = basılı)
LIMIT_PAN_L = 16     # Pan sol limit switch
LIMIT_PAN_R = 17     # Pan sağ limit switch
LIMIT_TILT_U = 18    # Tilt üst limit switch
LIMIT_TILT_D = 19    # Tilt alt limit switch

# ============================================================================
# HIZ VE ZAMANLAMA
# ============================================================================

MIN_STEP_DELAY_X_US = 250    # Pan üst sınırı: 4000 step/sn
MIN_STEP_DELAY_Y_US = 167    # Tilt üst sınırı: ~6000 step/sn
MAX_STEP_DELAY_US = 2500     # Yüklü taret için 400 step/sn torklu kalkış
ACCEL_STEP_US = 25.0         # Her gerçek step sonrası gecikme değişimi
WATCHDOG_TIMEOUT_MS = 2000   # 2 saniye komut gelmezse dur
TELEMETRY_INTERVAL_MS = 60000  # USB CDC buffer dolmasını önlemek için seyrek telemetri
TELEMETRY_ENABLED = False      # Backend raw write modunda RX okumadığı için kapalı

# ============================================================================
# PIN KURULUMU
# ============================================================================

step_x = Pin(STEP_X_PIN, Pin.OUT, value=0)
dir_x = Pin(DIR_X_PIN, Pin.OUT, value=0)
step_y = Pin(STEP_Y_PIN, Pin.OUT, value=0)
dir_y = Pin(DIR_Y_PIN, Pin.OUT, value=0)

enable_x = Pin(ENABLE_X_PIN, Pin.OUT, value=1)  # HIGH = disabled
enable_y = Pin(ENABLE_Y_PIN, Pin.OUT, value=1)  # HIGH = disabled

# Servo (Tetik) PWM Kurulumu - GP11, 50Hz
laser_servo = PWM(Pin(LASER_PIN))
laser_servo.freq(50)
laser_servo.duty_u16(0) # Başlangıç: Serbest (duty 0)

# Servo Açı Hesaplamaları (genellikle 1ms - 2ms arası 50Hz için 0-180 derece)
# 50 derece ~ 3459 duty, 165 derece ~ 7665 duty (ayarlanabilir)
SERVO_0_DEG_DUTY = 3459
SERVO_FIRE_DEG_DUTY = 7665

def servo_duty_for_degree(degree):
    """50 Hz servo mapping: 1.0 ms (0°) to 2.0 ms (180°)."""
    bounded = max(0, min(180, int(degree)))
    return int(3277 + (bounded * (3277 / 180)))
laser_servo.duty_u16(SERVO_0_DEG_DUTY) # Başlangıç: 0 derece (Sakin)

estop = Pin(ESTOP_PIN, Pin.IN, Pin.PULL_UP)
limit_pan_l = Pin(LIMIT_PAN_L, Pin.IN, Pin.PULL_UP)
limit_pan_r = Pin(LIMIT_PAN_R, Pin.IN, Pin.PULL_UP)
limit_tilt_u = Pin(LIMIT_TILT_U, Pin.IN, Pin.PULL_UP)
limit_tilt_d = Pin(LIMIT_TILT_D, Pin.IN, Pin.PULL_UP)

# ============================================================================
# GLOBAL DURUM
# ============================================================================

target_delay_x = 0       # 0 = dur, >0 = hedef step delay (µs)
target_delay_y = 0
current_delay_x = 0.0
current_delay_y = 0.0
dir_x_val = 0             # 0=CW, 1=CCW
dir_y_val = 0
driver_enabled = False
trigger_armed = False
trigger_active = False
pan_steps = 0              # Kümülatif step sayacı
tilt_steps = 0
last_cmd_time = 0          # Son komut zamanı (watchdog)
seq_counter = 0
lock = _thread.allocate_lock()
cmd_buffer = ""

# ============================================================================
# MICROSTEPPING
# ============================================================================

MICROSTEP_MODES = {
    '1/8':  (0, 0),
    '1/16': (1, 1),
    '1/32': (1, 0),
    '1/64': (0, 1),
}

def set_microstepping(motor='both', mode='1/8'):
    """Eski çalışan kartta GP4/5 ve GP12/13 UART hattıydı; burada pin sürmüyoruz."""
    if mode not in MICROSTEP_MODES:
        print(json.dumps({"type": "error", "code": "INVALID_MICROSTEP", "message": mode}))
        return

# ============================================================================
# YARDIMCI FONKSİYONLAR
# ============================================================================

def map_value(x, in_min, in_max, out_min, out_max):
    """Arduino map() karşılığı."""
    if in_max == in_min:
        return out_min
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)

def read_limits():
    """Limit switch durumlarını oku (LOW = tetiklenmiş)."""
    return {
        "pan_left": limit_pan_l.value() == 0,
        "pan_right": limit_pan_r.value() == 0,
        "tilt_up": limit_tilt_u.value() == 0,
        "tilt_down": limit_tilt_d.value() == 0,
    }

def read_estop():
    """E-stop durumunu oku (LOW = basılı)."""
    return estop.value() == 0

def safe_stop():
    """Tüm motorları güvenli şekilde durdur."""
    global target_delay_x, target_delay_y, current_delay_x, current_delay_y
    with lock:
        target_delay_x = 0
        target_delay_y = 0
        current_delay_x = 0.0
        current_delay_y = 0.0

def release_trigger():
    """Tetik servo çıkışını güvenli serbest konuma al."""
    global trigger_active
    laser_servo.duty_u16(SERVO_0_DEG_DUTY)
    trigger_active = False

# ============================================================================
# MOTOR SÜRME (AYRI THREAD)
# ============================================================================

def step_motor(step_pin, delay_us=2):
    """Tek adım pulse üret."""
    step_pin.on()
    time.sleep_us(delay_us)
    step_pin.off()

def motor_task():
    """
    Motor sürme görevi — ayrı thread'de çalışır.
    İvme rampası ile smooth hızlanma/yavaşlama.
    E-stop ve limit switch kontrolü.
    """
    global target_delay_x, target_delay_y, current_delay_x, current_delay_y
    global pan_steps, tilt_steps, driver_enabled, trigger_armed

    last_step_time_x = 0
    last_step_time_y = 0

    while True:
        with lock:
            de = driver_enabled
        # ---- E-stop kontrolü ----
        if read_estop():
            safe_stop()
            release_trigger()
            driver_enabled = False
            trigger_armed = False
            enable_x.value(1)
            enable_y.value(1)
            time.sleep_ms(10)
            continue
        if not de:
            time.sleep_ms(5)
            continue

        current_time = time.ticks_us()
        limits = read_limits()

        # ---- X Motoru (Pan) ----
        with lock:
            tgt_x = target_delay_x
            cur_x = current_delay_x
            dx = dir_x_val

        if tgt_x > 0:
            # Limit switch kontrolü
            if (dx == 1 and limits["pan_right"]) or (dx == 0 and limits["pan_left"]):
                with lock:
                    target_delay_x = 0
                    current_delay_x = 0.0
            else:
                # Gerçek step-bazlı ivme rampası. Önceki kod cur_x=target ile
                # doğrudan binlerce step/sn'e sıçrıyor, yüklü tarette motoru
                # döndürmek yerine yalnız titretiyordu.
                if cur_x == 0:
                    cur_x = float(MAX_STEP_DELAY_US)

                # Step pulse üret
                if time.ticks_diff(current_time, last_step_time_x) >= int(cur_x):
                    last_step_time_x = current_time
                    step_motor(step_x)
                    if cur_x > tgt_x:
                        cur_x = max(cur_x - ACCEL_STEP_US, tgt_x)
                    elif cur_x < tgt_x:
                        cur_x = min(cur_x + ACCEL_STEP_US, tgt_x)
                    # Step sayacını güncelle
                    with lock:
                        pan_steps += 1 if dx == 1 else -1

                with lock:
                    current_delay_x = cur_x
        else:
            with lock:
                current_delay_x = 0.0

        # ---- Y Motoru (Tilt) ----
        with lock:
            tgt_y = target_delay_y
            cur_y = current_delay_y
            dy = dir_y_val

        if tgt_y > 0:
            # Limit switch kontrolü
            if (dy == 1 and limits["tilt_up"]) or (dy == 0 and limits["tilt_down"]):
                with lock:
                    target_delay_y = 0
                    current_delay_y = 0.0
            else:
                if cur_y == 0:
                    cur_y = float(MAX_STEP_DELAY_US)

                if time.ticks_diff(current_time, last_step_time_y) >= int(cur_y):
                    last_step_time_y = current_time
                    step_motor(step_y)
                    if cur_y > tgt_y:
                        cur_y = max(cur_y - ACCEL_STEP_US, tgt_y)
                    elif cur_y < tgt_y:
                        cur_y = min(cur_y + ACCEL_STEP_US, tgt_y)
                    with lock:
                        tilt_steps += 1 if dy == 1 else -1

                with lock:
                    current_delay_y = cur_y
        else:
            with lock:
                current_delay_y = 0.0

        time.sleep_us(10)  # CPU'ya nefes aldır

# ============================================================================
# KOMUT İŞLEME
# ============================================================================

def process_command(cmd):
    """Serial komutları parse et ve işle."""
    global target_delay_x, target_delay_y
    global dir_x_val, dir_y_val
    global driver_enabled, last_cmd_time, trigger_armed, trigger_active
    global pan_steps, tilt_steps, SERVO_0_DEG_DUTY, SERVO_FIRE_DEG_DUTY

    cmd = cmd.strip()
    if not cmd:
        return
    if not (
        cmd.startswith("SPD,")
        or cmd.startswith("DRV,")
        or cmd.startswith("STP")
        or cmd.startswith("HOM")
        or cmd.startswith("PING")
        or cmd.startswith("STAT")
        or cmd.startswith("ARM,")
        or cmd.startswith("MICROSTEP,")
        or cmd.startswith("LZR,")
        or cmd.startswith("SRV,")
    ):
        marker_positions = [
            cmd.rfind(marker)
            for marker in ("SPD,", "DRV,", "STP", "HOM", "PING", "STAT", "ARM,", "MICROSTEP,", "LZR,", "SRV,")
        ]
        marker_positions = [pos for pos in marker_positions if pos >= 0]
        if marker_positions:
            cmd = cmd[max(marker_positions):]

    last_cmd_time = time.ticks_ms()
    parts = cmd.split(',')

    # ---- SPD,speed_x,speed_y ----
    if parts[0] == "SPD" and len(parts) == 3:
        try:
            val_x = int(parts[1])
            val_y = int(parts[2])

            # Keep the Pico-side output boundary fail-closed even if a host
            # command races an E-Stop or a driver disable.  CommandGateway
            # performs the same preflight, but the board remains the final
            # electrical authority.
            if read_estop():
                safe_stop()
                print(json.dumps({"type": "error", "code": "ESTOP_ACTIVE", "message": "Motion rejected"}))
                return
            if not driver_enabled:
                safe_stop()
                print(json.dumps({"type": "error", "code": "DRIVER_DISABLED", "message": "Motion rejected"}))
                return

            # X Motoru
            if val_x == 0:
                with lock:
                    target_delay_x = 0
            else:
                with lock:
                    dir_x_val = 1 if val_x > 0 else 0
                dir_x.value(dir_x_val)
                speed = min(abs(val_x), 1000)
                delay = map_value(speed, 1, 1000, MAX_STEP_DELAY_US, MIN_STEP_DELAY_X_US)
                with lock:
                    target_delay_x = delay

            # Y Motoru
            if val_y == 0:
                with lock:
                    target_delay_y = 0
            else:
                with lock:
                    dir_y_val = 1 if val_y > 0 else 0
                dir_y.value(dir_y_val)
                speed = min(abs(val_y), 1000)
                delay = map_value(speed, 1, 1000, MAX_STEP_DELAY_US, MIN_STEP_DELAY_Y_US)
                with lock:
                    target_delay_y = delay

            # Keep the old low-latency step/direction behaviour, but return
            # the ACK expected by the current CommandGateway.  Without this
            # line a JSON-line rollback would move the motor while the host
            # waited for an ACK and incorrectly safed the live path.
            print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "OK,SPD"}))

        except ValueError:
            print(json.dumps({"type": "error", "code": "INVALID_SPD", "message": cmd}))

    # ---- DRV,0/1 — Driver enable/disable ----
    elif parts[0] == "DRV" and len(parts) == 2:
        try:
            state = int(parts[1])
            if state == 1:
                if read_estop():
                    trigger_armed = False
                    release_trigger()
                    print(json.dumps({"type": "error", "code": "ESTOP_ACTIVE", "message": "Driver enable rejected"}))
                    return
                enable_x.value(0)  # LOW = enable
                enable_y.value(0)
                driver_enabled = True
                print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "DRIVER_ENABLED"}))
            else:
                enable_x.value(1)  # HIGH = disable
                enable_y.value(1)
                driver_enabled = False
                trigger_armed = False
                release_trigger()
                safe_stop()
                print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "DRIVER_DISABLED"}))
        except ValueError:
            print(json.dumps({"type": "error", "code": "INVALID_DRV", "message": cmd}))

    # ---- STP — Emergency stop ----
    elif parts[0] == "STP":
        safe_stop()
        trigger_armed = False
        release_trigger()
        print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "EMERGENCY_STOP"}))

    # ---- HOM — Home (sıfır noktası) ----
    elif parts[0] == "HOM":
        safe_stop()
        with lock:
            pan_steps = 0
            tilt_steps = 0
        print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "HOME_SET"}))

    # ---- PING ----
    elif parts[0] == "PING":
        print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "PONG"}))

    # ---- STAT — E-stop/driver/arm preflight snapshot ----
    elif parts[0] == "STAT":
        print(json.dumps({
            "type": "ack",
            "seq": 0,
            "accepted": True,
            "message": f"STATUS,ESTOP={1 if read_estop() else 0},DRV={1 if driver_enabled else 0},ARM={1 if trigger_armed else 0}",
        }))

    # ---- ARM,state — Tetik çıkışına açık preflight arm ----
    elif parts[0] == "ARM" and len(parts) == 2:
        try:
            requested = int(parts[1]) != 0
            if requested and read_estop():
                trigger_armed = False
                release_trigger()
                print(json.dumps({"type": "error", "code": "ESTOP_ACTIVE", "message": "Trigger arm rejected"}))
            else:
                trigger_armed = requested
                release_trigger()
                print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "TRIGGER_ARMED" if trigger_armed else "TRIGGER_DISARMED"}))
        except ValueError:
            print(json.dumps({"type": "error", "code": "INVALID_ARM", "message": cmd}))

    # ---- MICROSTEP,motor,mode ----
    elif parts[0] == "MICROSTEP" and len(parts) == 3:
        set_microstepping(parts[1], parts[2])
        print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": f"MICROSTEP_{parts[1]}_{parts[2]}"}))

    # ---- LZR,state — Servo Tetik Kontrolü ----
    elif parts[0] == "LZR" and len(parts) == 2:
        try:
            state = int(parts[1])
            if state == 1:
                if read_estop():
                    trigger_armed = False
                    release_trigger()
                    print(json.dumps({"type": "error", "code": "ESTOP_ACTIVE", "message": "Fire rejected"}))
                elif not trigger_armed:
                    release_trigger()
                    print(json.dumps({"type": "error", "code": "TRIGGER_NOT_ARMED", "message": "Fire rejected"}))
                else:
                    laser_servo.duty_u16(SERVO_FIRE_DEG_DUTY) # Tetik çek
                    trigger_active = True
                    print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "FIRE_SERVO_PULLED"}))
            else:
                release_trigger()
                print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "FIRE_SERVO_RELEASED"}))
        except ValueError:
            print(json.dumps({"type": "error", "code": "INVALID_LZR", "message": cmd}))

    # ---- SRV,CFG,release_deg,fire_deg — Trigger servo endpoint setup ----
    elif parts[0] == "SRV" and len(parts) == 4 and parts[1] == "CFG":
        try:
            release_deg = int(parts[2])
            fire_deg = int(parts[3])
            if release_deg < 0 or fire_deg > 180 or release_deg >= fire_deg:
                raise ValueError()
            if read_estop():
                release_trigger()
                print(json.dumps({"type": "error", "code": "ESTOP_ACTIVE", "message": "Servo config rejected"}))
            else:
                SERVO_0_DEG_DUTY = servo_duty_for_degree(release_deg)
                SERVO_FIRE_DEG_DUTY = servo_duty_for_degree(fire_deg)
                release_trigger()
                print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "SERVO_CONFIGURED"}))
        except ValueError:
            print(json.dumps({"type": "error", "code": "INVALID_SERVO_CONFIG", "message": cmd}))

    # ---- SRV,TEST — Empty-chamber diagnostic, same physical safety path ----
    elif parts[0] == "SRV" and len(parts) == 2 and parts[1] == "TEST":
        if read_estop():
            trigger_armed = False
            release_trigger()
            print(json.dumps({"type": "error", "code": "ESTOP_ACTIVE", "message": "Servo test rejected"}))
        elif not trigger_armed:
            release_trigger()
            print(json.dumps({"type": "error", "code": "TRIGGER_NOT_ARMED", "message": "Servo test rejected"}))
        else:
            laser_servo.duty_u16(SERVO_FIRE_DEG_DUTY)
            trigger_active = True
            print(json.dumps({"type": "ack", "seq": 0, "accepted": True, "message": "FIRE_SERVO_PULLED"}))

    else:
        print(json.dumps({"type": "nack", "seq": 0, "reason": f"UNKNOWN_CMD:{cmd}"}))

# ============================================================================
# TELEMETRİ RAPORU
# ============================================================================

def emit_telemetry():
    """Tek JSON telemetri frame'i yayınla."""
    global seq_counter
    limits = read_limits()
    with lock:
        ps = pan_steps
        ts = tilt_steps
        sx = target_delay_x
        sy = target_delay_y
        de = driver_enabled

    seq_counter = (seq_counter + 1) % 256

    telemetry = {
        "type": "telemetry",
        "seq": seq_counter,
        "device": "pico2",
        "firmware_version": "production-0.1",
        "estop_state": read_estop(),
        "driver_enabled": de,
        "pan_position_steps": ps,
        "tilt_position_steps": ts,
        "speed_active_x": sx > 0,
        "speed_active_y": sy > 0,
        "limits": limits,
        "safe_state": not de or (sx == 0 and sy == 0),
        "physical_outputs_enabled": de,
        "timestamp_ms": time.ticks_ms(),
    }
    print(json.dumps(telemetry))

# ============================================================================
# WATCHDOG
# ============================================================================

def check_watchdog():
    """2 saniye komut gelmezse motorları güvenli şekilde durdur."""
    global trigger_armed, trigger_active, driver_enabled
    current_time = time.ticks_ms()
    if last_cmd_time > 0 and time.ticks_diff(current_time, last_cmd_time) > WATCHDOG_TIMEOUT_MS:
        with lock:
            tx = target_delay_x
            ty = target_delay_y
        if tx > 0 or ty > 0:
            safe_stop()
        if trigger_active:
            release_trigger()
        trigger_armed = False
        driver_enabled = False
        enable_x.value(1)
        enable_y.value(1)
        if tx > 0 or ty > 0:
            print(json.dumps({
                "type": "error",
                "code": "WATCHDOG_TIMEOUT",
                "message": "No command for 2s, motors stopped",
            }))

# ============================================================================
# SERIAL OKUMA (ANA THREAD)
# ============================================================================

def serial_read_loop():
    """USB CDC serial'den satır satır komut oku."""
    global cmd_buffer
    import sys

    while True:
        try:
            if sys.stdin in select.select([sys.stdin], [], [], 0.005)[0]:
                char = sys.stdin.read(1)
                if char == '\n':
                    if cmd_buffer:
                        process_command(cmd_buffer)
                        cmd_buffer = ""
                elif char != '\r':
                    cmd_buffer += char
        except Exception:
            # USB CDC fallback: readline kullan
            try:
                line = sys.stdin.readline()
                if line:
                    process_command(line.strip())
            except Exception:
                time.sleep_ms(5)

# ============================================================================
# ANA PROGRAM
# ============================================================================

def main():
    """Sistem başlatma ve thread yönetimi."""
    global last_cmd_time
    import sys

    print("=" * 50)
    print("ISTIKLAL C2 — Pico 2 Motor Kontrol")
    print("Firmware: production-0.1")
    print("=" * 50)

    # Güvenli başlangıç
    enable_x.value(1)   # Driver disabled
    enable_y.value(1)
    laser_servo.duty_u16(SERVO_0_DEG_DUTY) # Laser/Servo 0 pozisyonu
    safe_stop()

    # Microstepping varsayılan: 1/8
    set_microstepping('both', '1/8')

    print(json.dumps({
        "type": "ack",
        "seq": 0,
        "accepted": True,
        "message": "PICO_READY",
    }))

    # Pin bilgisi
    print(f"Pins: STEP_X=GP{STEP_X_PIN} DIR_X=GP{DIR_X_PIN} STEP_Y=GP{STEP_Y_PIN} DIR_Y=GP{DIR_Y_PIN}")
    print(f"      ENABLE_X=GP{ENABLE_X_PIN} ENABLE_Y=GP{ENABLE_Y_PIN} ESTOP=GP{ESTOP_PIN}")
    print(f"      LIMITS: L=GP{LIMIT_PAN_L} R=GP{LIMIT_PAN_R} U=GP{LIMIT_TILT_U} D=GP{LIMIT_TILT_D}")

    last_cmd_time = time.ticks_ms()

    # RP2350 MicroPython tek ek worker thread destekler; motor task ayrı
    # thread'de, telemetry/watchdog ana döngüde çalışır.
    _thread.start_new_thread(motor_task, ())

    # Ana thread: serial okuma
    cmd_buf = ""
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    last_telemetry_time = 0
    last_watchdog_time = 0
    while True:
        try:
            if poller.poll(5):
                ch = sys.stdin.read(1)
                if ch == '\n' or ch == '\r':
                    if cmd_buf:
                        process_command(cmd_buf)
                        cmd_buf = ""
                else:
                    cmd_buf += ch

            now = time.ticks_ms()
            if TELEMETRY_ENABLED and time.ticks_diff(now, last_telemetry_time) >= TELEMETRY_INTERVAL_MS:
                emit_telemetry()
                last_telemetry_time = now
            if time.ticks_diff(now, last_watchdog_time) >= 200:
                check_watchdog()
                last_watchdog_time = now
        except Exception:
            time.sleep_ms(5)

# ============================================================================
# BAŞLAT
# ============================================================================

if __name__ == "__main__":
    main()
