"""
asama1_manual.py
Aşama 1: Manuel Balon Patlatma Modülü

Operatör tareti manuel kontrol ederken hedefin altındaki balona nişan alır
ve buton tetiklemesiyle balonu imha eder.

İşleyiş:
- GUI üzerinden "Aşama 1" modu seçildiğinde manuel kontrol eksenleri aktif kalır.
- GUI'de veya joystick üzerindeki "Balon Patlat" komutu geldiğinde 
  mevcut servo tetikleme mekanizması çalışır.
"""

import threading
import time


class Asama1Manual:
    """
    Aşama 1: Manuel Balon Patlatma modülünü yönetir.
    
    Bu modül operatörün manuel kontrolü altında balon patlatma işlemini gerçekleştirir.
    Taret hareketi tamamen operatör kontrolündedir, sadece ateşleme komutu bu modül
    tarafından yönetilir.
    """
    
    def __init__(self, pico_baglanti, goruntu_motoru=None):
        """
        Aşama 1 modülünü başlatır.
        
        Args:
            pico_baglanti: PicoBaglanti nesnesi - servo kontrol için
            goruntu_motoru: GoruntuMotoru nesnesi - opsiyonel, görsel geri bildirim için
        """
        self.pico = pico_baglanti
        self.goruntu = goruntu_motoru
        
        # Modül durum bayrağı
        self._aktif = threading.Event()
        self._thread = None
        
        # Servo parametreleri (varsayılan değerler)
        self._servo_baslangic = 90  # Başlangıç açısı
        self._servo_bitis = 45      # Bitiş açısı
        self._servo_bekleme = 350   # Bekleme süresi (ms)
        
        # İstatistikler
        self._toplam_atesleme = 0
        self._son_atesleme_zamani = 0.0
        
        # Durum bilgisi
        self._durum = "Beklemede"
        self._durum_lock = threading.Lock()
    
    @property
    def durum(self):
        """
        Modülün mevcut durumunu döndürür.
        
        Returns:
            str: Mevcut durum ("Beklemede", "Ateşleme Yapıldı", vb.)
        """
        with self._durum_lock:
            return self._durum
    
    @durum.setter
    def durum(self, deger):
        """
        Modül durumunu günceller.
        
        Args:
            deger: Yeni durum değeri (str)
        """
        with self._durum_lock:
            self._durum = deger
    
    def servo_parametrelerini_ayarla(self, baslangic=None, bitis=None, bekleme=None):
        """
        Servo motor parametrelerini ayarlar.
        
        Args:
            baslangic: Başlangıç açısı (0-180)
            bitis: Bitiş açısı (0-180)
            bekleme: Bekleme süresi (ms, 100-1000)
            
        Returns:
            bool: Ayarlama başarılı mı?
        """
        try:
            if baslangic is not None:
                baslangic = int(baslangic)
                if 0 <= baslangic <= 180:
                    self._servo_baslangic = baslangic
                else:
                    return False
                    
            if bitis is not None:
                bitis = int(bitis)
                if 0 <= bitis <= 180:
                    self._servo_bitis = bitis
                else:
                    return False
                    
            if bekleme is not None:
                bekleme = int(bekleme)
                if 100 <= bekleme <= 1000:
                    self._servo_bekleme = bekleme
                else:
                    return False
                    
            return True
        except (ValueError, TypeError):
            return False
    
    def servo_parametrelerini_al(self):
        """
        Mevcut servo parametrelerini döndürür.
        
        Returns:
            dict: Servo parametreleri (baslangic, bitis, bekleme)
        """
        return {
            "baslangic": self._servo_baslangic,
            "bitis": self._servo_bitis,
            "bekleme": self._servo_bekleme
        }
    
    def atesle(self):
        """
        Balon patlatma işlemini başlatır.
        
        Bu fonksiyon servo motoru başlangıç açısından bitiş açısına
        hareket ettirir, belirtilen süre bekler ve tekrar başlangıç
        açısına döner.
        
        Returns:
            bool: Ateşleme başarılı mı?
        """
        if not self.pico.bagli_mi():
            self.durum = "HATA: Bağlantı yok"
            return False
        
        # Parametreleri al
        baslangic_aci = self._servo_baslangic
        bitis_aci = self._servo_bitis
        bekleme_ms = self._servo_bekleme
        
        # Servo komutunu gönder
        basarili = self.pico.servo_kontrol(baslangic_aci, bitis_aci, bekleme_ms)
        
        if basarili:
            self._toplam_atesleme += 1
            self._son_atesleme_zamani = time.time()
            self.durum = f"✓ Ateşleme yapıldı ({baslangic_aci}° → {bitis_aci}°)"
            return True
        else:
            self.durum = "✗ Ateşleme başarısız"
            return False
    
    def baslat(self):
        """
        Aşama 1 modülünü başlatır.
        
        Bu modül arka planda çalışan bir thread gerektirmez çünkü
        tüm kontroller manuel olarak GUI üzerinden yapılır.
        Sadece durum bayrağını aktif eder.
        """
        self._aktif.set()
        self.durum = "Aşama 1 Aktif - Manuel Kontrol"
    
    def dur(self):
        """
        Aşama 1 modülünü durdurur.
        
        Durum bayrağını temizler ve modülü bekleme moduna alır.
        """
        self._aktif.clear()
        self.durum = "Aşama 1 Durduruldu"
    
    def aktif_mi(self):
        """
        Modülün aktif olup olmadığını kontrol eder.
        
        Returns:
            bool: Modül aktif mi?
        """
        return self._aktif.is_set()
    
    def istatistikleri_al(self):
        """
        Modül istatistiklerini döndürür.
        
        Returns:
            dict: İstatistikler (toplam_atesleme, son_atesleme_zamani)
        """
        return {
            "toplam_atesleme": self._toplam_atesleme,
            "son_atesleme_zamani": self._son_atesleme_zamani,
            "aktif": self.aktif_mi(),
            "durum": self.durum
        }
