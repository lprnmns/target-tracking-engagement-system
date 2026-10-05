"""
otonom_takip.py
Otonom hedef takip ve PID kontrol modülü.
"""

import threading
import time
import json
import os


class OtonomTakip:
    def __init__(self, goruntu_motoru, pico_baglanti, pid_dosya_yolu="pid_ayarlar.json"):
        self.goruntu = goruntu_motoru
        self.pico = pico_baglanti
        self._pid_dosya = pid_dosya_yolu

        self._aktif = threading.Event()
        self._thread = None

        # ================= PID KAZANÇLARI =================
        self._pid_lock = threading.Lock()

        self._varsayilanlar = {
            "kp_x": 30.0, "ki_x": 0.0, "kd_x": 5.0,
            "kp_y": 30.0, "ki_y": 0.0, "kd_y": 5.0,
            "invert_x": False, "invert_y": False
        }

        self.kp_x = self._varsayilanlar["kp_x"]
        self.ki_x = self._varsayilanlar["ki_x"]
        self.kd_x = self._varsayilanlar["kd_x"]
        self.kp_y = self._varsayilanlar["kp_y"]
        self.ki_y = self._varsayilanlar["ki_y"]
        self.kd_y = self._varsayilanlar["kd_y"]
        self.invert_x = self._varsayilanlar["invert_x"]
        self.invert_y = self._varsayilanlar["invert_y"]

        self._pid_yukle()

        self._integral_x = 0.0
        self._integral_y = 0.0
        self._onceki_hata_x = 0.0
        self._onceki_hata_y = 0.0
        self._son_zaman = time.time()

        # ================= HEDEF TAKİP DURUMU =================
        self._hedef_lock = threading.Lock()
        self._hedef_kilitli = False
        self._hedef_merkez = None
        self._kayip_sayac = 0
        self._kayip_limit = 20

    # ================= PID AYAR YÖNETİMİ =================

    def _pid_yukle(self):
        try:
            if os.path.isfile(self._pid_dosya):
                with open(self._pid_dosya, "r", encoding="utf-8") as f:
                    veri = json.load(f)
                with self._pid_lock:
                    self.kp_x = float(veri.get("kp_x", self.kp_x))
                    self.ki_x = float(veri.get("ki_x", self.ki_x))
                    self.kd_x = float(veri.get("kd_x", self.kd_x))
                    self.kp_y = float(veri.get("kp_y", self.kp_y))
                    self.ki_y = float(veri.get("ki_y", self.ki_y))
                    self.kd_y = float(veri.get("kd_y", self.kd_y))
                    self.invert_x = bool(veri.get("invert_x", self.invert_x))
                    self.invert_y = bool(veri.get("invert_y", self.invert_y))
                print(f"[Bilgi] PID ayarları yüklendi: {self._pid_dosya}")
        except Exception as hata:
            print(f"[Uyarı] PID ayarları yüklenemedi: {hata}")

    def _pid_kaydet(self):
        try:
            with self._pid_lock:
                veri = {
                    "kp_x": self.kp_x, "ki_x": self.ki_x, "kd_x": self.kd_x,
                    "kp_y": self.kp_y, "ki_y": self.ki_y, "kd_y": self.kd_y,
                    "invert_x": self.invert_x, "invert_y": self.invert_y
                }
            with open(self._pid_dosya, "w", encoding="utf-8") as f:
                json.dump(veri, f, indent=4, ensure_ascii=False)
        except Exception as hata:
            print(f"[Uyarı] PID ayarları kaydedilemedi: {hata}")

    def pid_guncelle(self, kp_x=None, ki_x=None, kd_x=None,
                     kp_y=None, ki_y=None, kd_y=None,
                     invert_x=None, invert_y=None):
        try:
            with self._pid_lock:
                if kp_x is not None: self.kp_x = float(kp_x)
                if ki_x is not None: self.ki_x = float(ki_x)
                if kd_x is not None: self.kd_x = float(kd_x)
                if kp_y is not None: self.kp_y = float(kp_y)
                if ki_y is not None: self.ki_y = float(ki_y)
                if kd_y is not None: self.kd_y = float(kd_y)
                if invert_x is not None: self.invert_x = bool(invert_x)
                if invert_y is not None: self.invert_y = bool(invert_y)
            self._pid_kaydet()
            return True
        except (ValueError, TypeError):
            return False

    def pid_degerlerini_al(self):
        with self._pid_lock:
            return {
                "kp_x": self.kp_x, "ki_x": self.ki_x, "kd_x": self.kd_x,
                "kp_y": self.kp_y, "ki_y": self.ki_y, "kd_y": self.kd_y,
                "invert_x": self.invert_x, "invert_y": self.invert_y
            }

    def pid_sifirla(self):
        with self._pid_lock:
            for k, v in self._varsayilanlar.items():
                setattr(self, k, v)
        self._pid_kaydet()

    def _integral_sifirla(self):
        self._integral_x = 0.0
        self._integral_y = 0.0
        self._onceki_hata_x = 0.0
        self._onceki_hata_y = 0.0

    # ================= HEDEF SEÇİMİ =================

    def hedef_sec(self, kutu):
        x1, y1, x2, y2 = kutu
        merkez_x = (x1 + x2) / 2.0
        merkez_y = (y1 + y2) / 2.0
        with self._hedef_lock:
            self._hedef_kilitli = True
            self._hedef_merkez = (merkez_x, merkez_y)
            self._kayip_sayac = 0
        self._integral_sifirla()

    def hedef_kilidini_ac(self):
        with self._hedef_lock:
            self._hedef_kilitli = False
            self._hedef_merkez = None
            self._kayip_sayac = 0

    def hedef_durumunu_al(self):
        with self._hedef_lock:
            return self._hedef_kilitli, self._hedef_merkez

    # ================= THREAD KONTROL =================

    def baslat(self):
        self._aktif.set()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._dongu, daemon=True)
            self._thread.start()

    def dur(self):
        self._aktif.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._integral_sifirla()
        self.hedef_kilidini_ac()
        try:
            self.pico.komut_gonder("X 0")
            self.pico.komut_gonder("Y 0")
        except Exception:
            pass

    # ================= ANA DÖNGÜ =================

    def _dongu(self):
        while self._aktif.is_set():
            simdi = time.time()
            dt = simdi - self._son_zaman
            if dt <= 0.0:
                dt = 0.001
            self._son_zaman = simdi

            if not self.pico.bagli_mi():
                time.sleep(0.05)
                continue

            with self._hedef_lock:
                kilitli = self._hedef_kilitli
                hedef_merkez = self._hedef_merkez

            if not kilitli:
                self.pico.komut_gonder("X 0")
                self.pico.komut_gonder("Y 0")
                self._integral_sifirla()
                time.sleep(0.02)
                continue

            kare, tespitler, _, _, _ = self.goruntu.verileri_al()

            if kare is None or not tespitler:
                self._kayip_sayac += 1
                if self._kayip_sayac >= self._kayip_limit:
                    self.hedef_kilidini_ac()
                self.pico.komut_gonder("X 0")
                self.pico.komut_gonder("Y 0")
                self._integral_sifirla()
                time.sleep(0.02)
                continue

            # Centroid takibi
            hedef = None
            min_mesafe = float('inf')

            for t in tespitler:
                x1, y1, x2, y2 = t["kutu"]
                mx = (x1 + x2) / 2.0
                my = (y1 + y2) / 2.0
                mesafe = ((mx - hedef_merkez[0]) ** 2 + (my - hedef_merkez[1]) ** 2) ** 0.5
                if mesafe < min_mesafe:
                    min_mesafe = mesafe
                    hedef = t

            if hedef is None or min_mesafe > 400:
                self._kayip_sayac += 1
                if self._kayip_sayac >= self._kayip_limit:
                    self.hedef_kilidini_ac()
                self.pico.komut_gonder("X 0")
                self.pico.komut_gonder("Y 0")
                self._integral_sifirla()
                time.sleep(0.02)
                continue

            self._kayip_sayac = 0

            x1, y1, x2, y2 = hedef["kutu"]
            yukseklik, genislik = kare.shape[:2]

            hedef_merkez_x = (x1 + x2) / 2.0
            hedef_merkez_y = (y1 + y2) / 2.0

            with self._hedef_lock:
                self._hedef_merkez = (hedef_merkez_x, hedef_merkez_y)

            ekran_merkez_x = genislik / 2.0
            ekran_merkez_y = yukseklik / 2.0

            # ============================================================
            # YÖN DÜZELTMESİ BURADA:
            # Hata hesaplaması: hedef - merkez (hedef merkeze göre nerede)
            # ============================================================
            hata_x = hedef_merkez_x - ekran_merkez_x
            hata_y = hedef_merkez_y - ekran_merkez_y

            with self._pid_lock:
                kp_x, ki_x, kd_x = self.kp_x, self.ki_x, self.kd_x
                kp_y, ki_y, kd_y = self.kp_y, self.ki_y, self.kd_y
                inv_x = self.invert_x
                inv_y = self.invert_y

            # X ekseni PID - Integral windup koruması ile
            p_x = hata_x * kp_x
            self._integral_x += hata_x * dt
            # Integral windup koruması: integral değeri sınırla
            max_integral = 5000.0
            self._integral_x = max(-max_integral, min(max_integral, self._integral_x))
            i_x = self._integral_x * ki_x
            d_x = ((hata_x - self._onceki_hata_x) / dt) * kd_x
            self._onceki_hata_x = hata_x
            hiz_x = p_x + i_x + d_x

            # Y ekseni PID - Integral windup koruması ile
            p_y = hata_y * kp_y
            self._integral_y += hata_y * dt
            # Integral windup koruması: integral değeri sınırla
            self._integral_y = max(-max_integral, min(max_integral, self._integral_y))
            i_y = self._integral_y * ki_y
            d_y = ((hata_y - self._onceki_hata_y) / dt) * kd_y
            self._onceki_hata_y = hata_y
            hiz_y = p_y + i_y + d_y

            # Invert bayrakları (ekstra ters çevirme gerekirse)
            if inv_x:
                hiz_x = -hiz_x
            if inv_y:
                hiz_y = -hiz_y

            # Motor hız limitleri - Güvenlik sınırlaması
            max_hiz = 12000.0
            hiz_x = max(-max_hiz, min(max_hiz, hiz_x))
            hiz_y = max(-max_hiz, min(max_hiz, hiz_y))
            
            # İvme sınırlaması (ani hız değişimlerini önle)
            max_ivme = 5000.0  # birim/s²
            if hasattr(self, '_onceki_hiz_x'):
                delta_hiz_x = hiz_x - self._onceki_hiz_x
                max_delta = max_ivme * dt
                if abs(delta_hiz_x) > max_delta:
                    hiz_x = self._onceki_hiz_x + (max_delta if delta_hiz_x > 0 else -max_delta)
            self._onceki_hiz_x = hiz_x
            
            if hasattr(self, '_onceki_hiz_y'):
                delta_hiz_y = hiz_y - self._onceki_hiz_y
                max_delta = max_ivme * dt
                if abs(delta_hiz_y) > max_delta:
                    hiz_y = self._onceki_hiz_y + (max_delta if delta_hiz_y > 0 else -max_delta)
            self._onceki_hiz_y = hiz_y

            # ============================================================
            # EKSEN TAKASI DÜZELTMESİ
            # Manuel moddaki eşleştirme:
            #   Joystick Y (ileri/geri, dikey) -> motorX
            #   Joystick X (sağ/sol, yatay)    -> motorY
            # Bu nedenle:
            #   Kamera Y (dikey, hiz_y) -> motorX komutu
            #   Kamera X (yatay, hiz_x) -> motorY komutu
            # ============================================================
            self.pico.komut_gonder(f"X {hiz_y:.1f}")
            self.pico.komut_gonder(f"Y {hiz_x:.1f}")

            time.sleep(0.02)