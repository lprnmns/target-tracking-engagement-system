"""
pico_baglanti.py
Raspberry Pi Pico 2 seri port bağlantı yönetimi.
"""

import threading
import time
import queue

import serial
from serial.tools import list_ports


class PicoBaglanti:
    """
    Pico ile seri haberleşmeyi arka planda yöneten sınıf.
    """

    def __init__(self, handler=None):
        """
        handler:
            Pico'dan gelen durumları arayüze bildiren fonksiyon.
            Örnek çağrı:
                handler("BAGLANTI_BASARILI")
                handler("PICO_YANIT", "OK,HOME_DONE")
        """
        self._handler = handler
        self._seri = None
        self._kilit = threading.Lock()
        self._kuyruk = queue.Queue(maxsize=200)
        self._calisiyor = threading.Event()
        self._yazici_thread = None
        self._okuyucu_thread = None

    def _bildir(self, tip, veri=None):
        """
        Handler fonksiyonunu güvenli şekilde çağırır.
        """
        try:
            if self._handler:
                self._handler(tip, veri)
        except Exception:
            pass

    def bagli_mi(self):
        """
        Seri port açık mı?
        """
        with self._kilit:
            return self._seri is not None and self._seri.is_open

    def portlari_bul(self):
        """
        Mevcut COM portlarını listeler. Pico portlarını öne alır.
        """
        try:
            portlar = list(list_ports.comports())
            pico_portlari = []
            diger_portlar = []

            for p in portlar:
                if p.device and (
                    p.vid == 0x2E8A or
                    "Raspberry" in (p.description or "") or
                    "Pico" in (p.description or "")
                ):
                    pico_portlari.append(p.device)
                else:
                    diger_portlar.append(p.device)

            return pico_portlari + diger_portlar

        except Exception:
            return []

    def baglan_async(self, port):
        """
        Arka planda bağlantı başlatır.
        Bağlantı kesilirse otomatik yeniden bağlanma dener.
        """
        self._yeniden_baglan_port = port
        self._yeniden_baglan_deneme = 0
        self._max_yeniden_baglan = 5
        threading.Thread(
            target=self._baglanti_islemi,
            args=(port,),
            daemon=True
        ).start()

    def _baglanti_islemi(self, port):
        """
        Seri portu açar, Pico'nun cevap vermesini bekler.
        Bağlantı kesilirse otomatik yeniden bağlanmayı dener.
        """
        try:
            ser = serial.Serial(
                port,
                460800,
                timeout=0.2,
                write_timeout=0.1
            )

            time.sleep(1.5)

            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass

            yanit = ""

            for _ in range(30):
                try:
                    ser.write(b"PING\n")
                except Exception:
                    break

                time.sleep(0.25)

                try:
                    yanit = ser.readline().decode(errors="ignore").strip()
                except Exception:
                    yanit = ""

                if yanit.startswith("OK,"):
                    break

            if not yanit.startswith("OK,"):
                try:
                    ser.close()
                except Exception:
                    pass

                self._bildir(
                    "BAGLANTI_HATA",
                    "Cihaz yanıt vermedi (OK,PONG alınamadı).\n"
                    "Pico'da doğru kodun yüklü olduğundan emin olun."
                )
                return

            while not self._kuyruk.empty():
                try:
                    self._kuyruk.get_nowait()
                except queue.Empty:
                    break

            with self._kilit:
                self._seri = ser

            self._calisiyor.set()
            self._yeniden_baglan_deneme = 0

            self._yazici_thread = threading.Thread(
                target=self._yazici_loop,
                daemon=True
            )
            self._okuyucu_thread = threading.Thread(
                target=self._okuyucu_loop,
                daemon=True
            )

            self._yazici_thread.start()
            self._okuyucu_thread.start()

            self._bildir("BAGLANTI_BASARILI")

        except Exception as e:
            self._bildir("BAGLANTI_HATA", str(e))

    def _yazici_loop(self):
        """
        Kuyruktan komutları alır ve Pico'ya gönderir.
        """
        while self._calisiyor.is_set():
            try:
                komut = self._kuyruk.get(timeout=0.1)
            except queue.Empty:
                continue

            if not self._calisiyor.is_set():
                break

            with self._kilit:
                ser = self._seri

            if ser is None or not ser.is_open:
                continue

            try:
                ser.write((komut + "\n").encode())
            except Exception:
                self._bildir("BAGLANTI_KOPTU")
                break

    def _okuyucu_loop(self):
        """
        Pico'dan gelen satırları okur ve handler'a iletir.
        Bağlantı kesilirse otomatik yeniden bağlanmayı dener.
        """
        while self._calisiyor.is_set():
            with self._kilit:
                ser = self._seri

            if ser is None or not ser.is_open:
                # Bağlantı koptu, yeniden bağlanmayı dene
                if hasattr(self, '_yeniden_baglan_port') and self._yeniden_baglan_port:
                    self._yeniden_baglan_deneme += 1
                    if self._yeniden_baglan_deneme <= self._max_yeniden_baglan:
                        self._bildir("BAGLANTI_KOPTU")
                        time.sleep(2.0)  # 2 saniye bekle
                        self._baglanti_islemi(self._yeniden_baglan_port)
                        return
                    else:
                        self._bildir("BAGLANTI_KOPTU")
                        break
                else:
                    self._bildir("BAGLANTI_KOPTU")
                    break
            
            try:
                line = ser.readline().decode(errors="ignore").strip()
            except Exception:
                self._bildir("BAGLANTI_KOPTU")
                # Yeniden bağlanma mekanizmasını tetikle
                if hasattr(self, '_yeniden_baglan_port') and self._yeniden_baglan_port:
                    self._yeniden_baglan_deneme += 1
                    if self._yeniden_baglan_deneme <= self._max_yeniden_baglan:
                        time.sleep(2.0)
                        self._baglanti_islemi(self._yeniden_baglan_port)
                        return
                break

            if line:
                self._bildir("PICO_YANIT", line)

    def komut_gonder(self, komut):
        """
        Komutu yazma kuyruğuna ekler.
        """
        if not self.bagli_mi():
            return

        try:
            self._kuyruk.put_nowait(komut)
        except queue.Full:
            pass

    def kes(self):
        """
        Seri port bağlantısını kapatır.
        """
        self._calisiyor.clear()

        if self._yazici_thread and self._yazici_thread.is_alive():
            self._yazici_thread.join(timeout=1.0)

        if self._okuyucu_thread and self._okuyucu_thread.is_alive():
            self._okuyucu_thread.join(timeout=1.0)

        with self._kilit:
            ser = self._seri
            self._seri = None

        if ser:
            try:
                ser.close()
            except Exception:
                pass

    def servo_kontrol(self, baslangic_aci, bitis_aci, bekleme_ms=350):
        """
        Servo motoru başlangıç açısından bitiş açısına hareket ettirir,
        belirtilen süre bekler ve tekrar başlangıç açısına döner.
        
        Args:
            baslangic_aci: Servonun başlangıç açısı (0-180)
            bitis_aci: Servonun hedef açısı (0-180)
            bekleme_ms: Bitiş açısında beklenecek süre (milisaniye)
        """
        if not self.bagli_mi():
            return False
        
        # SERVO komutu: SERVO,baslangic,bitis,bekleme
        komut = f"SERVO,{int(baslangic_aci)},{int(bitis_aci)},{int(bekleme_ms)}"
        self.komut_gonder(komut)
        return True