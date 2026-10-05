"""Yarışma güvenli-yama bayrakları (29 Eylül 2026).

KALICI (bayrak değil, hep açık):
  Y1   Takip döngüsünde kanıt/JPEG/dijital-ikiz kaydı çalışmaz (atış anı FPS düşüşü)
  Y1B  Atıştan sonra aynı hedef için yeni kanıt kaydı açılmaz

AYARLANABİLİR (varsayılan hepsi AÇIK). Kurulum kökündeki
`yarisma_bayraklari.ini` dosyasından okunur; dosya yoksa ya da satır yoksa AÇIK.
Dosyayı elle değil `BAYRAK_AYARLA.bat` menüsüyle değiştirin. Değişiklik bir
sonraki `GUVENLI.bat` başlatmasında geçerli olur.

  Y2  Tetik komutu motor kuyruğunda silinmez
  Y3  Atış bekleme 0.60 s; reddedilen atış beklemeyi yakmaz
  Y4  Aşama 3 takibi = 21 Eylül kontrolcüsü
  Y4B Aşama 2 / balon takibi = 21 Eylül kontrolcüsü
  Y5  Atış kapısı 21 Eylül değerleri
  Y6  Aşama 3 hedef seçicide ikinci kat dost filtresi
  Y8  Döngü hatasında 10 Hz'e düşme yok
  Y9  Balistik servisin yanal (ters işaretli) lead'i kapalı
  Y10 Balistik servisin tüm ofsetleri sıfır: nişangah = yalnız kayıtlı ofset
      (26 Eylül saha loglarında tüm atışlar balistik ek 0 ile yapıldı; zaman
      modeli "15 m"de takılı kalınca her mesafede -35 px ekliyordu.)
  Y11 Radar: otonomda duraklat/devam ve Aşama 3 yeni turda önce MERKEZE dön, sonra süpür
  Y12 Radar: merkeze varış Pico POZ ile teyit edilmeden süpürme başlamaz (POZ yoksa 3 s sonra uyarıyla devam)
  Y15 Hedef kaybında iz koruma: yakındaki balona yeniden kilitlen, yoksa hızı her tikte x0.6 frenle
"""

from __future__ import annotations

import os
from pathlib import Path

_ACIK = {"1", "true", "evet", "acik", "açık", "on", "yes"}
_KAPALI = {"0", "false", "hayir", "hayır", "kapali", "kapalı", "off", "no"}

AYARLANABILIR = ("Y2", "Y3", "Y4", "Y4B", "Y5", "Y6", "Y8", "Y9", "Y10", "Y11", "Y12", "Y15")


def ini_yolu() -> Path:
    # .../<kok>/backend/app/services/yarisma_bayraklari.py -> <kok>/yarisma_bayraklari.ini
    return Path(__file__).resolve().parents[3] / "yarisma_bayraklari.ini"


def _oku() -> dict[str, bool]:
    degerler = {ad: True for ad in AYARLANABILIR}
    try:
        for satir in ini_yolu().read_text(encoding="utf-8").splitlines():
            satir = satir.split("#", 1)[0].strip()
            if "=" not in satir:
                continue
            ad, deger = (x.strip() for x in satir.split("=", 1))
            ad = ad.upper()
            if ad in degerler:
                v = deger.lower()
                if v in _KAPALI:
                    degerler[ad] = False
                elif v in _ACIK:
                    degerler[ad] = True
    except OSError:
        pass
    # İsteğe bağlı ortam değişkeni ezmesi (ör. ISTIKLAL_Y5=0); normalde gerekmez.
    for ad in AYARLANABILIR:
        env = os.getenv(f"ISTIKLAL_{ad}")
        if env is not None:
            degerler[ad] = env.strip().lower() not in _KAPALI
    return degerler


_D = _oku()

Y1_KANIT_KAPALI = True      # kalıcı
Y1B_KANIT_SURDUR = True     # kalıcı
Y2_TETIK_KORU = _D["Y2"]
Y3_ATIS_ZAMANLAMA = _D["Y3"]
Y4_SAMPIYON_A3 = _D["Y4"]
Y4B_SAMPIYON_A2 = _D["Y4B"]
Y5_KAPI_21EYLUL = _D["Y5"]
Y6_DOST_FILTRE_A3 = _D["Y6"]
Y8_HATA_KADANS = _D["Y8"]
Y9_BALISTIK_LEAD_YOK = _D["Y9"]
Y10_BALISTIK_KAPALI = _D["Y10"]
Y11_RADAR_MERKEZ = _D["Y11"]
Y12_RADAR_POZ_TEYIT = _D["Y12"]
Y15_KAYIP_FREN = _D["Y15"]

Y3_BEKLEME_S = 0.60          # 26 Eylül: "Pico 580 ms servo stroku ile senkron"
Y3_RED_SONRASI_TEKRAR_S = 0.10


def durum() -> dict[str, bool]:
    """Açılışta loga yazılır: hangi yamaların aktif olduğu kayıt altında olsun."""
    return {
        "Y1_KANIT_KAPALI(kalici)": Y1_KANIT_KAPALI,
        "Y1B_KANIT_SURDUR(kalici)": Y1B_KANIT_SURDUR,
        "Y2_TETIK_KORU": Y2_TETIK_KORU,
        "Y3_ATIS_ZAMANLAMA": Y3_ATIS_ZAMANLAMA,
        "Y4_SAMPIYON_A3": Y4_SAMPIYON_A3,
        "Y4B_SAMPIYON_A2": Y4B_SAMPIYON_A2,
        "Y5_KAPI_21EYLUL": Y5_KAPI_21EYLUL,
        "Y6_DOST_FILTRE_A3": Y6_DOST_FILTRE_A3,
        "Y8_HATA_KADANS": Y8_HATA_KADANS,
        "Y9_BALISTIK_LEAD_YOK": Y9_BALISTIK_LEAD_YOK,
        "Y10_BALISTIK_KAPALI": Y10_BALISTIK_KAPALI,
        "Y11_RADAR_MERKEZ": Y11_RADAR_MERKEZ,
        "Y12_RADAR_POZ_TEYIT": Y12_RADAR_POZ_TEYIT,
        "Y15_KAYIP_FREN": Y15_KAYIP_FREN,
        "ini": str(ini_yolu()),
    }


_A2_AILESI = {"A2", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}


def kontrolcu_olustur(mode: str, persisted_path=None):
    """LightTrackingController fabrikası.

    Y4/Y4B kapalıyken güncel sınıfı güncel argümanlarla döndürür (değişiklik yok).
    Açıkken 21 Eylül'de zikzakta vuran A2 kontrolcüsünün BİREBİR kopyasını
    (light_tracking_controller_21eylul.py) "A2" profiliyle döndürür.
    21 Eylül'de A2 kalıcı JSON'u yüklemiyordu; bu yüzden persisted_path=None.
    """
    normalized = str(mode or "").upper()
    if (normalized == "A3" and Y4_SAMPIYON_A3) or (normalized in _A2_AILESI and Y4B_SAMPIYON_A2):
        from app.services.light_tracking_controller_21eylul import LightTrackingController as Sampiyon21

        return Sampiyon21("A2", persisted_path=None)
    from app.services.light_tracking_controller import LightTrackingController

    return LightTrackingController(mode, persisted_path=persisted_path)
