"""
goruntu_motoru.py
Kamera okuma, Ultralytics YOLO ile tespit ve iş parçacığı yönetimi.
Aşama 2 ve Aşama 3 için çift model ve hiyerarşik etiketleme destekli sürüm.
"""

import os
import threading
import time
from collections import deque

import cv2
from ultralytics import YOLO


class GoruntuMotoru:
    """
    Kamera ve YOLO tespitini arka planda çalıştıran sınıf.
    Çift model desteği: Hedef (hava aracı) ve balon tespiti için ayrı modeller çalıştırır.
    """

    def __init__(
        self,
        kamera_portu=0,
        genislik=640,
        yukseklik=480,
        engine_yolu="yolo11s.engine",
        conf_threshold=0.5,
        ikinci_model_yolu=None  # Balon tespiti için ikinci model
    ):
        self._port = kamera_portu
        self._genislik = genislik
        self._yukseklik = yukseklik
        self._engine_yolu = engine_yolu
        self._ikinci_model_yolu = ikinci_model_yolu

        # Model algılama doğruluk eşiği
        self._conf_threshold = conf_threshold
        self._ikinci_conf_threshold = conf_threshold

        # Veri güvenliği için kilitler
        self._veri_kilit = threading.Lock()
        self._ayar_kilit = threading.Lock()

        # Arka plan iş parçacıklarının kontrolü
        self._devam_et = threading.Event()
        self._kamera_thread = None
        self._infer_thread = None
        self._ayarlar_degisti = False

        # Kamera nesnesi
        self._kamera = None

        # Ultralytics modelleri - Çift model desteği
        self._model = None  # Birincil model (Hava aracı hedefleri)
        self._model_hazir = False
        self._ikinci_model = None  # İkinci model (Balon)
        self._ikinci_model_hazir = False

        # Son veri alanları
        self._en_son_kare = None
        self._en_son_kare_zamani = 0.0
        self._en_son_tespitler = []
        self._gecikme_ms = 0.0

        # METRİKLER
        self._kamera_fps = 0.0
        self._inference_fps = 0.0
        self._inference_sureler = deque(maxlen=30)
        self._ortalama_inference_ms = 0.0
        self._max_inference_ms = 0.0
        self._frame_zamanlari = deque(maxlen=30)
        self._ortalama_frame_ms = 0.0
        self._max_frame_ms = 0.0
        self._toplam_kamera_kareleri = 0
        self._toplam_inference_kareleri = 0
        self._son_islenen_zamani = 0.0

        # Modelleri yükle
        self._model_yukle(engine_yolu)
        if ikinci_model_yolu:
            self._ikinci_model_yukle(ikinci_model_yolu)

        # Kamerayı aç
        self._kamera_ac()

    def _model_dosyasini_bul(self, yol):
        """
        YOLO model dosyasının varlığını kontrol eder; gerekliyse mutlak yol döndürür.
        """
        if os.path.isfile(yol):
            return yol

        taban = os.path.dirname(os.path.abspath(__file__))
        adaylar = [
            os.path.join(taban, yol),
            os.path.join(taban, "..", yol),
        ]

        for aday in adaylar:
            if os.path.isfile(aday):
                return aday

        raise FileNotFoundError(f"Model dosyası bulunamadı: {yol}")

    def _model_yukle(self, yol):
        """
        Ultralytics YOLO modelini yükler (birincil model).
        """
        try:
            tam_yol = self._model_dosyasini_bul(yol)
            self._model = YOLO(tam_yol)
            self._model_hazir = True
            self._engine_yolu = tam_yol
            print(f"[Bilgi] Birincil model yüklendi: {tam_yol}")
        except Exception as hata:
            print(f"[Uyarı] YOLO modeli yüklenemedi: {hata}")
            if yol.endswith(".engine"):
                alternatif_yol = yol.replace(".engine", ".pt")
                try:
                    tam_yol = self._model_dosyasini_bul(alternatif_yol)
                    self._model = YOLO(tam_yol)
                    self._model_hazir = True
                    self._engine_yolu = tam_yol
                    print(f"[Bilgi] Fallback model yüklendi: {tam_yol}")
                except Exception as fallback_hata:
                    print(f"[Hata] Fallback model de yüklenemedi: {fallback_hata}")
                    self._model = None
                    self._model_hazir = False
            else:
                self._model = None
                self._model_hazir = False

    def _ikinci_model_yukle(self, yol):
        """
        Ultralytics YOLO ikinci modelini yükler (balon tespiti).
        """
        try:
            tam_yol = self._model_dosyasini_bul(yol)
            self._ikinci_model = YOLO(tam_yol)
            self._ikinci_model_hazir = True
            self._ikinci_model_yolu = tam_yol
            print(f"[Bilgi] İkinci model yüklendi: {tam_yol}")
        except Exception as hata:
            print(f"[Uyarı] İkinci YOLO modeli yüklenemedi: {hata}")
            if yol.endswith(".engine"):
                alternatif_yol = yol.replace(".engine", ".pt")
                try:
                    tam_yol = self._model_dosyasini_bul(alternatif_yol)
                    self._ikinci_model = YOLO(tam_yol)
                    self._ikinci_model_hazir = True
                    self._ikinci_model_yolu = tam_yol
                    print(f"[Bilgi] İkinci fallback model yüklendi: {tam_yol}")
                except Exception as fallback_hata:
                    print(f"[Hata] İkinci fallback model de yüklenemedi: {fallback_hata}")
                    self._ikinci_model = None
                    self._ikinci_model_hazir = False
            else:
                self._ikinci_model = None
                self._ikinci_model_hazir = False

    def _kamera_ac(self):
        """
        Verilen port ve çözünürlük ile kamerayı başlatır.
        """
        try:
            if self._kamera is not None:
                try:
                    self._kamera.release()
                except Exception:
                    pass
                self._kamera = None

            self._kamera = cv2.VideoCapture(self._port)

            if not self._kamera.isOpened():
                print(f"[Uyarı] Kamera açılamadı: port={self._port}")
                return

            self._kamera.set(cv2.CAP_PROP_FRAME_WIDTH, self._genislik)
            self._kamera.set(cv2.CAP_PROP_FRAME_HEIGHT, self._yukseklik)
            self._kamera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self._kamera.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc('M', 'J', 'P', 'G')
            )

        except Exception as hata:
            print(f"[Hata] Kamera başlatma hatası: {hata}")
            self._kamera = None

    def _hsv_renk_analizi(self, goruntu, kutu):
        """
        Hava araçlarının (F16 / Helikopter) gövde rengini analiz eder.
        Dönüş: "kirmizi", "mavi" veya "belirsiz"
        """
        x1, y1, x2, y2 = kutu
        roi = goruntu[y1:y2, x1:x2]

        if roi.size == 0:
            return "belirsiz"

        # 32x32 boyutunda hızlı analiz
        roi_kucuk = cv2.resize(roi, (32, 32))
        hsv = cv2.cvtColor(roi_kucuk, cv2.COLOR_BGR2HSV)

        # Kırmızı renk aralıkları
        kirmizi_mask1 = cv2.inRange(hsv, (0, 70, 50), (15, 255, 255))
        kirmizi_mask2 = cv2.inRange(hsv, (160, 70, 50), (180, 255, 255))
        kirmizi_mask = cv2.bitwise_or(kirmizi_mask1, kirmizi_mask2)
        kirmizi_oran = cv2.countNonZero(kirmizi_mask) / 1024.0

        # Mavi renk aralığı
        mavi_mask = cv2.inRange(hsv, (100, 70, 50), (130, 255, 255))
        mavi_oran = cv2.countNonZero(mavi_mask) / 1024.0

        if mavi_oran > 0.15 and mavi_oran > kirmizi_oran:
            return "mavi"
        elif kirmizi_oran > 0.15 and kirmizi_oran > mavi_oran:
            return "kirmizi"
        else:
            return "belirsiz"

    def _dost_dusman_belirle(self, sinif_adi, model_tipi, goruntu=None, kutu=None):
        """
        Tespit edilen nesnenin durumunu belirler:
        - Balonlar (ikinci model veya adı balon olanlar): Daima nötr "balon" döner.
        - Drone ve Balistik Füze: Daima "düşman" döner.
        - F16 ve Helikopter: Gövde rengi MAVİ ise "dost", KIRMIZI/DİĞER ise "düşman" döner.
        """
        sinif_adi_kucuk = sinif_adi.lower()

        # 1. BALON TESPİTİ - Balonlar asla peşinen dost veya düşman olarak etiketlenmez
        if model_tipi == "ikinci" or "balon" in sinif_adi_kucuk or "balloon" in sinif_adi_kucuk:
            return "balon"

        # 2. DRONE VE BALİSTİK FÜZE - Rengine bakılmaksızın doğrudan düşman
        if any(k in sinif_adi_kucuk for k in ["drone", "iha", "balistik", "ballistic", "fuze"]):
            return "düşman"

        # 3. F16 VE HELİKOPTER - Gövde renk analizi
        if any(k in sinif_adi_kucuk for k in ["f16", "f-16", "helikopter", "helicopter"]):
            if goruntu is not None and kutu is not None:
                renk = self._hsv_renk_analizi(goruntu, kutu)
                if renk == "mavi":
                    return "dost"
                return "düşman"
            return "düşman"

        # Diğer tanımlanamayan hava araçları
        return "düşman"

    def _tespit_et(self, goruntu):
        """
        Birincil (Hava Aracı) ve İkinci (Balon) modelleri koşturup tespitleri toplar.
        """
        with self._ayar_kilit:
            model = self._model
            model_hazir = self._model_hazir
            ikinci_model = self._ikinci_model
            ikinci_model_hazir = self._ikinci_model_hazir
            conf = self._conf_threshold
            ikinci_conf = self._ikinci_conf_threshold

        tespitler = []

        # 1. Birincil Model (Hava Araçları)
        if model_hazir and model is not None:
            try:
                sonuclar = model(goruntu, verbose=False, conf=conf)[0]
                for kutu in sonuclar.boxes:
                    x1, y1, x2, y2 = [int(v) for v in kutu.xyxy[0].tolist()]
                    guven = float(kutu.conf[0])
                    sinif_idx = int(kutu.cls[0])

                    try:
                        sinif_adi = model.names[sinif_idx]
                    except Exception:
                        sinif_adi = "hedef"

                    kutu_liste = [x1, y1, x2, y2]
                    durum = self._dost_dusman_belirle(sinif_adi, "birincil", goruntu, kutu_liste)

                    tespitler.append({
                        "kutu": kutu_liste,
                        "guven": guven,
                        "sinif": sinif_adi,
                        "durum": durum,  # dost, düşman veya balon
                        "model": "birincil"
                    })
            except Exception as hata:
                print(f"[Hata] Birincil model tespit hatası: {hata}")

        # 2. İkinci Model (Balonlar)
        if ikinci_model_hazir and ikinci_model is not None:
            try:
                sonuclar = ikinci_model(goruntu, verbose=False, conf=ikinci_conf)[0]
                for kutu in sonuclar.boxes:
                    x1, y1, x2, y2 = [int(v) for v in kutu.xyxy[0].tolist()]
                    guven = float(kutu.conf[0])
                    sinif_idx = int(kutu.cls[0])

                    try:
                        sinif_adi = ikinci_model.names[sinif_idx]
                    except Exception:
                        sinif_adi = "balon"

                    # İkinci model çıktıları istisnasız nötr 'balon' durumunu alır
                    durum = self._dost_dusman_belirle(sinif_adi, "ikinci")

                    tespitler.append({
                        "kutu": [x1, y1, x2, y2],
                        "guven": guven,
                        "sinif": sinif_adi,
                        "durum": durum,  # Daima 'balon'
                        "model": "ikinci"
                    })
            except Exception as hata:
                print(f"[Hata] İkinci model tespit hatası: {hata}")

        return tespitler

    def _kamera_loop(self):
        """Sürekli en taze kareyi okur."""
        sayac = 0
        baslangic = time.time()
        onceki_zaman = time.time()

        while self._devam_et.is_set():
            with self._ayar_kilit:
                if self._ayarlar_degisti:
                    self._kamera_ac()
                    self._ayarlar_degisti = False

            try:
                if self._kamera is None or not self._kamera.isOpened():
                    self._kamera_ac()
                    time.sleep(0.2)
                    continue

                if not self._kamera.grab():
                    time.sleep(0.005)
                    continue

                ret, kare = self._kamera.retrieve()
                if not ret:
                    time.sleep(0.005)
                    continue

            except Exception:
                time.sleep(0.01)
                continue

            simdi = time.time()
            frame_suresi = simdi - onceki_zaman
            onceki_zaman = simdi
            self._frame_zamanlari.append(frame_suresi)

            if self._frame_zamanlari:
                self._ortalama_frame_ms = (sum(self._frame_zamanlari) / len(self._frame_zamanlari)) * 1000
                self._max_frame_ms = max(self._frame_zamanlari) * 1000

            with self._veri_kilit:
                self._en_son_kare = kare
                self._en_son_kare_zamani = simdi

            self._toplam_kamera_kareleri += 1

            sayac += 1
            if simdi - baslangic >= 1.0:
                self._kamera_fps = sayac
                sayac = 0
                baslangic = simdi

    def _inference_loop(self):
        """En taze kareyi alır ve YOLO ile işler."""
        sayac = 0
        baslangic = time.time()

        while self._devam_et.is_set():
            with self._veri_kilit:
                kare = self._en_son_kare
                kare_zamani = self._en_son_kare_zamani

            if kare is None:
                time.sleep(0.002)
                continue

            if kare_zamani == self._son_islenen_zamani:
                time.sleep(0.002)
                continue

            self._son_islenen_zamani = kare_zamani

            bas = time.time()
            tespitler = self._tespit_et(kare)
            bitis = time.time()

            sure_ms = (bitis - bas) * 1000
            self._inference_sureler.append(sure_ms)

            if self._inference_sureler:
                self._ortalama_inference_ms = sum(self._inference_sureler) / len(self._inference_sureler)
                self._max_inference_ms = max(self._inference_sureler)

            with self._veri_kilit:
                self._en_son_tespitler = tespitler
                self._gecikme_ms = sure_ms

            self._toplam_inference_kareleri += 1

            sayac += 1
            if time.time() - baslangic >= 1.0:
                self._inference_fps = sayac
                sayac = 0
                baslangic = time.time()

            time.sleep(0.001)

    def baslat(self):
        """Kamera ve YOLO iş parçacıklarını başlatır."""
        self._devam_et.set()

        if self._kamera_thread is None or not self._kamera_thread.is_alive():
            self._kamera_thread = threading.Thread(target=self._kamera_loop, daemon=True)
            self._kamera_thread.start()

        if self._infer_thread is None or not self._infer_thread.is_alive():
            self._infer_thread = threading.Thread(target=self._inference_loop, daemon=True)
            self._infer_thread.start()

    def dur(self):
        """Tüm arka plan iş parçacıklarını durdurur."""
        self._devam_et.clear()
        if self._kamera_thread:
            self._kamera_thread.join(timeout=1.0)
        if self._infer_thread:
            self._infer_thread.join(timeout=1.0)

    def ayar_degistir(self, port=None, genislik=None, yukseklik=None):
        """Kamera portu ve/veya çözünürlük ayarlarını günceller."""
        with self._ayar_kilit:
            if port is not None:
                self._port = port
            if genislik is not None:
                self._genislik = genislik
            if yukseklik is not None:
                self._yukseklik = yukseklik
            self._ayarlar_degisti = True

    def set_conf_threshold(self, deger):
        """Birincil model için confidence threshold ayarlar."""
        try:
            deger = max(0.01, min(0.99, float(deger)))
            with self._ayar_kilit:
                self._conf_threshold = deger
        except Exception:
            pass

    def set_ikinci_conf_threshold(self, deger):
        """İkinci model için confidence threshold ayarlar."""
        try:
            deger = max(0.01, min(0.99, float(deger)))
            with self._ayar_kilit:
                self._ikinci_conf_threshold = deger
        except Exception:
            pass

    def model_yukle(self, yeni_yol):
        """Yeni birincil model yükler."""
        try:
            tam_yol = self._model_dosyasini_bul(yeni_yol)
            yeni_model = YOLO(tam_yol)

            with self._ayar_kilit:
                self._model = yeni_model
                self._model_hazir = True
                self._engine_yolu = tam_yol

            return True, tam_yol
        except Exception as hata:
            with self._ayar_kilit:
                self._model_hazir = False
            return False, str(hata)

    def ikinci_model_yukle(self, yeni_yol):
        """Yeni ikinci model yükler."""
        try:
            tam_yol = self._model_dosyasini_bul(yeni_yol)
            yeni_model = YOLO(tam_yol)

            with self._ayar_kilit:
                self._ikinci_model = yeni_model
                self._ikinci_model_hazir = True
                self._ikinci_model_yolu = tam_yol

            return True, tam_yol
        except Exception as hata:
            with self._ayar_kilit:
                self._ikinci_model_hazir = False
            return False, str(hata)

    def model_durumunu_al(self):
        with self._ayar_kilit:
            return self._model_hazir, self._engine_yolu

    def ikinci_model_durumunu_al(self):
        with self._ayar_kilit:
            return self._ikinci_model_hazir, self._ikinci_model_yolu if self._ikinci_model_yolu else "Yok"

    def verileri_al(self):
        """En son kare ve tespit verilerini döndürür."""
        with self._veri_kilit:
            if self._en_son_kare is None:
                return None, [], 0.0, 0.0, 0.0

            return (
                self._en_son_kare,
                list(self._en_son_tespitler),
                self._kamera_fps,
                self._gecikme_ms,
                self._en_son_kare_zamani
            )

    def metrikleri_al(self):
        with self._veri_kilit:
            return {
                "kamera_fps": self._kamera_fps,
                "inference_fps": self._inference_fps,
                "ortalama_inference_ms": self._ortalama_inference_ms,
                "max_inference_ms": self._max_inference_ms,
                "ortalama_frame_ms": self._ortalama_frame_ms,
                "max_frame_ms": self._max_frame_ms,
                "dropped_frames": max(0, self._toplam_kamera_kareleri - self._toplam_inference_kareleri),
                "toplam_kamera_kareleri": self._toplam_kamera_kareleri,
                "toplam_inference_kareleri": self._toplam_inference_kareleri,
            }
