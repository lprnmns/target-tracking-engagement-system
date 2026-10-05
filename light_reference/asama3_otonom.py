"""
asama3_otonom.py
Aşama 3: Dost/Düşman Ayrımı ve Hiyerarşik ROI Tabanlı Balon İmhası

DÜZELTMELER:
- POZ telemetrisi olmadan kafadan açı uydurma yok
- Telemetri tazeliği kontrolü
- Telemetri yoksa tarama güvenli şekilde durur
- Yasak bölge kontrolü telemetri stale iken güvenli tarafa düşer
"""

import threading
import time
import json
import os

import numpy as np


class Asama3Otonom:
    PAN_ADIM_DERECESI = (200 * 8 * 30) / 360.0
    TILT_ADIM_DERECESI = (200 * 8 * 20) / 360.0

    def __init__(
        self,
        goruntu_motoru,
        pico_baglanti,
        pid_dosya_yolu="asama3_pid_ayarlar.json"
    ):
        self.goruntu = goruntu_motoru
        self.pico = pico_baglanti
        self._pid_dosya = pid_dosya_yolu

        self._aktif = threading.Event()
        self._thread = None
        self._durum = "SEARCH"
        self._durum_lock = threading.Lock()
        self._durum_bilgisi = "Beklemede"

        # ================= TARAMA PARAMETRELERİ =================
        self._tarama_min_derece = 90.0
        self._tarama_max_derece = 180.0
        self._tarama_hizi_deg = 10.0
        self._tarama_yon = 1
        self._mutlak_pan_aci = None

        # POZ telemetri geçerlilik/zaman bilgisi
        self._son_pan_telemetri_zamani = 0.0
        self._telemetri_uyari_son_gonderim = 0.0

        # ================= DÖNÜŞ LİMİTLERİ =================
        self._donus_min_derece = 50.0
        self._donus_max_derece = 200.0

        # ================= ATEŞ / SERVO =================
        self._ates_onay_suresi = 0.15
        self._servo_baslangic = 0
        self._servo_bitis = 120
        self._servo_bekleme = 100

        # ================= PID PARAMETRELERİ =================
        self._pid_lock = threading.Lock()

        self._kp_x = 7.0
        self._ki_x = 0.00
        self._kd_x = 0.5

        self._kp_y = 10.5
        self._ki_y = 0.00
        self._kd_y = 2.5

        self._takip_deadband = 3.0
        self._merkez_filtresi = 0.25

        # DİKEY EKSEN SALINIM ÖNLEYİCİ
        self._takip_deadband_y = 5.0
        self._takip_deadband_y_cikis = 8.0
        self._min_hiz_rampa = 25.0
        self._dikey_uyanik = False

        self._min_takip_hizi = 70.0
        self._maks_takip_hizi = 8000.0
        self._maks_ivme = 7000.0

        # DİKEY EKSEN YUMUŞATMA
        self._tilt_filtre = 0.50
        self._tilt_y = None

        # LEAD POINT / FEEDFORWARD
        self._lead_kayma_orani = 140.0
        self._lead_esik = 8.5
        self._lead_kazanc = 0.50
        self._lead_filtre = 0.25
        self._k_fov = 52.0

        # BOYUTA GÖRE KAZANÇ
        self._kutu_esik_1 = 100.0
        self._kutu_esik_2 = 200.0
        self._kutu_esik_3 = 300.0

        self._kazanc_1 = 0.05
        self._kazanc_2 = 0.03
        self._kazanc_3 = 0.02
        self._kazanc_4 = 0.01

        # ATEŞ TOLERANSI
        self._ates_tolerans_oran = 15.0
        self._ates_tolerans_min_px = 10.0

        # AŞAMA 3 ROI / DOST KORUMA
        self._roi_yatay_pay = 0.20
        self._roi_dikey_pay = 1.50

        self._dost_roi_yatay_pay = 0.25
        self._dost_roi_dikey_pay = 1.80

        self._reset_suresi = 0.25

        # KALMAN FİLTRE
        self._kalman_state = None
        self._kalman_P = None
        self._kalman_q = 0.05
        self._kalman_r = 2.50

        # DAHİLİ DEĞİŞKENLER
        self._filtrelenmis_merkez_x = None
        self._filtrelenmis_merkez_y = None

        self._hedef_vx_gercek = 0.0
        self._dinamik_offset_x = 0.0

        self._integral_x = 0.0
        self._integral_y = 0.0

        self._onceki_hata_x = 0.0
        self._onceki_hata_y = 0.0
        self._onceki_hiz_x = 0.0
        self._onceki_hiz_y = 0.0

        self._kilit_baslangic_zamani = None
        self._reset_baslangic_zamani = None

        self._son_imha_zamani = 0.0
        self._toplam_imha = 0

        self._hedef_balon = None
        self._dusman_hedef = None

        self._pid_yukle()

    # =========================================================
    # PARAMETRE YÖNETİMİ
    # =========================================================
    def parametreleri_ayarla(self, **kwargs):
        try:
            with self._pid_lock:
                if "kp_x" in kwargs:
                    self._kp_x = float(kwargs["kp_x"])
                if "ki_x" in kwargs:
                    self._ki_x = float(kwargs["ki_x"])
                if "kd_x" in kwargs:
                    self._kd_x = float(kwargs["kd_x"])

                if "kp_y" in kwargs:
                    self._kp_y = float(kwargs["kp_y"])
                if "ki_y" in kwargs:
                    self._ki_y = float(kwargs["ki_y"])
                if "kd_y" in kwargs:
                    self._kd_y = float(kwargs["kd_y"])

            if "tarama_min_derece" in kwargs:
                self._tarama_min_derece = max(0.0, min(270.0, float(kwargs["tarama_min_derece"])))
            if "tarama_max_derece" in kwargs:
                self._tarama_max_derece = max(0.0, min(270.0, float(kwargs["tarama_max_derece"])))

            if self._tarama_max_derece <= self._tarama_min_derece:
                self._tarama_max_derece = min(270.0, self._tarama_min_derece + 5.0)

            if "donus_min_derece" in kwargs:
                self._donus_min_derece = max(0.0, min(270.0, float(kwargs["donus_min_derece"])))
            if "donus_max_derece" in kwargs:
                self._donus_max_derece = max(0.0, min(270.0, float(kwargs["donus_max_derece"])))

            if self._donus_max_derece <= self._donus_min_derece:
                self._donus_max_derece = min(270.0, self._donus_min_derece + 5.0)

            if "tilt_filtre" in kwargs:
                self._tilt_filtre = max(0.05, min(1.0, float(kwargs["tilt_filtre"])))

            if "takip_deadband_y" in kwargs:
                self._takip_deadband_y = max(1.0, min(30.0, float(kwargs["takip_deadband_y"])))

            if "takip_deadband_y_cikis" in kwargs:
                self._takip_deadband_y_cikis = max(
                    self._takip_deadband_y + 1.0,
                    min(40.0, float(kwargs["takip_deadband_y_cikis"]))
                )

            if "min_hiz_rampa" in kwargs:
                self._min_hiz_rampa = max(5.0, min(100.0, float(kwargs["min_hiz_rampa"])))

            if "tarama_hizi" in kwargs:
                self._tarama_hizi_deg = float(kwargs["tarama_hizi"])

            if "ates_onay_suresi" in kwargs:
                self._ates_onay_suresi = max(0.15, min(3.0, float(kwargs["ates_onay_suresi"])))

            if "servo_baslangic" in kwargs:
                self._servo_baslangic = int(kwargs["servo_baslangic"])
            if "servo_bitis" in kwargs:
                self._servo_bitis = int(kwargs["servo_bitis"])
            if "servo_bekleme" in kwargs:
                self._servo_bekleme = int(kwargs["servo_bekleme"])

            if "lead_kayma_orani" in kwargs:
                self._lead_kayma_orani = float(kwargs["lead_kayma_orani"])
            if "lead_esik" in kwargs:
                self._lead_esik = float(kwargs["lead_esik"])
            if "lead_kazanc" in kwargs:
                self._lead_kazanc = float(kwargs["lead_kazanc"])
            if "lead_filtre" in kwargs:
                self._lead_filtre = float(kwargs["lead_filtre"])

            if "kutu_esik_1" in kwargs:
                self._kutu_esik_1 = float(kwargs["kutu_esik_1"])
            if "kutu_esik_2" in kwargs:
                self._kutu_esik_2 = float(kwargs["kutu_esik_2"])
            if "kutu_esik_3" in kwargs:
                self._kutu_esik_3 = float(kwargs["kutu_esik_3"])

            if "kazanc_1" in kwargs:
                self._kazanc_1 = float(kwargs["kazanc_1"])
            if "kazanc_2" in kwargs:
                self._kazanc_2 = float(kwargs["kazanc_2"])
            if "kazanc_3" in kwargs:
                self._kazanc_3 = float(kwargs["kazanc_3"])
            if "kazanc_4" in kwargs:
                self._kazanc_4 = float(kwargs["kazanc_4"])

            if "ates_tolerans_oran" in kwargs:
                self._ates_tolerans_oran = float(kwargs["ates_tolerans_oran"])
            if "ates_tolerans_min_px" in kwargs:
                self._ates_tolerans_min_px = float(kwargs["ates_tolerans_min_px"])

            if "roi_yatay_pay" in kwargs:
                self._roi_yatay_pay = max(0.0, min(1.0, float(kwargs["roi_yatay_pay"])))
            if "roi_dikey_pay" in kwargs:
                self._roi_dikey_pay = max(0.1, min(5.0, float(kwargs["roi_dikey_pay"])))

            if "dost_roi_yatay_pay" in kwargs:
                self._dost_roi_yatay_pay = max(0.0, min(1.0, float(kwargs["dost_roi_yatay_pay"])))
            if "dost_roi_dikey_pay" in kwargs:
                self._dost_roi_dikey_pay = max(0.1, min(5.0, float(kwargs["dost_roi_dikey_pay"])))

            if "reset_suresi" in kwargs:
                self._reset_suresi = max(0.05, min(2.0, float(kwargs["reset_suresi"])))

            if "kalman_q" in kwargs:
                self._kalman_q = max(0.001, min(10.0, float(kwargs["kalman_q"])))
            if "kalman_r" in kwargs:
                self._kalman_r = max(0.001, min(50.0, float(kwargs["kalman_r"])))

            return True

        except Exception as e:
            print(f"[Asama3] Parametre Ayarlama Hatası: {e}")
            return False

    def parametreleri_al(self):
        with self._pid_lock:
            return {
                "kp_x": self._kp_x,
                "ki_x": self._ki_x,
                "kd_x": self._kd_x,
                "kp_y": self._kp_y,
                "ki_y": self._ki_y,
                "kd_y": self._kd_y,
                "tarama_min_derece": self._tarama_min_derece,
                "tarama_max_derece": self._tarama_max_derece,
                "donus_min_derece": self._donus_min_derece,
                "donus_max_derece": self._donus_max_derece,
                "tilt_filtre": self._tilt_filtre,
                "takip_deadband_y": self._takip_deadband_y,
                "takip_deadband_y_cikis": self._takip_deadband_y_cikis,
                "min_hiz_rampa": self._min_hiz_rampa,
                "tarama_hizi": self._tarama_hizi_deg,
                "ates_onay_suresi": self._ates_onay_suresi,
                "servo_baslangic": self._servo_baslangic,
                "servo_bitis": self._servo_bitis,
                "servo_bekleme": self._servo_bekleme,
                "lead_kayma_orani": self._lead_kayma_orani,
                "lead_esik": self._lead_esik,
                "lead_kazanc": self._lead_kazanc,
                "lead_filtre": self._lead_filtre,
                "kutu_esik_1": self._kutu_esik_1,
                "kutu_esik_2": self._kutu_esik_2,
                "kutu_esik_3": self._kutu_esik_3,
                "kazanc_1": self._kazanc_1,
                "kazanc_2": self._kazanc_2,
                "kazanc_3": self._kazanc_3,
                "kazanc_4": self._kazanc_4,
                "ates_tolerans_oran": self._ates_tolerans_oran,
                "ates_tolerans_min_px": self._ates_tolerans_min_px,
                "roi_yatay_pay": self._roi_yatay_pay,
                "roi_dikey_pay": self._roi_dikey_pay,
                "dost_roi_yatay_pay": self._dost_roi_yatay_pay,
                "dost_roi_dikey_pay": self._dost_roi_dikey_pay,
                "reset_suresi": self._reset_suresi,
                "kalman_q": self._kalman_q,
                "kalman_r": self._kalman_r,
            }

    def kalici_kaydet(self):
        try:
            veri = self.parametreleri_al()
            with open(self._pid_dosya, "w", encoding="utf-8") as f:
                json.dump(veri, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[Asama3] Dosya Kaydetme Hatası: {e}")
            return False

    def _pid_yukle(self):
        try:
            if os.path.isfile(self._pid_dosya):
                with open(self._pid_dosya, "r", encoding="utf-8") as f:
                    veri = json.load(f)
                self.parametreleri_ayarla(**veri)
        except Exception:
            pass

    def kazanc_sec(self, kutu_genislik):
        if kutu_genislik <= self._kutu_esik_1:
            return self._kazanc_1
        elif kutu_genislik <= self._kutu_esik_2:
            return self._kazanc_2
        elif kutu_genislik <= self._kutu_esik_3:
            return self._kazanc_3
        else:
            return self._kazanc_4

    # =========================================================
    # KALMAN FİLTRE - 4D STATE [x, y, vx, vy]
    # =========================================================
    def _kalman_sifirla(self):
        self._kalman_state = None
        self._kalman_P = None

    def _kalman_baslat(self, cx, cy):
        self._kalman_state = np.array([cx, cy, 0.0, 0.0], dtype=float)
        self._kalman_P = np.eye(4) * 500.0

    def _kalman_guncelle(self, cx, cy, dt):
        if self._kalman_state is None:
            self._kalman_baslat(cx, cy)
            return cx, cy, 0.0, 0.0

        dt = max(0.001, float(dt))

        F = np.eye(4)
        F[0, 2] = dt
        F[1, 3] = dt

        x = self._kalman_state
        P = self._kalman_P

        x = F @ x
        P = F @ P @ F.T + np.eye(4) * self._kalman_q

        H = np.array(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0]
            ],
            dtype=float
        )

        R = np.eye(2) * self._kalman_r
        z = np.array([cx, cy], dtype=float)

        y = z - H @ x
        S = H @ P @ H.T + R
        S += np.eye(2) * 1e-6

        K = P @ H.T @ np.linalg.inv(S)

        x = x + K @ y
        P = (np.eye(4) - K @ H) @ P

        self._kalman_state = x
        self._kalman_P = P

        return float(x[0]), float(x[1]), float(x[2]), float(x[3])

    # =========================================================
    # YARDIMCI FONKSİYONLAR
    # =========================================================
    def _merkez_al(self, kutu):
        x1, y1, x2, y2 = kutu
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def _balon_mu(self, tespit):
        sinif = str(tespit.get("sinif", "")).lower()
        durum = str(tespit.get("durum", "")).lower()
        model = str(tespit.get("model", "")).lower()

        if model == "ikinci":
            return True
        if durum == "balon":
            return True
        if "balon" in sinif or "balloon" in sinif:
            return True

        return False

    def _dost_mu(self, tespit):
        durum = str(tespit.get("durum", "")).lower()
        model = str(tespit.get("model", "")).lower()
        return model == "birincil" and durum == "dost"

    def _dusman_mu(self, tespit):
        durum = str(tespit.get("durum", "")).lower()
        model = str(tespit.get("model", "")).lower()
        return model == "birincil" and durum == "düşman"

    def _nokta_roi_icinde(self, cx, cy, roi):
        if roi is None:
            return False

        x1, y1, x2, y2 = roi
        return x1 <= cx <= x2 and y1 <= cy <= y2

    def _roi_olustur(self, kutu, yatay_pay, dikey_pay, genislik=None, yukseklik=None):
        try:
            x1, y1, x2, y2 = kutu
            w = x2 - x1
            h = y2 - y1

            if w <= 0 or h <= 0:
                return None

            roi_x1 = x1 - w * yatay_pay
            roi_x2 = x2 + w * yatay_pay

            roi_y1 = y2
            roi_y2 = y2 + h * dikey_pay

            if genislik is not None:
                roi_x1 = max(0.0, min(float(genislik), float(roi_x1)))
                roi_x2 = max(0.0, min(float(genislik), float(roi_x2)))

            if yukseklik is not None:
                roi_y1 = max(0.0, min(float(yukseklik), float(roi_y1)))
                roi_y2 = max(0.0, min(float(yukseklik), float(roi_y2)))

            if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
                return None

            return [roi_x1, roi_y1, roi_x2, roi_y2]

        except Exception:
            return None

    def _integral_sifirla(self):
        self._integral_x = 0.0
        self._integral_y = 0.0

        self._onceki_hata_x = 0.0
        self._onceki_hata_y = 0.0
        self._onceki_hiz_x = 0.0
        self._onceki_hiz_y = 0.0

        self._hedef_vx_gercek = 0.0
        self._dinamik_offset_x = 0.0

        self._filtrelenmis_merkez_x = None
        self._filtrelenmis_merkez_y = None

        self._tilt_y = None
        self._dikey_uyanik = False

        self._kilit_baslangic_zamani = None
        self._reset_baslangic_zamani = None

        self._kalman_sifirla()

    # ================= MUTLAK PAN TELEMETRİSİ =================
    def pan_acisi_bildir(self, aci):
        try:
            self._mutlak_pan_aci = float(aci)
            self._son_pan_telemetri_zamani = time.time()
        except Exception:
            pass

    def _pan_telemetri_taze(self, max_age=1.0):
        if self._mutlak_pan_aci is None:
            return False
        return (time.time() - self._son_pan_telemetri_zamani) <= max_age

    # ================= YASAK BÖLGE KONTROLÜ =================
    def _hedef_yasak_bolgede(self, balon, genislik):
        if not self._pan_telemetri_taze():
            return True

        try:
            x1, y1, x2, y2 = balon["kutu"]
            cx = (x1 + x2) / 2.0
            px_hata = cx - genislik / 2.0
            tahmini_pan = self._mutlak_pan_aci + (px_hata / self._k_fov)

            marj = 2.0

            return (tahmini_pan < self._donus_min_derece + marj) or \
                   (tahmini_pan > self._donus_max_derece - marj)
        except Exception:
            return True

    # =========================================================
    # HİYERARŞİK HEDEF SEÇİMİ
    # =========================================================
    def _hedef_balon_bul(self, tespitler, genislik, yukseklik):
        if not tespitler:
            return None, None

        balonlar = [t for t in tespitler if self._balon_mu(t)]
        dusmanlar = [t for t in tespitler if self._dusman_mu(t)]

        if not balonlar or not dusmanlar:
            return None, None

        dostlar = [t for t in tespitler if self._dost_mu(t)]

        dost_rois = []
        for d in dostlar:
            kutu = d.get("kutu")
            if kutu is None:
                continue

            roi = self._roi_olustur(
                kutu,
                self._dost_roi_yatay_pay,
                self._dost_roi_dikey_pay,
                genislik,
                yukseklik
            )

            if roi is not None:
                dost_rois.append((kutu, roi))

        geceri_balonlar = []

        for b in balonlar:
            kutu = b.get("kutu")
            if kutu is None:
                continue

            cx, cy = self._merkez_al(kutu)

            if cx < 0 or cy < 0 or cx > genislik or cy > yukseklik:
                continue

            dost_altinda = False

            for dost_kutu, dost_roi in dost_rois:
                if self._nokta_roi_icinde(cx, cy, dost_kutu):
                    dost_altinda = True
                    break

                if self._nokta_roi_icinde(cx, cy, dost_roi):
                    dost_altinda = True
                    break

            if not dost_altinda:
                geceri_balonlar.append((b, cx, cy))

        if not geceri_balonlar:
            return None, None

        ekran_merkez_x = genislik / 2.0
        ekran_merkez_y = yukseklik / 2.0

        en_iyi_hedef = None
        en_iyi_skor = float("inf")

        for dusman in dusmanlar:
            dusman_kutu = dusman.get("kutu")
            if dusman_kutu is None:
                continue

            roi = self._roi_olustur(
                dusman_kutu,
                self._roi_yatay_pay,
                self._roi_dikey_pay,
                genislik,
                yukseklik
            )

            if roi is None:
                continue

            roi_cx = (roi[0] + roi[2]) / 2.0
            roi_cy = (roi[1] + roi[3]) / 2.0

            dusman_cx, dusman_cy = self._merkez_al(dusman_kutu)

            dusman_ekran_mesafesi = np.hypot(
                dusman_cx - ekran_merkez_x,
                dusman_cy - ekran_merkez_y
            )

            for balon, cx, cy in geceri_balonlar:
                if not self._nokta_roi_icinde(cx, cy, roi):
                    continue

                balon_roi_mesafesi = np.hypot(cx - roi_cx, cy - roi_cy)
                skor = dusman_ekran_mesafesi + 0.25 * balon_roi_mesafesi

                if skor < en_iyi_skor:
                    en_iyi_skor = skor
                    en_iyi_hedef = (balon, dusman)

        if en_iyi_hedef is None:
            return None, None

        return en_iyi_hedef[0], en_iyi_hedef[1]

    # =========================================================
    # SEARCH TARAMA HAREKETİ
    # =========================================================
    def _tarama_hareketi(self, dt):
        eff_min = max(self._tarama_min_derece, self._donus_min_derece)
        eff_max = min(self._tarama_max_derece, self._donus_max_derece)

        if eff_max <= eff_min:
            eff_min, eff_max = self._donus_min_derece, self._donus_max_derece

        # DÜZELTME: Telemetri yokken kafadan açı uydurma
        if not self._pan_telemetri_taze():
            self._durum_bilgisi = "⏳ POZ telemetrisi bekleniyor (tarama durdu)"

            simdi = time.time()
            if simdi - self._telemetri_uyari_son_gonderim >= 0.25:
                try:
                    self.pico.komut_gonder("Y 0")
                    self.pico.komut_gonder("X 0")
                except Exception:
                    pass
                self._telemetri_uyari_son_gonderim = simdi

            return

        adim_deg = self._tarama_hizi_deg * dt * self._tarama_yon
        self._mutlak_pan_aci += adim_deg

        if self._mutlak_pan_aci >= eff_max:
            self._mutlak_pan_aci = eff_max
            self._tarama_yon = -1
        elif self._mutlak_pan_aci <= eff_min:
            self._mutlak_pan_aci = eff_min
            self._tarama_yon = 1

        tarama_motor_hizi = self._tarama_hizi_deg * self.PAN_ADIM_DERECESI * self._tarama_yon

        self.pico.komut_gonder(f"Y {tarama_motor_hizi:.1f}")
        self.pico.komut_gonder("X 0")

    # =========================================================
    # PID + KALMAN + FEEDFORWARD TAKİP
    # =========================================================
    def _pid_takip_et(self, balon, genislik, yukseklik, dt):
        x1, y1, x2, y2 = balon["kutu"]

        kutu_genislik = x2 - x1
        kutu_yukseklik = y2 - y1

        if kutu_genislik <= 2 or kutu_yukseklik <= 2:
            return False

        ham_merkez_x, ham_merkez_y = self._merkez_al(balon["kutu"])

        kal_x, kal_y, kal_vx, kal_vy = self._kalman_guncelle(
            ham_merkez_x,
            ham_merkez_y,
            dt
        )

        self._filtrelenmis_merkez_x = kal_x

        if self._tilt_y is None:
            self._tilt_y = kal_y
        else:
            alpha_t = max(0.05, min(1.0, self._tilt_filtre))
            self._tilt_y = alpha_t * kal_y + (1.0 - alpha_t) * self._tilt_y

        self._filtrelenmis_merkez_y = self._tilt_y

        ekran_merkez_x = genislik / 2.0
        ekran_merkez_y = yukseklik / 2.0

        taret_deg_s = self._onceki_hiz_x / self.PAN_ADIM_DERECESI
        v_taret_px = taret_deg_s * self._k_fov

        vx_gercek = kal_vx + v_taret_px

        alpha_v = max(0.05, min(1.0, self._lead_filtre))
        self._hedef_vx_gercek = (
            alpha_v * vx_gercek +
            (1.0 - alpha_v) * self._hedef_vx_gercek
        )

        hiz_abs = abs(self._hedef_vx_gercek)
        esik = self._lead_esik

        if hiz_abs > esik:
            yon = np.sign(self._hedef_vx_gercek)
            maks_pay = (kutu_genislik / 2.0) * (self._lead_kayma_orani / 100.0)
            kayma_kazanci = self.kazanc_sec(kutu_genislik)
            kayma_miktari = min((hiz_abs - esik) * kayma_kazanci * 0.1, 1.0)
            hedef_offset = yon * maks_pay * kayma_miktari
        else:
            hedef_offset = 0.0

        alpha_lead = max(0.05, min(1.0, self._lead_filtre))
        self._dinamik_offset_x = (
            alpha_lead * hedef_offset +
            (1.0 - alpha_lead) * self._dinamik_offset_x
        )

        if abs(self._dinamik_offset_x) < 0.5:
            self._dinamik_offset_x = 0.0

        hedef_nokta_x = kal_x + self._dinamik_offset_x
        hedef_nokta_y = self._tilt_y

        hata_x = hedef_nokta_x - ekran_merkez_x
        hata_y = hedef_nokta_y - ekran_merkez_y

        if abs(hata_x) < self._takip_deadband:
            hata_x = 0.0

        if not self._dikey_uyanik:
            if abs(hata_y) >= self._takip_deadband_y_cikis:
                self._dikey_uyanik = True
        else:
            if abs(hata_y) <= self._takip_deadband_y:
                self._dikey_uyanik = False

        if not self._dikey_uyanik:
            hata_y = 0.0

        with self._pid_lock:
            kp_x = self._kp_x
            ki_x = self._ki_x
            kd_x = self._kd_x

            kp_y = self._kp_y
            ki_y = self._ki_y
            kd_y = self._kd_y

        dt = max(0.001, dt)

        p_x = hata_x * kp_x

        self._integral_x += hata_x * dt
        self._integral_x = max(-3000.0, min(3000.0, self._integral_x))
        i_x = self._integral_x * ki_x

        d_x = ((hata_x - self._onceki_hata_x) / dt) * kd_x
        self._onceki_hata_x = hata_x

        hiz_x = p_x + i_x + d_x

        if abs(hata_x) >= self._takip_deadband and abs(hiz_x) < self._min_takip_hizi:
            hiz_x = self._min_takip_hizi * np.sign(hata_x)

        p_y = hata_y * kp_y

        self._integral_y += hata_y * dt
        self._integral_y = max(-1500.0, min(1500.0, self._integral_y))
        i_y = self._integral_y * ki_y

        d_y = ((hata_y - self._onceki_hata_y) / dt) * kd_y
        self._onceki_hata_y = hata_y

        hiz_y = p_y + i_y + d_y

        if abs(hata_y) > 0.0:
            min_hiz_y = min(self._min_takip_hizi, abs(hata_y) * self._min_hiz_rampa)
            if abs(hiz_y) < min_hiz_y:
                hiz_y = min_hiz_y * np.sign(hata_y)

        hiz_x = max(-self._maks_takip_hizi, min(self._maks_takip_hizi, hiz_x))
        hiz_y = max(-self._maks_takip_hizi, min(self._maks_takip_hizi, hiz_y))

        max_delta = self._maks_ivme * dt

        delta_x = hiz_x - self._onceki_hiz_x
        if abs(delta_x) > max_delta:
            hiz_x = self._onceki_hiz_x + (max_delta if delta_x > 0 else -max_delta)
        self._onceki_hiz_x = hiz_x

        delta_y = hiz_y - self._onceki_hiz_y
        if abs(delta_y) > max_delta:
            hiz_y = self._onceki_hiz_y + (max_delta if delta_y > 0 else -max_delta)
        self._onceki_hiz_y = hiz_y

        self.pico.komut_gonder(f"X {hiz_y:.1f}")
        self.pico.komut_gonder(f"Y {hiz_x:.1f}")

        tol_x = max(
            self._ates_tolerans_min_px,
            kutu_genislik * self._ates_tolerans_oran / 100.0
        )
        tol_y = max(
            self._ates_tolerans_min_px,
            kutu_yukseklik * self._ates_tolerans_oran / 100.0
        )

        merkez_hata_x = kal_x - ekran_merkez_x
        merkez_hata_y = self._tilt_y - ekran_merkez_y

        tolerans_icinde = (
            abs(merkez_hata_x) <= tol_x and
            abs(merkez_hata_y) <= tol_y
        )

        return tolerans_icinde

    # =========================================================
    # İMHA / SERVO
    # =========================================================
    def _balon_patlat(self):
        if not self.pico.bagli_mi():
            return False

        simdi = time.time()
        if simdi - self._son_imha_zamani < 0.6:
            return False

        def _tetikle():
            try:
                self.pico.servo_kontrol(
                    self._servo_baslangic,
                    self._servo_bitis,
                    self._servo_bekleme
                )
            except Exception as e:
                print(f"[Asama3] Servo ateşleme hatası: {e}")

        threading.Thread(target=_tetikle, daemon=True).start()

        self._toplam_imha += 1
        self._son_imha_zamani = simdi

        return True

    def test_atesi(self):
        if not self.pico.bagli_mi():
            return False

        def _tetikle():
            try:
                self.pico.servo_kontrol(
                    self._servo_baslangic,
                    self._servo_bitis,
                    self._servo_bekleme
                )
            except Exception as e:
                print(f"[Asama3] Test ateşi hatası: {e}")

        threading.Thread(target=_tetikle, daemon=True).start()
        return True

    # =========================================================
    # ANA FSM DÖNGÜSÜ
    # =========================================================
    def _dongu(self):
        son_zaman = time.time()

        while self._aktif.is_set():
            simdi = time.time()
            dt = max(0.001, simdi - son_zaman)
            son_zaman = simdi

            if not self.pico.bagli_mi():
                time.sleep(0.05)
                continue

            kare, tespitler, _, _, _ = self.goruntu.verileri_al()
            if kare is None:
                time.sleep(0.01)
                continue

            yukseklik, genislik = kare.shape[:2]

            hedef_balon, dusman_hedef = self._hedef_balon_bul(
                tespitler,
                genislik,
                yukseklik
            )

            # Yasak bölge / telemetri belirsizliği: hedefi seçme
            if hedef_balon is not None and self._hedef_yasak_bolgede(hedef_balon, genislik):
                hedef_balon = None
                dusman_hedef = None

            self._hedef_balon = hedef_balon
            self._dusman_hedef = dusman_hedef

            with self._durum_lock:
                mevcut_durum = self._durum

            # ================= SEARCH =================
            if mevcut_durum == "SEARCH":
                self._reset_baslangic_zamani = None

                if hedef_balon is not None:
                    self._integral_sifirla()

                    with self._durum_lock:
                        self._durum = "ENEMY_BALLOON_TRACK"

                    self._durum_bilgisi = "Düşman balonu tespit edildi"
                else:
                    self._tarama_hareketi(dt)
                    self._durum_bilgisi = "Düşman balonu aranıyor..."

            # ================= ENEMY_BALLOON_TRACK =================
            elif mevcut_durum == "ENEMY_BALLOON_TRACK":
                if hedef_balon is not None:
                    self._durum_bilgisi = "Düşman balonu takip ediliyor"

                    tolerans_icinde = self._pid_takip_et(
                        hedef_balon,
                        genislik,
                        yukseklik,
                        dt
                    )

                    if tolerans_icinde:
                        self._kilit_baslangic_zamani = simdi

                        with self._durum_lock:
                            self._durum = "LOCK_VERIFY"

                        self._durum_bilgisi = "Kilit doğrulamasına geçildi"
                else:
                    self._integral_sifirla()

                    with self._durum_lock:
                        self._durum = "SEARCH"

                    self._durum_bilgisi = "Hedef kaybedildi, arama yapılıyor"

            # ================= LOCK_VERIFY =================
            elif mevcut_durum == "LOCK_VERIFY":
                if hedef_balon is not None:
                    tolerans_icinde = self._pid_takip_et(
                        hedef_balon,
                        genislik,
                        yukseklik,
                        dt
                    )

                    if tolerans_icinde:
                        if self._kilit_baslangic_zamani is None:
                            self._kilit_baslangic_zamani = simdi

                        gecen_kilit = simdi - self._kilit_baslangic_zamani
                        gereken_kilit = max(0.15, self._ates_onay_suresi)

                        if gecen_kilit >= gereken_kilit:
                            self._balon_patlat()
                            self._kilit_baslangic_zamani = None

                            with self._durum_lock:
                                self._durum = "ENGAGE"

                            self._durum_bilgisi = "Ateşlendi / düşman balonu imha ediliyor"
                        else:
                            yuzde = int((gecen_kilit / max(0.01, gereken_kilit)) * 100)
                            self._durum_bilgisi = f"Kilitleniyor... %{min(100, yuzde)}"
                    else:
                        self._kilit_baslangic_zamani = None

                        with self._durum_lock:
                            self._durum = "ENEMY_BALLOON_TRACK"

                        self._durum_bilgisi = "Kilit bozuldu, takip devam ediyor"
                else:
                    self._kilit_baslangic_zamani = None
                    self._integral_sifirla()

                    with self._durum_lock:
                        self._durum = "SEARCH"

                    self._durum_bilgisi = "Kilit doğrulamasında hedef kaybedildi"

            # ================= ENGAGE =================
            elif mevcut_durum == "ENGAGE":
                self._durum_bilgisi = "Balon imha edildi / takip sürdürülüyor"

                if hedef_balon is not None:
                    self._pid_takip_et(
                        hedef_balon,
                        genislik,
                        yukseklik,
                        dt
                    )

                if simdi - self._son_imha_zamani >= 0.4:
                    self._reset_baslangic_zamani = simdi

                    with self._durum_lock:
                        self._durum = "RESET"

            # ================= RESET =================
            elif mevcut_durum == "RESET":
                self._durum_bilgisi = "Sistem sıfırlanıyor..."

                if self._reset_baslangic_zamani is None:
                    self._reset_baslangic_zamani = simdi

                    try:
                        self.pico.komut_gonder("X 0")
                        self.pico.komut_gonder("Y 0")
                    except Exception:
                        pass

                if simdi - self._reset_baslangic_zamani >= self._reset_suresi:
                    self._integral_sifirla()

                    with self._durum_lock:
                        self._durum = "SEARCH"

                    self._durum_bilgisi = "Yeni hedef aranıyor"

            time.sleep(0.015)

    # =========================================================
    # KONTROL
    # =========================================================
    def baslat(self):
        self._aktif.set()

        with self._durum_lock:
            self._durum = "SEARCH"

        self._durum_bilgisi = "Aşama 3 başlatıldı"

        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._dongu, daemon=True)
            self._thread.start()

    def dur(self):
        self._aktif.clear()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)

        self._thread = None

        try:
            self.pico.komut_gonder("X 0")
            self.pico.komut_gonder("Y 0")
        except Exception:
            pass

        self._integral_sifirla()

        self._hedef_balon = None
        self._dusman_hedef = None

        with self._durum_lock:
            self._durum = "SEARCH"

        self._durum_bilgisi = "Beklemede"

        # DÜZELTME: tekrar başlatınca eski/kafadan açıyla başlamasın
        self._mutlak_pan_aci = None
        self._son_pan_telemetri_zamani = 0.0
        self._telemetri_uyari_son_gonderim = 0.0

    def aktif_mi(self):
        return self._aktif.is_set()

    def istatistikleri_al(self):
        hedef_kutu = None

        if self._hedef_balon is not None:
            try:
                hedef_kutu = self._hedef_balon["kutu"]
            except Exception:
                hedef_kutu = None

        return {
            "toplam_imha": self._toplam_imha,
            "durum_bilgisi": self._durum_bilgisi,
            "aktif": self.aktif_mi(),
            "durum": self._durum,
            "hedef_balon_kutu": hedef_kutu,
            "dusman_hedef_kutu": self._dusman_hedef["kutu"] if self._dusman_hedef else None,
        }
