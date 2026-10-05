/*
Hava Savunma Sistemi - Çift Çekirdek & Gelişmiş TMC2209 Kontrol Kodu
Teknofest Çelik Kubbe

DÜZELTME:
- TMC2209'da olmayan ADC_VSUPPLY() kaldırıldı.
- 24V / Acil stop takibi için test_connection() ve uv_cp() (Charge Pump Undervoltage) kullanıldı.
- PWR,RAW komutu kaldırıldı, PWR,DURUM yazılımsal flag'leri döndürüyor.
*/

#include <math.h>
#include <Servo.h>

#ifdef ARDUINO_ARCH_RP2040
#include <TMCStepper.h>
#include <AccelStepper.h>
#endif

#define BAUD_RATE 460800

// ================= PIN TANIMLARI =================
#define X_STEP 1
#define X_DIR 0
#define X_EN 6
#define X_TX 4
#define X_RX 5

#define Y_STEP 9
#define Y_DIR 8
#define Y_EN 14
#define Y_TX 12
#define Y_RX 13

#define LIMIT_X_PIN 22
#define LIMIT_Y_PIN 26
#define SERVO_PIN 15

// ================= SÜRÜCÜ / HAREKET AYARLARI =================
#define R_SENSE 0.11f
#define AKIM_MA 1200
#define MIKROSTEP 8
#define MAX_HIZ 12000L
#define MANUAL_MAX_HIZ 8000L
#define MIN_HIZ 100L
#define IVME 35000.0f
#define TIMEOUT_MS 300
#define TPWMTHRS_VAL 250

// ================= MEKANİK / LİMİT AYARLARI =================
#define X_DISLI 20
#define Y_DISLI 30
#define X_MIN_DERECE 0
#define X_MAX_DERECE 60
#define Y_MIN_DERECE 0
#define Y_MAX_DERECE 270

// ================= AUTOHOME AYARLARI =================
#define HOMING_SPEED 1500
#define BACKOFF_SPEED 600
#define BACKOFF_STEPS 100
#define HOMING_TIMEOUT 20000

// ================= MERKEZ KONUM AYARLARI =================
#define CENTER_SPEED 3000
#define CENTER_ACC 8000

// ================= SERVO AYARLARI =================
#define SERVO_MIN_ANGLE 0
#define SERVO_MAX_ANGLE 180
#define SERVO_SPEED 2

// ================= GLOBAL SÜRÜCÜ NESNELERİ =================
#ifdef ARDUINO_ARCH_RP2040
TMC2209Stepper tmcX(&Serial2, R_SENSE, 0b00);
TMC2209Stepper tmcY(&Serial1, R_SENSE, 0b00);

AccelStepper motorX(AccelStepper::DRIVER, X_STEP, X_DIR);
AccelStepper motorY(AccelStepper::DRIVER, Y_STEP, Y_DIR);
#endif

// ================= VOLATILE ÇEKİRDEKLER ARASI VERİLER =================
volatile int hizModu = 1;
volatile int modManuel = 1;
volatile bool motorAcik = true;
volatile int motorUStep = MIKROSTEP;

volatile float hedefX = 0, hedefY = 0;
volatile float anlikX = 0, anlikY = 0;

volatile long motorPozX = 0;
volatile long motorPozY = 0;

volatile uint32_t sonRx = 0;

String serialBuffer = "";

enum HomingState {
  HS_IDLE, HS_X_GO, HS_X_BACK, HS_Y_GO, HS_Y_BACK,
  HS_CENTER_MOVE, HS_DONE, HS_FAIL
};

volatile int homingState = HS_IDLE;
volatile bool homingIstek = false;
volatile bool homingIptalIstek = false;

uint32_t homingStartMs = 0;
long backoffHedef = 0;
long centerHedefX = 0;
long centerHedefY = 0;

volatile bool posGecerli = false;

// ================= 24V / ACİL DURDUR TAKİBİ =================
#define BESLEME_KONTROL_MS 150

volatile bool besleme24VAktif = true;
volatile bool acilDurumAktif = false;

uint32_t sonBeslemeKontrol = 0;
uint8_t beslemeLowSayac = 0;
uint8_t beslemeHighSayac = 0;

// ================= PAN & TILT LİMİTLERİ =================
volatile float limitPanMinDeg = 0.0f;
volatile float limitPanMaxDeg = 270.0f;
#define PAN_LIMIT_MARGIN 0.5f

volatile float limitTiltMinDeg = 0.0f;
volatile float limitTiltMaxDeg = 60.0f;
#define TILT_LIMIT_MARGIN 0.5f

// ================= GLOBAL DEĞİŞKENLER - SERVO =================
Servo servoMotor;

bool servoHareketEdiyor = false;
unsigned long servoBeklemeSuresi = 0;
unsigned long servoBeklemeBaslangic = 0;

int servoBaslangicAci = 90;
int servoBitisAci = 45;
bool servoDonusAsamasi = false;
int servoMevcutAci = 90;
unsigned long sonServoAdimZamani = 0;

bool balloonTriggerRequested = false;
bool balonModeliAktif = false;

// ================= LİMİT HESAPLARI =================
long maxX() { return (200L * motorUStep * X_DISLI * X_MAX_DERECE) / 360L; }
long minX() { return (200L * motorUStep * X_DISLI * X_MIN_DERECE) / 360L; }
long maxY() { return (200L * motorUStep * Y_DISLI * Y_MAX_DERECE) / 360L; }
long minY() { return (200L * motorUStep * Y_DISLI * Y_MIN_DERECE) / 360L; }

float xDerece() { return (motorPozX * 360.0) / (200L * motorUStep * X_DISLI); }
float yDerece() { return (motorPozY * 360.0) / (200L * motorUStep * Y_DISLI); }

long maksHiz() { return hizModu ? MAX_HIZ : MAX_HIZ / 4; }

float sinirla(float h) {
  float m = (float)maksHiz();
  return h > m ? m : (h < -m ? -m : h);
}

bool hareketVar() {
  return fabsf(anlikX) > 1 || fabsf(hedefX) > 1 || fabsf(anlikY) > 1 || fabsf(hedefY) > 1;
}

bool homingAktifMi() {
  return homingState != HS_IDLE && homingState != HS_DONE && homingState != HS_FAIL;
}

bool xLimitTetikli() { return digitalRead(LIMIT_X_PIN) == LOW; }
bool yLimitTetikli() { return digitalRead(LIMIT_Y_PIN) == LOW; }

void motorlariDur() {
  hedefX = 0;
  hedefY = 0;
}

// ================= 24V KONTROLÜ (TMC2209 UYUMLU) =================
void besleme24VKontrolEt() {
#ifdef ARDUINO_ARCH_RP2040
  if (millis() - sonBeslemeKontrol < BESLEME_KONTROL_MS) return;
  sonBeslemeKontrol = millis();

  // TMC2209'da dahili voltaj ölçer yoktur.
  // 1. test_connection() == 0 : UART haberleşmesi başarılı (Mantık gücü var)
  // 2. uv_cp() == false : Motor beslemesi (VM/24V) yeterli, charge pump çalışıyor.
  // Eğer UART yanıt vermiyorsa VEYA charge pump undervoltage hatası veriyorsa 24V/Güç kesik demektir.
  
  bool x_ok = (tmcX.test_connection() == 0) && !tmcX.uv_cp();
  bool y_ok = (tmcY.test_connection() == 0) && !tmcY.uv_cp();
  
  bool surucuAktif = x_ok && y_ok;

  // 24V düşük / acil stop basılmış
  if (!surucuAktif) {
    beslemeLowSayac++;
    beslemeHighSayac = 0;

    if (beslemeLowSayac >= 3 && besleme24VAktif) {
      besleme24VAktif = false;
      acilDurumAktif = true;

      if (homingAktifMi()) {
        homingIptalIstek = true;
      }

      motorlariDur();
      Serial.println("ERR,ACIL_DURDUR");
    }
  }
  // 24V geri geldi / acil stop bırakıldı
  else {
    beslemeHighSayac++;
    beslemeLowSayac = 0;

    if (beslemeHighSayac >= 3 && !besleme24VAktif) {
      besleme24VAktif = true;
      acilDurumAktif = false;

      // Güç kesildiği için pozisyon güvenilir değil
      posGecerli = false;
      motorlariDur();

      Serial.println("OK,ACIL_DURDUR_KALKTI");
      Serial.println("OK,HOME_GEREKLI");
    }
  }
#endif
}

#ifdef ARDUINO_ARCH_RP2040
void surucuKur(TMC2209Stepper &d) {
  d.begin();
  d.rms_current(AKIM_MA);
  d.microsteps(motorUStep);
  d.intpol(true);
  d.pwm_autoscale(true);
  d.en_spreadCycle(false);
  d.TPWMTHRS(TPWMTHRS_VAL);
}
#endif

// ================= AUTOHOME FONKSİYONLARI =================
void homingBaslat() {
  if (homingAktifMi()) return;
  homingIstek = true;
}

void homingIptal() {
  if (!homingAktifMi()) return;
  homingIptalIstek = true;
}

void homingCalis() {
  uint32_t now = millis();

  if (homingState != HS_CENTER_MOVE && now - homingStartMs > HOMING_TIMEOUT) {
#ifdef ARDUINO_ARCH_RP2040
    motorX.setSpeed(0);
    motorY.setSpeed(0);
    motorX.stop();
    motorY.stop();
#endif
    homingState = HS_FAIL;
    Serial.println("ERR,HOME_FAIL");
    return;
  }

  switch (homingState) {
    case HS_X_GO:
      if (xLimitTetikli()) {
#ifdef ARDUINO_ARCH_RP2040
        motorX.setSpeed(0);
        motorX.runSpeed();
        backoffHedef = motorX.currentPosition() + BACKOFF_STEPS;
#endif
        homingState = HS_X_BACK;
        return;
      }
#ifdef ARDUINO_ARCH_RP2040
      motorX.setSpeed(-HOMING_SPEED);
      motorX.runSpeed();
#endif
      break;

    case HS_X_BACK:
#ifdef ARDUINO_ARCH_RP2040
      if (motorX.currentPosition() >= backoffHedef) {
        motorX.setSpeed(0);
        motorX.runSpeed();
        motorX.setCurrentPosition(0);
        homingState = HS_Y_GO;
        homingStartMs = now;
        return;
      }
      motorX.setSpeed(BACKOFF_SPEED);
      motorX.runSpeed();
#endif
      break;

    case HS_Y_GO:
      if (yLimitTetikli()) {
#ifdef ARDUINO_ARCH_RP2040
        motorY.setSpeed(0);
        motorY.runSpeed();
        backoffHedef = motorY.currentPosition() + BACKOFF_STEPS;
#endif
        homingState = HS_Y_BACK;
        return;
      }
#ifdef ARDUINO_ARCH_RP2040
      motorY.setSpeed(-HOMING_SPEED);
      motorY.runSpeed();
#endif
      break;

    case HS_Y_BACK:
#ifdef ARDUINO_ARCH_RP2040
      if (motorY.currentPosition() >= backoffHedef) {
        motorY.setSpeed(0);
        motorY.runSpeed();
        motorY.setCurrentPosition(0);

        posGecerli = true;

        Serial.println("OK,HOME_DONE");
        Serial.println("OK,HOME_POS,X:0.00,Y:0.00");

        centerHedefX = maxX() / 2;
        centerHedefY = maxY() / 2;

        motorX.setMaxSpeed(CENTER_SPEED);
        motorX.setAcceleration(CENTER_ACC);
        motorX.moveTo(centerHedefX);

        motorY.setMaxSpeed(CENTER_SPEED);
        motorY.setAcceleration(CENTER_ACC);
        motorY.moveTo(centerHedefY);

        homingState = HS_CENTER_MOVE;
        return;
      }
      motorY.setSpeed(BACKOFF_SPEED);
      motorY.runSpeed();
#endif
      break;

    case HS_CENTER_MOVE:
#ifdef ARDUINO_ARCH_RP2040
      motorX.run();
      motorY.run();

      if (motorX.distanceToGo() == 0 && motorY.distanceToGo() == 0) {
        motorX.setMaxSpeed(MAX_HIZ);
        motorX.setAcceleration(IVME);
        motorY.setMaxSpeed(MAX_HIZ);
        motorY.setAcceleration(IVME);

        motorPozX = motorX.currentPosition();
        motorPozY = motorY.currentPosition();

        Serial.print("OK,CENTER_POS,X:");
        Serial.print(xDerece(), 2);
        Serial.print(",Y:");
        Serial.println(yDerece(), 2);

        homingState = HS_IDLE;
        Serial.println("OK,CENTER_DONE");
        return;
      }
#endif
      break;

    default:
      break;
  }
}

// ================= NON-BLOCKING SERVO =================
void servoKontrol() {
  if (!servoHareketEdiyor) return;

  unsigned long simdi = millis();

  if (!servoDonusAsamasi) {
    if (servoMevcutAci != servoBitisAci) {
      if (simdi - sonServoAdimZamani >= SERVO_SPEED) {
        sonServoAdimZamani = simdi;

        int hedefAci = servoBitisAci;
        int adim = (hedefAci > servoMevcutAci) ? 1 : -1;

        servoMevcutAci += adim;
        servoMotor.write(servoMevcutAci);

        if (servoMevcutAci == hedefAci) {
          servoBeklemeBaslangic = simdi;
          Serial.println("OK,SERVO_HEDEFTE");
        }
      }
    } else {
      if (simdi - servoBeklemeBaslangic >= servoBeklemeSuresi) {
        servoDonusAsamasi = true;
        servoMevcutAci = servoBitisAci;
        sonServoAdimZamani = simdi;
        Serial.println("OK,SERVO_DONUS_BASLATILDI");
      }
    }
  } else {
    if (servoMevcutAci != servoBaslangicAci) {
      if (simdi - sonServoAdimZamani >= SERVO_SPEED) {
        sonServoAdimZamani = simdi;

        int hedefAci = servoBaslangicAci;
        int adim = (hedefAci > servoMevcutAci) ? 1 : -1;

        servoMevcutAci += adim;
        servoMotor.write(servoMevcutAci);

        if (servoMevcutAci == hedefAci) {
          servoHareketEdiyor = false;
          servoDonusAsamasi = false;
          Serial.println("OK,SERVO_TAMAMLANDI");
        }
      }
    }
  }
}

void servoBaslat(int baslangic, int bitis, int bekleme) {
  if (servoHareketEdiyor) {
    Serial.println("ERR,SERVO_MESGUL");
    return;
  }

  if (baslangic < SERVO_MIN_ANGLE || baslangic > SERVO_MAX_ANGLE ||
      bitis < SERVO_MIN_ANGLE || bitis > SERVO_MAX_ANGLE ||
      bekleme < 50 || bekleme > 2000) {
    Serial.println("ERR,SERVO_PARAMETRE_HATASI");
    return;
  }

  servoBaslangicAci = baslangic;
  servoBitisAci = bitis;
  servoBeklemeSuresi = bekleme;
  servoMevcutAci = baslangic;

  servoHareketEdiyor = true;
  servoDonusAsamasi = false;
  sonServoAdimZamani = millis();

  servoMotor.write(servoMevcutAci);

  Serial.println("OK,SERVO_BASLATILDI");
}

void balloonPatlat() {
  servoBaslat(0, 120, 100);
}

// ================= CORE 0: KOMUT VE SERVO YÖNETİMİ =================
void komutIsle(String cmd) {
  cmd.trim();
  if (!cmd.length()) return;

  sonRx = millis();

  if (cmd == "PING") {
    Serial.println("OK,PONG");
    return;
  }

  if (cmd == "POZ" || cmd == "GET_POS") {
    Serial.print("OK,POZ,X:");
    Serial.print(xDerece(), 2);
    Serial.print(",Y:");
    Serial.println(yDerece(), 2);
    return;
  }

  if (cmd == "HB") {
    bool tmcX_hata = false;
    bool tmcY_hata = false;

#ifdef ARDUINO_ARCH_RP2040
    tmcX_hata = tmcX.otpw();
    tmcY_hata = tmcY.otpw();
#endif

    Serial.print("OK,HB|SERVO:");
    Serial.print(servoHareketEdiyor ? "1" : "0");

    Serial.print("|BALON_MODELI:");
    Serial.print(balonModeliAktif ? "1" : "0");

    Serial.print("|TMC_X_WARN:");
    Serial.print(tmcX_hata ? "1" : "0");

    Serial.print("|TMC_Y_WARN:");
    Serial.print(tmcY_hata ? "1" : "0");

    Serial.print("|24V:");
    Serial.print(besleme24VAktif ? "1" : "0");

    Serial.print("|ACIL:");
    Serial.println(acilDurumAktif ? "1" : "0");

    return;
  }

  if (cmd == "BALON_MODELI,DURUM") {
    Serial.print("OK,BALON_MODELI:");
    Serial.println(balonModeliAktif ? "AKTIF" : "PASIF");
    return;
  }

  if (cmd == "BALON_MODELI,AKTIF") {
    balonModeliAktif = true;
    Serial.println("OK,BALON_MODELI_AKTIF");
    return;
  }

  if (cmd == "BALON_MODELI,PASIF") {
    balonModeliAktif = false;
    Serial.println("OK,BALON_MODELI_PASIF");
    return;
  }

  if (cmd == "STP") {
    homingIptal();
    motorlariDur();
    Serial.println("OK,STOP");
    return;
  }

  if (cmd.startsWith("LIMIT,PAN,")) {
    String p = cmd.substring(10);
    int v = p.indexOf(',');

    if (v > 0) {
      float mn = p.substring(0, v).toFloat();
      float mx = p.substring(v + 1).toFloat();

      if (mx <= mn) mx = mn + 5.0f;

      if (mn < 0) mn = 0;
      if (mn > 270) mn = 270;
      if (mx < 0) mx = 0;
      if (mx > 270) mx = 270;

      if (mx <= mn) {
        Serial.println("ERR,LIMIT");
        return;
      }

      limitPanMinDeg = mn;
      limitPanMaxDeg = mx;

      Serial.println("OK,LIMIT_SET");
    } else {
      Serial.println("ERR,LIMIT");
    }

    return;
  }

  if (cmd.startsWith("LIMIT,TILT,")) {
    String p = cmd.substring(11);
    int v = p.indexOf(',');

    if (v > 0) {
      float mn = p.substring(0, v).toFloat();
      float mx = p.substring(v + 1).toFloat();

      if (mx <= mn) mx = mn + 5.0f;

      if (mn < 0) mn = 0;
      if (mn > 60) mn = 60;
      if (mx < 0) mx = 0;
      if (mx > 60) mx = 60;

      if (mx <= mn) {
        Serial.println("ERR,LIMIT");
        return;
      }

      limitTiltMinDeg = mn;
      limitTiltMaxDeg = mx;

      Serial.println("OK,LIMIT_SET");
    } else {
      Serial.println("ERR,LIMIT");
    }

    return;
  }

#ifdef ARDUINO_ARCH_RP2040

  if (cmd == "PWR" || cmd == "PWR,DURUM") {
    Serial.print("OK,PWR,24V:");
    Serial.print(besleme24VAktif ? "1" : "0");
    Serial.print(",ACIL:");
    Serial.println(acilDurumAktif ? "1" : "0");
    return;
  }

  if (cmd == "HOME") {
    homingBaslat();
    return;
  }

  if (cmd == "MODE,MANUAL") {
    modManuel = 1;
    Serial.println("OK,MODE_MANUAL");
    return;
  }

  if (cmd == "MODE,AUTO") {
    modManuel = 0;
    Serial.println("OK,MODE_AUTO");
    return;
  }

  if (cmd.startsWith("MODE,SPEED,")) {
    int m = cmd.substring(11).toInt();
    if (m == 0 || m == 1) {
      hizModu = m;
      Serial.println(m ? "OK,MODE_SPEED_FAST" : "OK,MODE_SPEED_FINE");
    }
    return;
  }

  if (cmd.startsWith("USTEP,")) {
    int u = cmd.substring(6).toInt();

    if (u >= 1 && u <= 64 && !hareketVar() && !homingAktifMi()) {
      motorUStep = u;
      tmcX.microsteps(u);
      tmcY.microsteps(u);

      posGecerli = false;

      Serial.print("OK,USTEP_");
      Serial.println(u);
    } else {
      Serial.println("ERR,USTEP");
    }

    return;
  }

  if (cmd == "MOTOR,ON") {
    digitalWrite(X_EN, LOW);
    digitalWrite(Y_EN, LOW);
    motorAcik = true;
    Serial.println("OK,MOTOR_ON");
    return;
  }

  if (cmd == "MOTOR,OFF") {
    motorlariDur();
    digitalWrite(X_EN, HIGH);
    digitalWrite(Y_EN, HIGH);
    motorAcik = false;
    Serial.println("OK,MOTOR_OFF");
    return;
  }

  if (cmd.startsWith("X ")) {
    hedefX = sinirla(cmd.substring(2).toFloat());
    return;
  }

  if (cmd.startsWith("Y ")) {
    hedefY = sinirla(cmd.substring(2).toFloat());
    return;
  }

#endif

  if (cmd.startsWith("SERVO,")) {
    String parametreler = cmd.substring(6);

    int virgul1 = parametreler.indexOf(',');
    int virgul2 = parametreler.indexOf(',', virgul1 + 1);

    if (virgul1 > 0 && virgul2 > virgul1) {
      int baslangic = parametreler.substring(0, virgul1).toInt();
      int bitis = parametreler.substring(virgul1 + 1, virgul2).toInt();
      int bekleme = parametreler.substring(virgul2 + 1).toInt();

      servoBaslat(baslangic, bitis, bekleme);
    } else {
      Serial.println("ERR,SERVO_FORMAT_HATASI");
    }

    return;
  }

  if (cmd == "BALON,PATLAT") {
    balloonPatlat();
    return;
  }

  Serial.println("OK,KOMUT_ALINDI");
}

void satirIsle(String line) {
  line.trim();
  if (!line.length()) return;

  sonRx = millis();

  char c = line.charAt(0);
  bool harf = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');

  if (harf) {
    komutIsle(line);
    return;
  }

  int v1 = line.indexOf(',');
  int v2 = line.indexOf(',', v1 + 1);
  int v3 = line.indexOf(',', v2 + 1);
  int v4 = line.indexOf(',', v3 + 1);

  if (v1 <= 0 || v2 <= v1 || v3 <= v2 || v4 <= v3) return;

  int x = line.substring(0, v1).toInt();
  int y = line.substring(v1 + 1, v2).toInt();
  int tetik = line.substring(v2 + 1, v3).toInt();

  if (tetik == 1 && !balloonTriggerRequested) {
    balloonTriggerRequested = true;
    balloonPatlat();
  } else if (tetik == 0) {
    balloonTriggerRequested = false;
  }

#ifdef ARDUINO_ARCH_RP2040
  if (!modManuel || homingAktifMi()) return;

  if (x == 0) {
    hedefX = 0;
  } else {
    float h = (float)map(constrain(abs(x), 1, 1000), 1, 1000, MIN_HIZ, MANUAL_MAX_HIZ);
    hedefX = sinirla(x < 0 ? -h : h);
  }

  if (y == 0) {
    hedefY = 0;
  } else {
    float h = (float)map(constrain(abs(y), 1, 1000), 1, 1000, MIN_HIZ, MANUAL_MAX_HIZ);
    hedefY = sinirla(y < 0 ? -h : h);
  }
#endif
}

// ================= CORE 0 SETUP & LOOP =================
void setup() {
  Serial.begin(BAUD_RATE);
  delay(1000);

#ifdef ARDUINO_ARCH_RP2040
  Serial2.setTX(X_TX);
  Serial2.setRX(X_RX);
  Serial2.begin(115200);

  Serial1.setTX(Y_TX);
  Serial1.setRX(Y_RX);
  Serial1.begin(115200);

  pinMode(X_EN, OUTPUT);
  pinMode(Y_EN, OUTPUT);
  digitalWrite(X_EN, LOW);
  digitalWrite(Y_EN, LOW);

  pinMode(LIMIT_X_PIN, INPUT_PULLUP);
  pinMode(LIMIT_Y_PIN, INPUT_PULLUP);

  surucuKur(tmcX);
  surucuKur(tmcY);

  motorX.setMaxSpeed(MAX_HIZ);
  motorX.setAcceleration(IVME);
  motorY.setMaxSpeed(MAX_HIZ);
  motorY.setAcceleration(IVME);
#endif

  servoMotor.attach(SERVO_PIN, 500, 2500);
  servoMotor.write(servoBaslangicAci);
  delay(500);

  sonRx = millis();

  Serial.println("OK,SISTEM_HAZIR");
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n') {
      satirIsle(serialBuffer);
      serialBuffer = "";
    } else if (c != '\r') {
      serialBuffer += c;
    }
  }

  // 24V / acil stop kontrolü
  besleme24VKontrolEt();

  if (servoHareketEdiyor) {
    servoKontrol();
  }
}

// ================= CORE 1: YÜKSEK HIZLI MOTOR DÖNGÜSÜ =================
#ifdef ARDUINO_ARCH_RP2040

uint32_t sonT1 = 0;

void setup1() {
  delay(500);
  sonT1 = micros();
}

void loop1() {
  if (homingIstek) {
    homingIstek = false;

    hedefX = 0; hedefY = 0;
    anlikX = 0; anlikY = 0;

    motorX.setSpeed(0);
    motorY.setSpeed(0);

    homingStartMs = millis();
    homingState = HS_X_GO;

    Serial.println("OK,HOME_START");
  }

  if (homingIptalIstek) {
    homingIptalIstek = false;

    motorX.setSpeed(0);
    motorY.setSpeed(0);
    motorX.stop();
    motorY.stop();

    homingState = HS_IDLE;

    Serial.println("ERR,HOME_ABORT");
  }

  if (millis() - sonRx > TIMEOUT_MS) {
    motorlariDur();
  }

  // Acil durdur aktifse tüm hareket hedeflerini sıfırla
  if (acilDurumAktif) {
    hedefX = 0;
    hedefY = 0;
    anlikX = 0;
    anlikY = 0;
  }

  uint32_t t = micros();
  float dt = (t - sonT1) / 1000000.0f;
  sonT1 = t;

  if (dt <= 0.0f) return;
  if (dt > 0.05f) dt = 0.05f;

  if (homingAktifMi()) {
    homingCalis();
  } else {
    if (xLimitTetikli()) motorX.setCurrentPosition(0);
    if (yLimitTetikli()) motorY.setCurrentPosition(0);

    float hx = hedefX, hy = hedefY;

    if (xLimitTetikli() && hx < 0) hx = 0;
    if (yLimitTetikli() && hy < 0) hy = 0;

    if (posGecerli) {
      long px = motorX.currentPosition();
      if ((px <= minX() && hx < 0) || (px >= maxX() && hx > 0)) hx = 0;

      long py = motorY.currentPosition();
      if ((py <= minY() && hy < 0) || (py >= maxY() && hy > 0)) hy = 0;

      float xDeg = (px * 360.0f) / (200L * motorUStep * X_DISLI);
      if (hx < 0 && xDeg <= limitTiltMinDeg + TILT_LIMIT_MARGIN) hx = 0;
      if (hx > 0 && xDeg >= limitTiltMaxDeg - TILT_LIMIT_MARGIN) hx = 0;

      float yDeg = (py * 360.0f) / (200L * motorUStep * Y_DISLI);

      if (hy < 0 && yDeg <= limitPanMinDeg + PAN_LIMIT_MARGIN) hy = 0;
      if (hy > 0 && yDeg >= limitPanMaxDeg - PAN_LIMIT_MARGIN) hy = 0;
    }

    float dX = hx - anlikX;
    float adimX = IVME * dt;
    anlikX += (dX > adimX) ? adimX : (dX < -adimX) ? -adimX : dX;

    float dY = hy - anlikY;
    float adimY = IVME * dt;
    anlikY += (dY > adimY) ? adimY : (dY < -adimY) ? -adimY : dY;

    motorX.setSpeed(anlikX);
    motorY.setSpeed(anlikY);

    motorX.runSpeed();
    motorY.runSpeed();
  }

  motorPozX = motorX.currentPosition();
  motorPozY = motorY.currentPosition();
}

#endif