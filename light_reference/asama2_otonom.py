"""
asama2_otonom.py
Aşama 2: Otonom Parkur Taraması ve Balon İmhası Modülü

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


class Asama2Otonom:
    PAN_ADIM_DERECESI = (200 * 8 * 30) / 360.0
    TILT_ADIM_DERECESI = (200 * 8 * 20) / 360.0

    def __init__(self, goruntu_motoru, pico_baglanti, pid_dosya_yolu="asama2_pid_ayarlar.json"):
        self.goruntu = goruntu_motoru
        self.pico = pico_baglanti
        self._pid_dosya = pid_dosya_yolu

        self._aktif = threading.Event()
        self._thread = None
        self._durum = "SEARCH"
        self._durum_lock = threading.Lock()

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

        # Ateş Onay Süresi
        self._ates_onay_suresi = 0.35

        # Servo Parametreleri
        self._servo_baslangic = 0
        self._servo_bitis = 120
        self._servo_bekleme = 100

        # PID Parametreleri
        self._pid_lock = threading.Lock()
        self._kp_x = 11.0
        self._ki_x = 0.00
        self._kd_x = 0.8
        self._kp_y = 18.0
        self._ki_y = 0.00
        self._kd_y = 4.0

        self._takip_deadband = 3.0
        self._merkez_filtresi = 0.25

        # DİKEY EKSEN SALINIM ÖNLEYİCİ
        self._takip_deadband_y = 5.0
        self._takip_deadband_y_cikis = 8.0
        self._min_hiz_rampa = 25.0
        self._dikey_uyanik = False

        self._min_takip_hizi = 120.0
        self._maks_takip_hizi = 8000.0
        self._maks_ivme = 7000.0

        # Hibrit Lead Point Parametreleri
        self._lead_kayma_orani = 140.0
        self._lead_esik = 8.5
        self._lead_kazanc = 0.50
        self._lead_filtre = 0.25
        self._k_fov = 52.0

        # Boyuta göre kademeli kayma kazancı
        self._kutu_esik_1 = 100.0
        self._kutu_esik_2 = 200.0
        self._kutu_esik_3 = 300.0
        self._kazanc_1 = 0.05
        self._kazanc_2 = 0.03
        self._kazanc_3 = 0.02
        self._kazanc_4 = 0.01

        # Ateş toleransı
        self._ates_tolerans_oran = 15.0
        self._ates_tolerans_min_px = 10.0

        # Dahili Değişkenler
        self._filtrelenmis_merkez_x = None
        self._filtrelenmis_merkez_y = None
        self._onceki_hedef_x = None
        self._onceki_hedef_y = None
        self._hedef_vx_gercek = 0.0
        self._dinamik_offset_x = 0.0
        self._kilit_baslangic_zamani = None

        self._integral_x = 0.0
        self._integral_y = 0.0
        self._onceki_hata_x = 0.0
        self._onceki_hata_y = 0.0
        self._onceki_hiz_x = 0.0
        self._onceki_hiz_y = 0.0

        self._son_imha_zamani = 0.0
        self._toplam_imha = 0

        self._secili_balon = None
        self._durum_bilgisi = "Beklemede"

        self._pid_yukle()

    # ================= PARAMETRE =================
    def parametreleri_ayarla(self, **kwargs):
        try:
            with self._pid_lock:
                if "kp_x" in kwargs: self._kp_x = float(kwargs["kp_x"])
                if "ki_x" in kwargs: self._ki_x = float(kwargs["ki_x"])
                if "kd_x" in kwargs: self._kd_x = float(kwargs["kd_x"])
                if "kp_y" in kwargs: self._kp_y = float(kwargs["kp_y"])
                if "ki_y" in kwargs: self._ki_y = float(kwargs["ki_y"])
                if "kd_y" in kwargs: self._kd_y = float(kwargs["kd_y"])

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

            if "takip_deadband_y" in kwargs:
                self._takip_deadband_y = max(1.0, min(30.0, float(kwargs["takip_deadband_y"])))

            if "takip_deadband_y_cikis" in kwargs:
                self._takip_deadband_y_cikis = max(
                    self._takip_deadband_y + 1.0,
                    min(40.0, float(kwargs["takip_deadband_y_cikis"]))
                )

            if "min_hiz_rampa" in kwargs:
                self._min_hiz_rampa = max(5.0, min(100.0, float(kwargs["min_hiz_rampa"])))

            if "tarama_hizi" in kwargs: self._tarama_hizi_deg = float(kwargs["tarama_hizi"])
            if "ates_onay_suresi" in kwargs: self._ates_onay_suresi = float(kwargs["ates_onay_suresi"])

            if "servo_baslangic" in kwargs: self._servo_baslangic = int(kwargs["servo_baslangic"])
            if "servo_bitis" in kwargs: self._servo_bitis = int(kwargs["servo_bitis"])
            if "servo_bekleme" in kwargs: self._servo_bekleme = int(kwargs["servo_bekleme"])

            if "lead_kayma_orani" in kwargs: self._lead_kayma_orani = float(kwargs["lead_kayma_orani"])
            if "lead_esik" in kwargs: self._lead_esik = float(kwargs["lead_esik"])
            if "lead_kazanc" in kwargs: self._lead_kazanc = float(kwargs["lead_kazanc"])
            if "lead_filtre" in kwargs: self._lead_filtre = float(kwargs["lead_filtre"])

            if "kutu_esik_1" in kwargs: self._kutu_esik_1 = float(kwargs["kutu_esik_1"])
            if "kutu_esik_2" in kwargs: self._kutu_esik_2 = float(kwargs["kutu_esik_2"])
            if "kutu_esik_3" in kwargs: self._kutu_esik_3 = float(kwargs["kutu_esik_3"])

            if "kazanc_1" in kwargs: self._kazanc_1 = float(kwargs["kazanc_1"])
            if "kazanc_2" in kwargs: self._kazanc_2 = float(kwargs["kazanc_2"])
            if "kazanc_3" in kwargs: self._kazanc_3 = float(kwargs["kazanc_3"])
            if "kazanc_4" in kwargs: self._kazanc_4 = float(kwargs["kazanc_4"])

            if "ates_tolerans_oran" in kwargs: self._ates_tolerans_oran = float(kwargs["ates_tolerans_oran"])
            if "ates_tolerans_min_px" in kwargs: self._ates_tolerans_min_px = float(kwargs["ates_tolerans_min_px"])

            return True
        except Exception as e:
            print(f"[Asama2] Parametre Ayarlama Hatası: {e}")
            return False

    def parametreleri_al(self):
        with self._pid_lock:
            return {
                "kp_x": self._kp_x, "ki_x": self._ki_x, "kd_x": self._kd_x,
                "kp_y": self._kp_y, "ki_y": self._ki_y, "kd_y": self._kd_y,
                "tarama_min_derece": self._tarama_min_derece,
                "tarama_max_derece": self._tarama_max_derece,
                "donus_min_derece": self._donus_min_derece,
                "donus_max_derece": self._donus_max_derece,
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
            }

    def kalici_kaydet(self):
        try:
            veri = self.parametreleri_al()
            with open(self._pid_dosya, "w", encoding="utf-8") as f:
                json.dump(veri, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[Asama2] Dosya Kaydetme Hatası: {e}")
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

    def _integral_sifirla(self):
        self._integral_x = 0.0
        self._integral_y = 0.0

        self._onceki_hata_x = 0.0
        self._onceki_hata_y = 0.0
        self._onceki_hiz_x = 0.0
        self._onceki_hiz_y = 0.0

        self._onceki_hedef_x = None
        self._onceki_hedef_y = None

        self._hedef_vx_gercek = 0.0
        self._dinamik_offset_x = 0.0
        self._kilit_baslangic_zamani = None

        self._filtrelenmis_merkez_x = None
        self._filtrelenmis_merkez_y = None
        self._dikey_uyanik = False

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
        """
        Telemetri yoksa/eskiyse güvenli taraf: hedefi seçme.
        """
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

    # ================= YARDIMCI =================
    def _balon_bul(self, tespitler):
        balonlar = []
        for t in tespitler:
            sinif_adi = t.get("sinif", "").lower()
            model_tipi = t.get("model", "")
            if "balon" in sinif_adi or model_tipi == "ikinci":
                balonlar.append(t)
        return balonlar

    def _en_uygun_balon_sec(self, balonlar, genislik, yukseklik):
        if not balonlar:
            return None

        ekran_merkez_x = genislik / 2.0
        ekran_merkez_y = yukseklik / 2.0

        en_yakin = None
        min_mesafe = float('inf')

        for balon in balonlar:
            x1, y1, x2, y2 = balon["kutu"]
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0

            dist = ((cx - ekran_merkez_x) ** 2 + (cy - ekran_merkez_y) ** 2) ** 0.5
            if dist < min_mesafe:
                min_mesafe = dist
                en_yakin = balon

        return en_yakin

    # ================= TARAMA =================
    def _tarama_hareketi(self, dt):
        # Tarama penceresi, dönüş limitlerinin DIŞINA çıkamaz
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

    # ================= TAKİP =================
    def _pid_takip_et(self, balon, genislik, yukseklik, dt):
        x1, y1, x2, y2 = balon["kutu"]

        ham_merkez_x = (x1 + x2) / 2.0
        ham_merkez_y = (y1 + y2) / 2.0

        kutu_genislik = x2 - x1
        kutu_yukseklik = y2 - y1

        ekran_merkez_x = genislik / 2.0
        ekran_merkez_y = yukseklik / 2.0

        if self._filtrelenmis_merkez_x is None:
            self._filtrelenmis_merkez_x = ham_merkez_x
            self._filtrelenmis_merkez_y = ham_merkez_y
        else:
            alpha_pos = max(0.05, min(1.0, self._merkez_filtresi))
            self._filtrelenmis_merkez_x = alpha_pos * ham_merkez_x + (1.0 - alpha_pos) * self._filtrelenmis_merkez_x
            self._filtrelenmis_merkez_y = alpha_pos * ham_merkez_y + (1.0 - alpha_pos) * self._filtrelenmis_merkez_y

        anlik_vx_bagil = 0.0
        if self._onceki_hedef_x is not None and dt > 0.0001:
            anlik_vx_bagil = (self._filtrelenmis_merkez_x - self._onceki_hedef_x) / dt

        self._onceki_hedef_x = self._filtrelenmis_merkez_x
        self._onceki_hedef_y = self._filtrelenmis_merkez_y

        taret_deg_s = self._onceki_hiz_x / self.PAN_ADIM_DERECESI
        v_taret_px = taret_deg_s * self._k_fov
        anlik_vx_gercek = anlik_vx_bagil + v_taret_px

        alpha_v = max(0.05, min(1.0, self._lead_filtre))
        self._hedef_vx_gercek = alpha_v * anlik_vx_gercek + (1.0 - alpha_v) * self._hedef_vx_gercek

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
        self._dinamik_offset_x = alpha_lead * hedef_offset + (1.0 - alpha_lead) * self._dinamik_offset_x

        if abs(self._dinamik_offset_x) < 0.5:
            self._dinamik_offset_x = 0.0

        hedef_nokta_x = self._filtrelenmis_merkez_x + self._dinamik_offset_x
        hedef_nokta_y = self._filtrelenmis_merkez_y

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
            kp_x, ki_x, kd_x = self._kp_x, self._ki_x, self._kd_x
            kp_y, ki_y, kd_y = self._kp_y, self._ki_y, self._kd_y

        p_x = hata_x * kp_x

        self._integral_x += hata_x * dt
        self._integral_x = max(-3000.0, min(3000.0, self._integral_x))
        i_x = self._integral_x * ki_x

        d_x = ((hata_x - self._onceki_hata_x) / dt) * kd_x if dt > 0 else 0.0
        self._onceki_hata_x = hata_x

        hiz_x = p_x + i_x + d_x

        if abs(hata_x) >= self._takip_deadband and abs(hiz_x) < self._min_takip_hizi:
            hiz_x = self._min_takip_hizi * np.sign(hata_x)

        p_y = hata_y * kp_y

        self._integral_y += hata_y * dt
        self._integral_y = max(-1500.0, min(1500.0, self._integral_y))
        i_y = self._integral_y * ki_y

        d_y = ((hata_y - self._onceki_hata_y) / dt) * kd_y if dt > 0 else 0.0
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

        tol_x = max(self._ates_tolerans_min_px, kutu_genislik * self._ates_tolerans_oran / 100.0)
        tol_y = max(self._ates_tolerans_min_px, kutu_yukseklik * self._ates_tolerans_oran / 100.0)

        merkez_hata_x = self._filtrelenmis_merkez_x - ekran_merkez_x
        merkez_hata_y = self._filtrelenmis_merkez_y - ekran_merkez_y

        tolerans_icinde = (abs(merkez_hata_x) <= tol_x and abs(merkez_hata_y) <= tol_y)
        return tolerans_icinde

    # ================= İMHA =================
    def _balon_patlat(self):
        if not self.pico.bagli_mi():
            return False

        simdi = time.time()
        if simdi - self._son_imha_zamani < 0.6:
            return False

        def _tetikle():
            self.pico.servo_kontrol(
                self._servo_baslangic,
                self._servo_bitis,
                self._servo_bekleme
            )

        threading.Thread(target=_tetikle, daemon=True).start()

        self._toplam_imha += 1
        self._son_imha_zamani = simdi
        return True

    # ================= ANA DÖNGÜ =================
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

            balonlar = self._balon_bul(tespitler)
            secili_balon = self._en_uygun_balon_sec(balonlar, genislik, yukseklik)

            # Yasak bölgedeki / telemetri belirsiz hedefi SEÇME
            if secili_balon is not None and self._hedef_yasak_bolgede(secili_balon, genislik):
                secili_balon = None

            self._secili_balon = secili_balon

            with self._durum_lock:
                mevcut_durum = self._durum

            if mevcut_durum == "SEARCH":
                self._durum_bilgisi = "🔍 Parkur Taranıyor..."
                self._kilit_baslangic_zamani = None

                if secili_balon:
                    self._integral_sifirla()
                    with self._durum_lock:
                        self._durum = "TRACK"
                else:
                    self._tarama_hareketi(dt)

            elif mevcut_durum == "TRACK":
                if secili_balon:
                    tolerans_icinde = self._pid_takip_et(secili_balon, genislik, yukseklik, dt)

                    if tolerans_icinde:
                        if self._kilit_baslangic_zamani is None:
                            self._kilit_baslangic_zamani = simdi

                        gecen_kilit = simdi - self._kilit_baslangic_zamani

                        if gecen_kilit >= self._ates_onay_suresi:
                            self._balon_patlat()
                            self._kilit_baslangic_zamani = None

                            with self._durum_lock:
                                self._durum = "ENGAGE"
                        else:
                            yuzde = int((gecen_kilit / max(0.01, self._ates_onay_suresi)) * 100)
                            self._durum_bilgisi = f"🎯 Kilitleniyor... %{min(100, yuzde)}"
                    else:
                        self._kilit_baslangic_zamani = None
                        self._durum_bilgisi = "🎯 Balon Takip Ediliyor"
                else:
                    self._kilit_baslangic_zamani = None
                    self._integral_sifirla()

                    with self._durum_lock:
                        self._durum = "SEARCH"

            elif mevcut_durum == "ENGAGE":
                self._durum_bilgisi = "💥 Ateşlendi / Takip Sürüyor"
                self._kilit_baslangic_zamani = None

                if secili_balon:
                    self._pid_takip_et(secili_balon, genislik, yukseklik, dt)

                    if simdi - self._son_imha_zamani >= 0.4:
                        with self._durum_lock:
                            self._durum = "TRACK"
                else:
                    self._integral_sifirla()

                    with self._durum_lock:
                        self._durum = "SEARCH"

            time.sleep(0.015)

    # ================= KONTROL =================
    def baslat(self):
        self._aktif.set()

        with self._durum_lock:
            self._durum = "SEARCH"

        self._durum_bilgisi = "Aşama 2 başlatıldı"

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

        self._secili_balon = None
        self._durum_bilgisi = "Beklemede"

        # DÜZELTME: tekrar başlatınca eski/kafadan açıyla başlamasın
        self._mutlak_pan_aci = None
        self._son_pan_telemetri_zamani = 0.0
        self._telemetri_uyari_son_gonderim = 0.0

    def aktif_mi(self):
        return self._aktif.is_set()

    def istatistikleri_al(self):
        return {
            "toplam_imha": self._toplam_imha,
            "durum_bilgisi": self._durum_bilgisi,
            "aktif": self.aktif_mi()
        }
