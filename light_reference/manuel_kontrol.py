"""
manuel_kontrol.py
Logitech Joystick ve WASD klavye ile manuel kontrol yönetimi.

EKSEN TAKASI:
Joystick Y (ileri/geri) -> Sistemin X ekseni
Joystick X (sağ/sol)    -> Sistemin Y ekseni
"""

import time

import pygame


class ManuelKontrol:
    JOY_DEADZONE = 0.08
    JOY_EXPO = 1.8
    GONDERIM_HZ = 30

    JOY_X_TERS = False
    JOY_Y_TERS = True

    def __init__(self):
        self.joystick = None
        self.isim = "Bulunamadı"

        self.tuslar = {
            "W": False,
            "S": False,
            "A": False,
            "D": False
        }

        self._son_gonderme = 0.0

        self._anlik_x = 0
        self._anlik_y = 0
        self._anlik_tetik = 0

        try:
            pygame.init()
            pygame.joystick.init()

            if pygame.joystick.get_count() > 0:
                self.joystick = pygame.joystick.Joystick(0)
                self.joystick.init()
                self.isim = self.joystick.get_name()

        except Exception:
            self.joystick = None
            self.isim = "Bulunamadı"

    def tus_basla(self, tus):
        self.tuslar[tus] = True

    def tus_birak(self, tus):
        self.tuslar[tus] = False

    def _joystick_olcekle(self, ham_deger):
        """
        Deadzone + expo uygulayarak joystick değerini -1000..+1000 arasına çevirir.
        """
        deadzone = self.JOY_DEADZONE

        if abs(ham_deger) < deadzone:
            return 0

        isaret = 1 if ham_deger > 0 else -1

        oran = (abs(ham_deger) - deadzone) / (1.0 - deadzone)

        if oran < 0.0:
            oran = 0.0
        if oran > 1.0:
            oran = 1.0

        if self.JOY_EXPO > 0.01:
            oran = oran ** self.JOY_EXPO

        deger = int(oran * 1000.0)

        if deger > 1000:
            deger = 1000

        return isaret * deger

    def komut_uret(self):
        """
        Manuel kontrol komutu üretir.
        Joystick bağlantısı kesilirse algılar ve sıfır komut döner.

        Dönüş:
            (komut_stringi veya None, x_degeri, y_degeri)
        """
        simdi = time.time()

        if simdi - self._son_gonderme < (1.0 / self.GONDERIM_HZ):
            return None, self._anlik_x, self._anlik_y

        self._son_gonderme = simdi

        x = 0
        y = 0
        tetik = 0

        if self.joystick:
            try:
                pygame.event.pump()

                # Joystick bağlantısını kontrol et
                try:
                    joystick_bagli = self.joystick.get_init()
                    if not joystick_bagli:
                        # Joystick bağlantısı kesilmiş
                        print("[Uyarı] Joystick bağlantısı kesildi")
                        self.joystick = None
                        self.isim = "Bağlantı Kesildi"
                        return f"{x},{y},{tetik},0,0", x, y
                except Exception:
                    # Joystick erişim hatası - bağlantı kesilmiş olabilir
                    print("[Uyarı] Joystick erişim hatası, bağlantı kesildi kabul ediliyor")
                    self.joystick = None
                    self.isim = "Bağlantı Kesildi"
                    return f"{x},{y},{tetik},0,0", x, y

                x_ham = self.joystick.get_axis(0)
                y_ham = self.joystick.get_axis(1)
                tetik = 1 if self.joystick.get_button(0) else 0

                if self.JOY_X_TERS:
                    x_ham = -x_ham

                if self.JOY_Y_TERS:
                    y_ham = -y_ham

                # Eksen takası:
                # Joystick Y -> Sistem X
                # Joystick X -> Sistem Y
                x = self._joystick_olcekle(y_ham)
                y = self._joystick_olcekle(x_ham)

            except Exception as e:
                print(f"[Uyarı] Joystick okuma hatası: {e}")
                x = 0
                y = 0
                tetik = 0

        # Klavye override
        if self.tuslar["W"]:
            x = 1000
        elif self.tuslar["S"]:
            x = -1000

        if self.tuslar["D"]:
            y = 1000
        elif self.tuslar["A"]:
            y = -1000

        self._anlik_x = x
        self._anlik_y = y
        self._anlik_tetik = tetik

        komut = f"{x},{y},{tetik},0,0"

        return komut, x, y

    def kapat(self):
        try:
            pygame.quit()
        except Exception:
            pass