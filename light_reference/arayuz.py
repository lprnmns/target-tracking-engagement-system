"""
arayuz.py - TAM SÜRÜM
24V / Acil Durdur entegrasyonu eklendi.
"""

import os
import atexit
import subprocess
import time
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import Canvas, Frame, Scrollbar

import cv2
import numpy as np
from PIL import Image, ImageTk

from goruntu_motoru import GoruntuMotoru
from pico_baglanti import PicoBaglanti
from manuel_kontrol import ManuelKontrol
from otonom_takip import OtonomTakip
from asama1_manual import Asama1Manual
from asama2_otonom import Asama2Otonom
from asama3_otonom import Asama3Otonom


class Arayuz:
    def __init__(self, kok):
        self.kok = kok
        self.kok.title("Teknofest Çelik Kubbe - Hava Savunma Sistemi")
        self.kok.geometry("1280x800")

        # ================= MODÜLLER =================
        self.motor = GoruntuMotoru(
            kamera_portu=1,
            genislik=1920,
            yukseklik=1080,
            engine_yolu="yolo11s.engine",
            conf_threshold=0.5,
            ikinci_model_yolu=None
        )
        self.motor.baslat()

        self.pico = PicoBaglanti(handler=self._pico_callback)
        self.manuel = ManuelKontrol()

        self.otonom = OtonomTakip(
            self.motor,
            self.pico,
            pid_dosya_yolu="pid_ayarlar.json"
        )

        self.asama1 = Asama1Manual(self.pico, self.motor)

        self.asama2 = Asama2Otonom(
            self.motor,
            self.pico,
            pid_dosya_yolu="asama2_pid_ayarlar.json"
        )

        self.asama3 = Asama3Otonom(
            self.motor,
            self.pico,
            pid_dosya_yolu="asama3_pid_ayarlar.json"
        )

        # ================= DURUM DEĞİŞKENLERİ =================
        self._gpu_kullanimi = "N/A"
        self._son_gpu_zamani = time.time()

        self._display_sayac = 0
        self._display_baslangic = time.time()
        self._display_fps_guncel = 0

        self._mod = "kapali"
        self._gosterilen_x_hiz = 0
        self._gosterilen_y_hiz = 0
        self._motor_modu = 1
        self._mikrostep_degeri = 8
        self._son_hb_zamani = 0.0

        # POZ telemetrisi
        self._motor_aci_x = 0.0
        self._motor_aci_y = 0.0
        self._son_poz_sorgu_zamani = 0.0

        # Dönüş limitleri
        self._donus_limit_min = 50.0
        self._donus_limit_max = 200.0
        self._limit_gonderildi = False

        # 24V / Acil durdur
        self._besleme_24v = True
        self._acil_durum_aktif = False

        self._son_tespitler = []
        self._son_oran_x = 1.0
        self._son_oran_y = 1.0
        self._gecmis_kutular = {}
        self._balon_model_yuklu_mu = False

        self._port_listesi = self.pico.portlari_bul()
        if not self._port_listesi:
            self._port_listesi = ["COM1", "COM2", "COM3"]

        # ================= ARAYÜZ KURULUMU =================
        self._arayuz_kur()
        self.kok.protocol("WM_DELETE_WINDOW", self.kapat)
        atexit.register(self._atexit_temizlik)

        self._guncelle()

    # ================= PICO CALLBACK =================
    def _pico_callback(self, tip, veri=None):
        try:
            self.kok.after(0, self._pico_isleyici, tip, veri)
        except Exception:
            pass

    def _pico_isleyici(self, tip, veri=None):
        if tip == "BAGLANTI_BASARILI":
            self.durum_etiketi.config(text="Bağlı", fg="green")
            self.baglan_buton.config(state="normal")

            self._komut_gonder("MODE,MANUAL")
            self._limit_gonder()

            if messagebox.askyesno(
                "AutoHome",
                "Bağlantı kuruldu.\n\nEksenleri sıfırlamak için AutoHome başlatılsın mı?"
            ):
                self._autohome_baslat()

        elif tip == "BAGLANTI_HATA":
            self.durum_etiketi.config(text="Bağlantı Hatası", fg="red")
            self.baglan_buton.config(state="normal")
            messagebox.showerror("Bağlantı Hatası", veri or "Bilinmeyen hata")

        elif tip == "BAGLANTI_KOPTU":
            self.durum_etiketi.config(text="Bağlantı koptu", fg="red")
            self.baglan_buton.config(state="normal")

            self._limit_gonderildi = False
            self._acil_durum_aktif = False
            self._acil_durum_guncelle()

            self._sistem_kapali()

        elif tip == "PICO_YANIT":
            line = (veri or "").strip()

            if line == "OK,HOME_DONE":
                if self._mod == "homing":
                    self.mod_etiketi.config(text="Merkeze Gidiyor...", bg="blue")

            elif line == "OK,CENTER_DONE":
                if self._mod == "homing":
                    self._mod = "manuel"
                    self._komut_gonder("MODE,MANUAL")
                    self._komut_gonder("MOTOR,ON")
                    self.mod_etiketi.config(text="SİSTEM HAZIR", bg="green")
                    self.kok.after(3000, lambda: self.mod_etiketi.config(text="Manuel Mod"))

            elif line in ("ERR,HOME_FAIL", "ERR,HOME_ABORT"):
                self._mod = "kapali"
                self.mod_etiketi.config(text="Sistem Kapalı", bg="red")
                messagebox.showerror(
                    "AutoHome",
                    "AutoHome başarısız oldu veya iptal edildi.\n\n"
                    "Kontrol: Limit switch bağlantıları ve pinleri."
                )

            elif line == "ERR,ACIL_DURDUR":
                self._besleme_24v = False
                self._acil_durum_aktif = True
                self._acil_durum_guncelle()

                try:
                    self._sistem_kapali()
                except Exception:
                    pass

                self.mod_etiketi.config(text="ACİL DURDUR", bg="red")

                messagebox.showwarning(
                    "Acil Durdur",
                    "24V besleme kesildi.\n\n"
                    "Muhtemelen acil durdur butonu aktif.\n"
                    "Sistem güvenli moda alındı."
                )

            elif line == "OK,ACIL_DURDUR_KALKTI":
                self._besleme_24v = True
                self._acil_durum_aktif = False
                self._acil_durum_guncelle()

                self.mod_etiketi.config(text="24V Geldi - AutoHome Gerekli", bg="orange")

                messagebox.showinfo(
                    "24V Geri Geldi",
                    "24V besleme geri geldi.\n\n"
                    "Motor pozisyonları güç kaybından dolayı güvenilir değil.\n"
                    "AutoHome yapmanız gerekiyor."
                )

            elif line == "OK,HOME_GEREKLI":
                self.mod_etiketi.config(text="AutoHome Gerekli", bg="orange")

            elif line.startswith("OK,USTEP_"):
                messagebox.showinfo(
                    "Mikrostep",
                    "Mikrostep değeri değişti.\n\n"
                    "Açı limitlerinin doğru ölçeklenmesi için AutoHome yapmanız önerilir."
                )

            elif line.startswith("OK,BALON_MODELI_"):
                if "AKTIF" in line:
                    self.balon_model_durum_label.config(text="Balon Modeli: Aktif", fg="green")
                elif "PASIF" in line:
                    self.balon_model_durum_label.config(text="Balon Modeli: Yüklenmedi", fg="red")

            elif line.startswith("OK,HB|"):
                try:
                    parts = [p.strip() for p in line.split("|")]

                    for part in parts[1:]:
                        if part.startswith("BALON_MODELI:"):
                            durum = part.split(":", 1)[1].strip()
                            if durum == "1":
                                self.balon_model_durum_label.config(text="Balon Modeli: Aktif", fg="green")
                            else:
                                self.balon_model_durum_label.config(text="Balon Modeli: Yüklenmedi", fg="red")

                        elif part.startswith("24V:"):
                            durum = part.split(":", 1)[1].strip()
                            self._besleme_24v = (durum == "1")

                        elif part.startswith("ACIL:"):
                            durum = part.split(":", 1)[1].strip()
                            yeni_acil_durum = (durum == "1")

                            if yeni_acil_durum != self._acil_durum_aktif:
                                self._acil_durum_aktif = yeni_acil_durum
                                self._acil_durum_guncelle()

                except Exception:
                    pass

            elif line.startswith(("OK,POZ,", "OK,HOME_POS,", "OK,CENTER_POS,")):
                try:
                    px = None
                    py = None

                    parts = line.split(",")

                    for part in parts[2:]:
                        if part.startswith("X:"):
                            px = float(part.split(":")[1])
                        elif part.startswith("Y:"):
                            py = float(part.split(":")[1])

                    if px is not None and py is not None:
                        self._pan_telemetri_isle(px, py)

                except Exception:
                    pass

            elif line == "OK,LIMIT_SET":
                self._limit_gonderildi = True
                try:
                    self.donus_limit_durum_label.config(
                        text=f"✓ Firmware: {self._donus_limit_min:.0f}°-{self._donus_limit_max:.0f}°",
                        fg="green"
                    )
                except Exception:
                    pass

            elif line == "ERR,LIMIT":
                try:
                    self.donus_limit_durum_label.config(text="✗ Limit Hatası", fg="red")
                except Exception:
                    pass

    # ================= ACİL DURDUR YARDIMCILARI =================
    def _acil_durum_guncelle(self):
        try:
            if self._acil_durum_aktif:
                self.acil_durum_label.config(
                    text="ACİL DURDUR AKTİF (24V YOK)",
                    fg="red"
                )

                if self._mod != "homing":
                    self.mod_etiketi.config(text="ACİL DURDUR", bg="red")
            else:
                self.acil_durum_label.config(
                    text="24V Aktif",
                    fg="green"
                )
        except Exception:
            pass

    def _acil_durum_kontrol(self):
        if getattr(self, "_acil_durum_aktif", False):
            messagebox.showerror(
                "Acil Durdur Aktif",
                "24V besleme yok / acil durdur aktif.\n\n"
                "Önce acil durdur butonunu kontrol edin.\n"
                "24V geri geldikten sonra AutoHome yapmanız gerekir."
            )
            return True

        return False

    # ================= ARAYÜZ KURULUMU =================
    def _arayuz_kur(self):
        ana_kutu = tk.Frame(self.kok)
        ana_kutu.pack(fill="both", expand=True)

        sag_panel = tk.Frame(ana_kutu, width=340, bg="lightgray")
        sag_panel.pack(side="right", fill="y", padx=5, pady=5)
        sag_panel.pack_propagate(False)

        sol_panel = tk.Frame(ana_kutu)
        sol_panel.pack(side="left", fill="both", expand=True)

        kamera_frame = tk.Frame(sol_panel)
        kamera_frame.pack(fill="both", expand=True)

        self.goruntu_etiketi = tk.Label(kamera_frame, cursor="crosshair")
        self.goruntu_etiketi.pack(padx=10, pady=(10, 5), fill="both", expand=True)
        self.goruntu_etiketi.bind("<Button-1>", self._goruntu_tiklama)

        # ================= AŞAMA BUTONLARI =================
        asama_buton_frame = tk.Frame(kamera_frame, bg="#e0e0e0")
        asama_buton_frame.pack(padx=10, pady=(0, 10), fill="x")

        tk.Label(
            asama_buton_frame,
            text="AŞAMA 1:",
            bg="#e0e0e0",
            font=("Arial", 9, "bold")
        ).pack(side="left", padx=(5, 5))

        self.asama1_baslat_buton_ana = tk.Button(
            asama_buton_frame,
            text="▶ Başlat",
            command=self._asama1_baslat,
            bg="green",
            fg="white",
            font=("Arial", 9, "bold"),
            width=8,
            height=1
        )
        self.asama1_baslat_buton_ana.pack(side="left", padx=2)

        self.asama1_durdur_buton_ana = tk.Button(
            asama_buton_frame,
            text="⏹ Durdur",
            command=self._asama1_durdur,
            bg="red",
            fg="white",
            font=("Arial", 9, "bold"),
            width=8,
            height=1,
            state="disabled"
        )
        self.asama1_durdur_buton_ana.pack(side="left", padx=2)

        self.balon_patlat_buton = tk.Button(
            asama_buton_frame,
            text="🎈 PATLAT",
            command=self._balon_patlat,
            bg="darkred",
            fg="white",
            font=("Arial", 9, "bold"),
            width=10,
            height=1
        )
        self.balon_patlat_buton.pack(side="left", padx=8)

        tk.Label(asama_buton_frame, text="|", bg="#e0e0e0").pack(side="left", padx=8)

        tk.Label(
            asama_buton_frame,
            text="AŞAMA 2:",
            bg="#e0e0e0",
            font=("Arial", 9, "bold")
        ).pack(side="left", padx=(0, 5))

        self.asama2_baslat_buton_ana = tk.Button(
            asama_buton_frame,
            text="▶ Başlat",
            command=self._asama2_baslat,
            bg="blue",
            fg="white",
            font=("Arial", 9, "bold"),
            width=8,
            height=1
        )
        self.asama2_baslat_buton_ana.pack(side="left", padx=2)

        self.asama2_durdur_buton_ana = tk.Button(
            asama_buton_frame,
            text="⏹ Durdur",
            command=self._asama2_durdur,
            bg="orange",
            fg="white",
            font=("Arial", 9, "bold"),
            width=8,
            height=1,
            state="disabled"
        )
        self.asama2_durdur_buton_ana.pack(side="left", padx=2)

        tk.Label(asama_buton_frame, text="|", bg="#e0e0e0").pack(side="left", padx=5)

        tk.Label(
            asama_buton_frame,
            text="AŞAMA 3:",
            bg="#e0e0e0",
            font=("Arial", 9, "bold")
        ).pack(side="left", padx=(0, 5))

        self.asama3_baslat_buton_ana = tk.Button(
            asama_buton_frame,
            text="▶ Başlat",
            command=self._asama3_baslat,
            bg="purple",
            fg="white",
            font=("Arial", 9, "bold"),
            width=8,
            height=1
        )
        self.asama3_baslat_buton_ana.pack(side="left", padx=2)

        self.asama3_durdur_buton_ana = tk.Button(
            asama_buton_frame,
            text="⏹ Durdur",
            command=self._asama3_durdur,
            bg="darkred",
            fg="white",
            font=("Arial", 9, "bold"),
            width=8,
            height=1,
            state="disabled"
        )
        self.asama3_durdur_buton_ana.pack(side="left", padx=2)

        self.servo_durum_label_ana = tk.Label(
            kamera_frame,
            text="",
            bg="#f0f0f0",
            font=("Arial", 9)
        )
        self.servo_durum_label_ana.pack(pady=(0, 5))

        # ================= SEKMELER =================
        notebook = ttk.Notebook(sag_panel)
        notebook.pack(fill="both", expand=True, padx=2, pady=2)

        sekme_durum = tk.Frame(notebook, bg="lightgray")
        sekme_kontrol = tk.Frame(notebook, bg="lightgray")
        sekme_servo = tk.Frame(notebook, bg="lightgray")
        sekme_pid = tk.Frame(notebook, bg="lightgray")
        sekme_asama2 = tk.Frame(notebook, bg="lightgray")
        sekme_asama3 = tk.Frame(notebook, bg="lightgray")

        notebook.add(sekme_durum, text="Durum")
        notebook.add(sekme_kontrol, text="Kontrol Paneli")
        notebook.add(sekme_servo, text="Servo Kontrol")
        notebook.add(sekme_pid, text="PID Ayarları")
        notebook.add(sekme_asama2, text="Aşama 2 Kontrol")
        notebook.add(sekme_asama3, text="Aşama 3 Kontrol")

        self._asama2_sekmesi_doldur(sekme_asama2)
        self._asama3_sekmesi_doldur(sekme_asama3)

        # ================= DURUM SEKMESİ =================
        tk.Label(
            sekme_durum,
            text="Sistem Durumu",
            bg="lightgray",
            font=("Arial", 12, "bold")
        ).pack(pady=10)

        tk.Label(sekme_durum, text="Bağlantı:", bg="lightgray").pack(anchor="w", padx=10)

        self.durum_etiketi = tk.Label(
            sekme_durum,
            text="Bağlantı Kapalı",
            bg="lightgray",
            fg="red",
            font=("Arial", 10, "bold")
        )
        self.durum_etiketi.pack(pady=2)

        tk.Label(
            sekme_durum,
            text="Sistem Modu:",
            bg="lightgray"
        ).pack(anchor="w", padx=10, pady=(10, 0))

        self.mod_etiketi = tk.Label(
            sekme_durum,
            text="Sistem Kapalı",
            bg="red",
            fg="white",
            font=("Arial", 11, "bold")
        )
        self.mod_etiketi.pack(pady=5)

        tk.Label(
            sekme_durum,
            text="Motor Kontrol",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(10, 5))

        mod_frame = tk.Frame(sekme_durum, bg="lightgray")
        mod_frame.pack(pady=5)

        tk.Button(mod_frame, text="Kapalı", command=self._sistem_kapali, width=8).pack(side="left", padx=2)
        tk.Button(mod_frame, text="Manuel", command=self._manuel_mod_ac, width=8).pack(side="left", padx=2)
        tk.Button(mod_frame, text="Otonom", command=self._otonom_mod_ac, bg="lightblue", width=8).pack(side="left", padx=2)

        tk.Button(sekme_durum, text="AutoHome", command=self._autohome_baslat).pack(pady=5)

        tk.Label(sekme_durum, text="Hız Modu:", bg="lightgray").pack(anchor="w", padx=10, pady=(5, 0))

        self.hiz_modu_combobox = ttk.Combobox(
            sekme_durum,
            values=["Hızlı", "Hassas"],
            state="readonly",
            width=12
        )
        self.hiz_modu_combobox.set("Hızlı")
        self.hiz_modu_combobox.pack(padx=10, anchor="w")
        self.hiz_modu_combobox.bind("<<ComboboxSelected>>", self._hiz_modu_degistir)

        tk.Label(sekme_durum, text="Mikrostep:", bg="lightgray").pack(anchor="w", padx=10, pady=(5, 0))

        self.mikrostep_combobox = ttk.Combobox(
            sekme_durum,
            values=[1, 2, 4, 8, 16, 64],
            state="readonly",
            width=12
        )
        self.mikrostep_combobox.set(self._mikrostep_degeri)
        self.mikrostep_combobox.pack(padx=10, anchor="w")
        self.mikrostep_combobox.bind("<<ComboboxSelected>>", self._mikrostep_degistir)

        # ================= DÖNÜŞ LİMİTLERİ =================
        tk.Label(
            sekme_durum,
            text="Dönüş Limitleri (Yasak Bölge)",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(15, 5))

        tk.Label(
            sekme_durum,
            text="Belirtilen aralık DIŞI yasaklanır.\nManuel, Aşama 1/2/3, Otonom - tüm modlarda geçerli.",
            bg="lightgray",
            font=("Arial", 8),
            fg="gray"
        ).pack(pady=(0, 5))

        donus_frame = tk.Frame(sekme_durum, bg="lightgray")
        donus_frame.pack(pady=2)

        tk.Label(donus_frame, text="Min (°):", bg="lightgray", font=("Arial", 9)).pack(side="left", padx=2)

        self.donus_min_spin = tk.Spinbox(
            donus_frame,
            from_=0,
            to=270,
            increment=5,
            width=6,
            command=self._donus_limit_degisti
        )
        self.donus_min_spin.pack(side="left", padx=2)
        self.donus_min_spin.delete(0, tk.END)
        self.donus_min_spin.insert(0, str(int(self._donus_limit_min)))
        self.donus_min_spin.bind("<KeyRelease>", self._donus_limit_degisti)
        self.donus_min_spin.bind("<FocusOut>", self._donus_limit_degisti)

        tk.Label(donus_frame, text="Max (°):", bg="lightgray", font=("Arial", 9)).pack(side="left", padx=(10, 2))

        self.donus_max_spin = tk.Spinbox(
            donus_frame,
            from_=0,
            to=270,
            increment=5,
            width=6,
            command=self._donus_limit_degisti
        )
        self.donus_max_spin.pack(side="left", padx=2)
        self.donus_max_spin.delete(0, tk.END)
        self.donus_max_spin.insert(0, str(int(self._donus_limit_max)))
        self.donus_max_spin.bind("<KeyRelease>", self._donus_limit_degisti)
        self.donus_max_spin.bind("<FocusOut>", self._donus_limit_degisti)

        tk.Button(
            sekme_durum,
            text="📤 Firmware'e Gönder",
            command=self._limit_gonder,
            bg="darkblue",
            fg="white",
            font=("Arial", 9, "bold"),
            width=18
        ).pack(pady=5)

        self.donus_limit_durum_label = tk.Label(
            sekme_durum,
            text="Bekleniyor...",
            bg="lightgray",
            font=("Arial", 8, "bold"),
            fg="gray"
        )
        self.donus_limit_durum_label.pack(pady=2)

        tk.Label(sekme_durum, text="Joystick:", bg="lightgray").pack(anchor="w", padx=10, pady=(10, 0))

        renk = "green" if self.manuel.joystick else "red"

        self.joy_durum_etiketi = tk.Label(
            sekme_durum,
            text=self.manuel.isim,
            bg="lightgray",
            fg=renk,
            font=("Arial", 9, "bold")
        )
        self.joy_durum_etiketi.pack(pady=2)

        self.joy_x_etiketi = tk.Label(sekme_durum, text="X Ekseni: 0", bg="lightgray")
        self.joy_x_etiketi.pack(pady=2)

        self.joy_y_etiketi = tk.Label(sekme_durum, text="Y Ekseni: 0", bg="lightgray")
        self.joy_y_etiketi.pack(pady=2)

        # Balon Modeli Durumu
        tk.Label(sekme_durum, text="Balon Modeli:", bg="lightgray").pack(anchor="w", padx=10, pady=(10, 0))

        self.balon_model_durum_label = tk.Label(
            sekme_durum,
            text="Balon Modeli: Yüklenmedi",
            bg="lightgray",
            font=("Arial", 9)
        )
        self.balon_model_durum_label.pack(pady=2)

        balon_btn_frame = tk.Frame(sekme_durum, bg="lightgray")
        balon_btn_frame.pack(pady=5)

        self.balon_aktif_buton = tk.Button(
            balon_btn_frame,
            text="Balon Modeli Aktif",
            command=self._balon_model_aktif,
            bg="green",
            fg="white",
            width=14
        )
        self.balon_aktif_buton.pack(side="left", padx=2)

        self.balon_pasif_buton = tk.Button(
            balon_btn_frame,
            text="Balon Modeli Pasif",
            command=self._balon_model_pasif,
            bg="red",
            fg="white",
            width=14
        )
        self.balon_pasif_buton.pack(side="left", padx=2)

        # 24V / Acil Durdur Durumu
        tk.Label(
            sekme_durum,
            text="24V / Acil Durdur:",
            bg="lightgray"
        ).pack(anchor="w", padx=10, pady=(10, 0))

        self.acil_durum_label = tk.Label(
            sekme_durum,
            text="24V Aktif",
            bg="lightgray",
            fg="green",
            font=("Arial", 10, "bold")
        )
        self.acil_durum_label.pack(pady=2)

        # Hedef Model Durumu
        tk.Label(sekme_durum, text="Hedef Model:", bg="lightgray").pack(anchor="w", padx=10, pady=(10, 0))

        self.hedef_model_durum_label = tk.Label(
            sekme_durum,
            text="Hedef Model: Bekleniyor",
            bg="lightgray",
            font=("Arial", 9)
        )
        self.hedef_model_durum_label.pack(pady=2)

        hedef_btn_frame = tk.Frame(sekme_durum, bg="lightgray")
        hedef_btn_frame.pack(pady=5)

        self.hedef_aktif_buton = tk.Button(
            hedef_btn_frame,
            text="Hedef Model Aktif",
            command=self._hedef_model_aktif,
            bg="green",
            fg="white",
            width=14
        )
        self.hedef_aktif_buton.pack(side="left", padx=2)

        self.hedef_pasif_buton = tk.Button(
            hedef_btn_frame,
            text="Hedef Model Pasif",
            command=self._hedef_model_pasif,
            bg="red",
            fg="white",
            width=14
        )
        self.hedef_pasif_buton.pack(side="left", padx=2)

        tk.Label(
            sekme_durum,
            text="Aşama Durumu",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(15, 5))

        self.asama_durum_label = tk.Label(
            sekme_durum,
            text="Durum: Beklemede",
            bg="lightgray",
            font=("Arial", 10, "bold"),
            fg="black"
        )
        self.asama_durum_label.pack(pady=5)

        # ================= KONTROL PANELİ SEKMESİ =================
        tk.Label(
            sekme_kontrol,
            text="Pico Bağlantısı",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(10, 5))

        tk.Label(sekme_kontrol, text="COM Port:", bg="lightgray").pack(anchor="w", padx=10)

        com_frame = tk.Frame(sekme_kontrol, bg="lightgray")
        com_frame.pack(pady=2, padx=10, anchor="w")

        self.com_port_combobox = ttk.Combobox(
            com_frame,
            values=self._port_listesi,
            state="readonly",
            width=10
        )

        if self._port_listesi:
            self.com_port_combobox.set(self._port_listesi[0])

        self.com_port_combobox.pack(side="left", padx=2)

        tk.Button(
            com_frame,
            text="⟳",
            width=3,
            command=self._port_listesi_yenile
        ).pack(side="left", padx=2)

        baglan_kes_frame = tk.Frame(sekme_kontrol, bg="lightgray")
        baglan_kes_frame.pack(pady=5)

        self.baglan_buton = tk.Button(
            baglan_kes_frame,
            text="Bağlan",
            command=self._seri_baglan
        )
        self.baglan_buton.pack(side="left", padx=2)

        tk.Button(
            baglan_kes_frame,
            text="Kes",
            command=self._seri_kes
        ).pack(side="left", padx=2)

        tk.Label(
            sekme_kontrol,
            text="Kamera Ayarları",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(10, 5))

        tk.Label(sekme_kontrol, text="Kamera Portu:", bg="lightgray").pack(anchor="w", padx=10)

        self.port_dropdown = ttk.Combobox(
            sekme_kontrol,
            values=[0, 1, 2],
            state="readonly",
            width=10
        )
        self.port_dropdown.set(1)
        self.port_dropdown.pack(padx=10, anchor="w")

        tk.Label(sekme_kontrol, text="Çözünürlük:", bg="lightgray").pack(anchor="w", padx=10, pady=(5, 0))

        self.cozunurluk_dropdown = ttk.Combobox(
            sekme_kontrol,
            values=["640x480", "1280x720", "1920x1080"],
            state="readonly",
            width=14
        )
        self.cozunurluk_dropdown.set("1920x1080")
        self.cozunurluk_dropdown.pack(padx=10, anchor="w")

        tk.Button(
            sekme_kontrol,
            text="UYGULA",
            command=self._ayarlari_uygula
        ).pack(pady=5)

        tk.Label(
            sekme_kontrol,
            text="Model Ayarları",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(10, 5))

        tk.Button(
            sekme_kontrol,
            text="Hedef Modeli Seç",
            command=self._model_sec
        ).pack(pady=2)

        tk.Label(
            sekme_kontrol,
            text="Doğruluk Eşiği (Confidence):",
            bg="lightgray"
        ).pack(anchor="w", padx=10, pady=(5, 0))

        conf_frame = tk.Frame(sekme_kontrol, bg="lightgray")
        conf_frame.pack(fill="x", padx=10, pady=2)

        self.conf_scale = tk.Scale(
            conf_frame,
            from_=0.05,
            to=0.95,
            resolution=0.05,
            orient="horizontal",
            command=self._conf_degisti,
            bg="lightgray",
            length=180
        )
        self.conf_scale.set(0.5)
        self.conf_scale.pack(side="left", fill="x", expand=True)

        self.conf_deger_label = tk.Label(conf_frame, text="0.50", bg="lightgray", width=5)
        self.conf_deger_label.pack(side="left", padx=5)

        tk.Label(
            sekme_kontrol,
            text="Balon Model Ayarları:",
            bg="lightgray",
            font=("Arial", 10, "bold")
        ).pack(pady=(10, 5))

        tk.Button(
            sekme_kontrol,
            text="Balon Modeli Seç",
            command=self._ikinci_model_sec
        ).pack(pady=2)

        tk.Label(
            sekme_kontrol,
            text="Balon Confidence:",
            bg="lightgray"
        ).pack(anchor="w", padx=10, pady=(5, 0))

        balon_conf_frame = tk.Frame(sekme_kontrol, bg="lightgray")
        balon_conf_frame.pack(fill="x", padx=10, pady=2)

        self.balon_conf_scale = tk.Scale(
            balon_conf_frame,
            from_=0.05,
            to=0.95,
            resolution=0.05,
            orient="horizontal",
            command=self._balon_conf_degisti,
            bg="lightgray",
            length=180
        )
        self.balon_conf_scale.set(0.5)
        self.balon_conf_scale.pack(side="left", fill="x", expand=True)

        self.balon_conf_deger_label = tk.Label(balon_conf_frame, text="0.50", bg="lightgray", width=5)
        self.balon_conf_deger_label.pack(side="left", padx=5)

        # ================= SERVO KONTROL SEKMESİ =================
        tk.Label(
            sekme_servo,
            text="Servo Kontrol Parametreleri",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(15, 5))

        tk.Label(
            sekme_servo,
            text="Başlangıç Açısı (0-180):",
            bg="lightgray"
        ).pack(anchor="w", padx=10)

        servo_bas_frame_servo = tk.Frame(sekme_servo, bg="lightgray")
        servo_bas_frame_servo.pack(fill="x", padx=10, pady=2)

        self.servo_bas_scale_servo = tk.Scale(
            servo_bas_frame_servo,
            from_=0,
            to=180,
            resolution=1,
            orient="horizontal",
            command=self._servo_bas_degisti,
            bg="lightgray",
            length=220
        )
        self.servo_bas_scale_servo.set(0)
        self.servo_bas_scale_servo.pack(side="left", fill="x", expand=True)

        self.servo_bas_deger_label_servo = tk.Label(
            servo_bas_frame_servo,
            text="0°",
            bg="lightgray",
            width=6
        )
        self.servo_bas_deger_label_servo.pack(side="left", padx=5)

        tk.Label(
            sekme_servo,
            text="Bitiş Açısı (0-180):",
            bg="lightgray"
        ).pack(anchor="w", padx=10, pady=(5, 0))

        servo_bit_frame_servo = tk.Frame(sekme_servo, bg="lightgray")
        servo_bit_frame_servo.pack(fill="x", padx=10, pady=2)

        self.servo_bit_scale_servo = tk.Scale(
            servo_bit_frame_servo,
            from_=0,
            to=180,
            resolution=1,
            orient="horizontal",
            command=self._servo_bit_degisti,
            bg="lightgray",
            length=220
        )
        self.servo_bit_scale_servo.set(120)
        self.servo_bit_scale_servo.pack(side="left", fill="x", expand=True)

        self.servo_bit_deger_label_servo = tk.Label(
            servo_bit_frame_servo,
            text="120°",
            bg="lightgray",
            width=6
        )
        self.servo_bit_deger_label_servo.pack(side="left", padx=5)

        tk.Label(
            sekme_servo,
            text="Bekleme Süresi (ms):",
            bg="lightgray"
        ).pack(anchor="w", padx=10, pady=(5, 0))

        servo_bekle_frame_servo = tk.Frame(sekme_servo, bg="lightgray")
        servo_bekle_frame_servo.pack(fill="x", padx=10, pady=2)

        self.servo_bekle_scale_servo = tk.Scale(
            servo_bekle_frame_servo,
            from_=50,
            to=1000,
            resolution=25,
            orient="horizontal",
            command=self._servo_bekle_degisti,
            bg="lightgray",
            length=220
        )
        self.servo_bekle_scale_servo.set(100)
        self.servo_bekle_scale_servo.pack(side="left", fill="x", expand=True)

        self.servo_bekle_deger_label_servo = tk.Label(
            servo_bekle_frame_servo,
            text="100ms",
            bg="lightgray",
            width=6
        )
        self.servo_bekle_deger_label_servo.pack(side="left", padx=5)

        self.balon_patlat_buton_servo = tk.Button(
            sekme_servo,
            text="🎈 BALON PATLAT",
            command=self._balon_patlat,
            bg="red",
            fg="white",
            font=("Arial", 14, "bold"),
            height=2
        )
        self.balon_patlat_buton_servo.pack(pady=15, fill="x", padx=20)

        self.servo_durum_label_servo = tk.Label(
            sekme_servo,
            text="",
            bg="lightgray",
            font=("Arial", 9)
        )
        self.servo_durum_label_servo.pack(pady=2)

        # ================= PID AYARLARI SEKMESİ =================
        tk.Label(
            sekme_pid,
            text="PID Ayarları (Otonom Takip)",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=(15, 5))

        tk.Label(sekme_pid, text="X Ekseni:", bg="lightgray").pack(anchor="w", padx=10)

        pid_x_frame_pid = tk.Frame(sekme_pid, bg="lightgray")
        pid_x_frame_pid.pack(pady=2)

        tk.Label(pid_x_frame_pid, text="Kp:", bg="lightgray").pack(side="left", padx=2)
        self.pid_kp_x_pid = tk.Entry(pid_x_frame_pid, width=6, justify="center")
        self.pid_kp_x_pid.pack(side="left", padx=2)

        tk.Label(pid_x_frame_pid, text="Ki:", bg="lightgray").pack(side="left", padx=2)
        self.pid_ki_x_pid = tk.Entry(pid_x_frame_pid, width=6, justify="center")
        self.pid_ki_x_pid.pack(side="left", padx=2)

        tk.Label(pid_x_frame_pid, text="Kd:", bg="lightgray").pack(side="left", padx=2)
        self.pid_kd_x_pid = tk.Entry(pid_x_frame_pid, width=6, justify="center")
        self.pid_kd_x_pid.pack(side="left", padx=2)

        tk.Label(sekme_pid, text="Y Ekseni:", bg="lightgray").pack(anchor="w", padx=10, pady=(10, 0))

        pid_y_frame_pid = tk.Frame(sekme_pid, bg="lightgray")
        pid_y_frame_pid.pack(pady=2)

        tk.Label(pid_y_frame_pid, text="Kp:", bg="lightgray").pack(side="left", padx=2)
        self.pid_kp_y_pid = tk.Entry(pid_y_frame_pid, width=6, justify="center")
        self.pid_kp_y_pid.pack(side="left", padx=2)

        tk.Label(pid_y_frame_pid, text="Ki:", bg="lightgray").pack(side="left", padx=2)
        self.pid_ki_y_pid = tk.Entry(pid_y_frame_pid, width=6, justify="center")
        self.pid_ki_y_pid.pack(side="left", padx=2)

        tk.Label(pid_y_frame_pid, text="Kd:", bg="lightgray").pack(side="left", padx=2)
        self.pid_kd_y_pid = tk.Entry(pid_y_frame_pid, width=6, justify="center")
        self.pid_kd_y_pid.pack(side="left", padx=2)

        pid_btn_frame = tk.Frame(sekme_pid, bg="lightgray")
        pid_btn_frame.pack(pady=15)

        tk.Button(pid_btn_frame, text="Sıfırla", command=self._pid_sifirla, width=10).pack(side="left", padx=5)
        tk.Button(pid_btn_frame, text="Kaydet", command=self._pid_ayarlari_kaydet, width=10).pack(side="left", padx=5)

        self.pid_durum_label_pid = tk.Label(sekme_pid, text="", bg="lightgray", font=("Arial", 9))
        self.pid_durum_label_pid.pack(pady=2)

        self._pid_entryleri_doldur()
        self._pid_event_bagla()

        # ================= KLAVYE =================
        self.kok.bind('<KeyPress-w>', lambda e: self.manuel.tus_basla('W'))
        self.kok.bind('<KeyPress-s>', lambda e: self.manuel.tus_basla('S'))
        self.kok.bind('<KeyPress-a>', lambda e: self.manuel.tus_basla('A'))
        self.kok.bind('<KeyPress-d>', lambda e: self.manuel.tus_basla('D'))

        self.kok.bind('<KeyRelease-w>', lambda e: self.manuel.tus_birak('W'))
        self.kok.bind('<KeyRelease-s>', lambda e: self.manuel.tus_birak('S'))
        self.kok.bind('<KeyRelease-a>', lambda e: self.manuel.tus_birak('A'))
        self.kok.bind('<KeyRelease-d>', lambda e: self.manuel.tus_birak('D'))

    # ================= SCROLL YARDIMCISI =================
    def _canvas_scroll_bagla(self, canvas):
        def _on_enter(event):
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        def _on_leave(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _on_enter)
        canvas.bind("<Leave>", _on_leave)

    # ================= AŞAMA 2 KONTROL SEKMESİ =================
    def _asama2_sekmesi_doldur(self, sekme):
        asama2_canvas = Canvas(sekme, bg="lightgray", highlightthickness=0)
        asama2_scrollbar = Scrollbar(sekme, orient="vertical", command=asama2_canvas.yview)

        self.asama2_scroll_frame = Frame(asama2_canvas, bg="lightgray")
        self.asama2_scroll_frame.bind(
            "<Configure>",
            lambda e: asama2_canvas.configure(scrollregion=asama2_canvas.bbox("all"))
        )

        asama2_canvas.create_window((0, 0), window=self.asama2_scroll_frame, anchor="nw")
        asama2_canvas.configure(yscrollcommand=asama2_scrollbar.set)

        asama2_scrollbar.pack(side="right", fill="y")
        asama2_canvas.pack(side="left", fill="both", expand=True)

        self._canvas_scroll_bagla(asama2_canvas)

        tk.Label(
            self.asama2_scroll_frame,
            text="Aşama 2: Gelişmiş Kontrol Paneli",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=5)

        p = self.asama2.parametreleri_al()

        pid_x_frame = tk.LabelFrame(
            self.asama2_scroll_frame,
            text="X Ekseni PID (Pan)",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        pid_x_frame.pack(fill="x", padx=6, pady=3)

        self.asama2_kpx_var = tk.StringVar(value=f"{p.get('kp_x', 11.0):.1f}")
        self.asama2_kdx_var = tk.StringVar(value=f"{p.get('kd_x', 0.8):.1f}")
        self.asama2_kix_var = tk.StringVar(value=f"{p.get('ki_x', 0.00):.2f}")

        self._spin_satir(pid_x_frame, "Kp X (Pan):", self.asama2_kpx_var, 0, 100, 1.0)
        self._spin_satir(pid_x_frame, "Kd X (Pan):", self.asama2_kdx_var, 0, 50, 0.1)
        self._spin_satir(pid_x_frame, "Ki X (Pan):", self.asama2_kix_var, 0, 10, 0.01)

        pid_y_frame = tk.LabelFrame(
            self.asama2_scroll_frame,
            text="Y Ekseni PID (Tilt)",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        pid_y_frame.pack(fill="x", padx=6, pady=3)

        self.asama2_kpy_var = tk.StringVar(value=f"{p.get('kp_y', 18.0):.1f}")
        self.asama2_kdy_var = tk.StringVar(value=f"{p.get('kd_y', 4.0):.1f}")
        self.asama2_kiy_var = tk.StringVar(value=f"{p.get('ki_y', 0.00):.2f}")

        self._spin_satir(pid_y_frame, "Kp Y (Tilt):", self.asama2_kpy_var, 0, 100, 1.0)
        self._spin_satir(pid_y_frame, "Kd Y (Tilt):", self.asama2_kdy_var, 0, 50, 0.5)
        self._spin_satir(pid_y_frame, "Ki Y (Tilt):", self.asama2_kiy_var, 0, 10, 0.01)

        lead_frame = tk.LabelFrame(
            self.asama2_scroll_frame,
            text="Lead Point (Öngörü Noktası) Ayarları",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        lead_frame.pack(fill="x", padx=6, pady=3)

        self.asama2_lead_oran_var = tk.StringVar(value=f"{p.get('lead_kayma_orani', 140.0):.0f}")
        self.asama2_lead_esik_var = tk.StringVar(value=f"{p.get('lead_esik', 8.5):.1f}")
        self.asama2_lead_filtre_var = tk.StringVar(value=f"{p.get('lead_filtre', 0.25):.2f}")

        self._spin_satir(lead_frame, "Maks. Kayma (%):", self.asama2_lead_oran_var, 0, 250, 5)
        self._spin_satir(lead_frame, "Hız Eşiği (px/s):", self.asama2_lead_esik_var, 0.5, 30.0, 0.5)
        self._spin_satir(lead_frame, "Lead Filtre (Alpha):", self.asama2_lead_filtre_var, 0.05, 1.0, 0.05)

        boyut_frame = tk.LabelFrame(
            self.asama2_scroll_frame,
            text="Boyuta Göre Kayma Kazancı",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        boyut_frame.pack(fill="x", padx=6, pady=3)

        self.asama2_kutu_esik1_var = tk.StringVar(value=f"{p.get('kutu_esik_1', 100.0):.0f}")
        self.asama2_kazanc1_var = tk.StringVar(value=f"{p.get('kazanc_1', 0.05):.3f}")
        self.asama2_kutu_esik2_var = tk.StringVar(value=f"{p.get('kutu_esik_2', 200.0):.0f}")
        self.asama2_kazanc2_var = tk.StringVar(value=f"{p.get('kazanc_2', 0.03):.3f}")
        self.asama2_kutu_esik3_var = tk.StringVar(value=f"{p.get('kutu_esik_3', 300.0):.0f}")
        self.asama2_kazanc3_var = tk.StringVar(value=f"{p.get('kazanc_3', 0.02):.3f}")
        self.asama2_kazanc4_var = tk.StringVar(value=f"{p.get('kazanc_4', 0.01):.3f}")

        self._spin_satir(boyut_frame, "Eşik 1 (px, ≤):", self.asama2_kutu_esik1_var, 20, 800, 10)
        self._spin_satir(boyut_frame, "Kazanç 1 (≤E1):", self.asama2_kazanc1_var, 0.001, 2.0, 0.005)
        self._spin_satir(boyut_frame, "Eşik 2 (px):", self.asama2_kutu_esik2_var, 40, 900, 10)
        self._spin_satir(boyut_frame, "Kazanç 2 (E1-E2):", self.asama2_kazanc2_var, 0.001, 2.0, 0.005)
        self._spin_satir(boyut_frame, "Eşik 3 (px):", self.asama2_kutu_esik3_var, 60, 1000, 10)
        self._spin_satir(boyut_frame, "Kazanç 3 (E2-E3):", self.asama2_kazanc3_var, 0.001, 2.0, 0.005)
        self._spin_satir(boyut_frame, "Kazanç 4 (>E3):", self.asama2_kazanc4_var, 0.001, 2.0, 0.005)

        tarama_frame = tk.LabelFrame(
            self.asama2_scroll_frame,
            text="Tarama & Ateş Toleransı",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        tarama_frame.pack(fill="x", padx=6, pady=3)

        self.asama2_pan_min_var = tk.StringVar(value=f"{p.get('tarama_min_derece', 90.0):.0f}")
        self.asama2_pan_max_var = tk.StringVar(value=f"{p.get('tarama_max_derece', 180.0):.0f}")
        self.asama2_hiz_var = tk.StringVar(value=f"{p.get('tarama_hizi', 10.0):.0f}")
        self.asama2_ates_oran_var = tk.StringVar(value=f"{p.get('ates_tolerans_oran', 15.0):.0f}")
        self.asama2_ates_min_var = tk.StringVar(value=f"{p.get('ates_tolerans_min_px', 10.0):.0f}")
        self.asama2_ates_onay_var = tk.StringVar(value=f"{p.get('ates_onay_suresi', 0.35):.2f}")

        self._spin_satir(tarama_frame, "Tarama Min (°):", self.asama2_pan_min_var, 0, 270, 5)
        self._spin_satir(tarama_frame, "Tarama Max (°):", self.asama2_pan_max_var, 0, 270, 5)
        self._spin_satir(tarama_frame, "Tarama Hızı (°/s):", self.asama2_hiz_var, 5, 120, 5)
        self._spin_satir(tarama_frame, "Ateş Toleransı (%):", self.asama2_ates_oran_var, 5, 100, 5)
        self._spin_satir(tarama_frame, "Min Tolerans (px):", self.asama2_ates_min_var, 2, 60, 2)
        self._spin_satir(tarama_frame, "Kilit Süresi (s):", self.asama2_ates_onay_var, 0.05, 3.0, 0.05)

        donus_frame_a2 = tk.LabelFrame(
            self.asama2_scroll_frame,
            text="Dönüş Limitleri (Yasak Bölge)",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        donus_frame_a2.pack(fill="x", padx=6, pady=3)

        self.asama2_donus_min_var = tk.StringVar(value=f"{p.get('donus_min_derece', 50.0):.0f}")
        self.asama2_donus_max_var = tk.StringVar(value=f"{p.get('donus_max_derece', 200.0):.0f}")

        self._spin_satir(donus_frame_a2, "Dönüş Min (°):", self.asama2_donus_min_var, 0, 270, 5)
        self._spin_satir(donus_frame_a2, "Dönüş Max (°):", self.asama2_donus_max_var, 0, 270, 5)

        durum_frame = tk.LabelFrame(
            self.asama2_scroll_frame,
            text="Canlı Durum",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        durum_frame.pack(fill="x", padx=6, pady=4)

        self.asama2_durum_label = tk.Label(
            durum_frame,
            text="⏸️ Beklemede",
            bg="lightgray",
            font=("Arial", 10, "bold"),
            fg="orange"
        )
        self.asama2_durum_label.pack(pady=4)

        btn_f = tk.Frame(self.asama2_scroll_frame, bg="lightgray")
        btn_f.pack(pady=6)

        tk.Button(
            btn_f,
            text="🔥 Test Ateşi",
            command=self._asama2_test_atesi,
            bg="orange",
            fg="white",
            font=("Arial", 9, "bold"),
            width=11
        ).pack(side="left", padx=2)

        tk.Button(
            btn_f,
            text="💾 Kaydet",
            command=self._asama2_ayarlari_kaydet,
            bg="darkgreen",
            fg="white",
            font=("Arial", 9, "bold"),
            width=11
        ).pack(side="left", padx=2)

        self.asama2_kayit_label = tk.Label(
            self.asama2_scroll_frame,
            text="",
            bg="lightgray",
            font=("Arial", 8, "bold")
        )
        self.asama2_kayit_label.pack(pady=(0, 4))

        self._asama2_parametre_guncelle()

    def _spin_satir(self, parent, text, var, f, t, inc):
        row = tk.Frame(parent, bg="lightgray")
        row.pack(fill="x", padx=5, pady=2)

        tk.Label(row, text=text, bg="lightgray", width=18, anchor="w", font=("Arial", 8)).pack(side="left")

        sp = tk.Spinbox(
            row,
            from_=f,
            to=t,
            increment=inc,
            width=8,
            textvariable=var,
            command=self._asama2_parametre_guncelle
        )
        sp.pack(side="left", padx=2)

        sp.bind("<KeyRelease>", self._asama2_parametre_guncelle)
        sp.bind("<FocusOut>", self._asama2_parametre_guncelle)

    def _asama2_parametre_guncelle(self, event=None):
        try:
            parametreler = {
                "kp_x": float(self.asama2_kpx_var.get()),
                "kd_x": float(self.asama2_kdx_var.get()),
                "ki_x": float(self.asama2_kix_var.get()),

                "kp_y": float(self.asama2_kpy_var.get()),
                "kd_y": float(self.asama2_kdy_var.get()),
                "ki_y": float(self.asama2_kiy_var.get()),

                "lead_kayma_orani": float(self.asama2_lead_oran_var.get()),
                "lead_esik": float(self.asama2_lead_esik_var.get()),
                "lead_filtre": float(self.asama2_lead_filtre_var.get()),

                "kutu_esik_1": float(self.asama2_kutu_esik1_var.get()),
                "kazanc_1": float(self.asama2_kazanc1_var.get()),
                "kutu_esik_2": float(self.asama2_kutu_esik2_var.get()),
                "kazanc_2": float(self.asama2_kazanc2_var.get()),
                "kutu_esik_3": float(self.asama2_kutu_esik3_var.get()),
                "kazanc_3": float(self.asama2_kazanc3_var.get()),
                "kazanc_4": float(self.asama2_kazanc4_var.get()),

                "tarama_min_derece": float(self.asama2_pan_min_var.get()),
                "tarama_max_derece": float(self.asama2_pan_max_var.get()),
                "tarama_hizi": float(self.asama2_hiz_var.get()),

                "ates_tolerans_oran": float(self.asama2_ates_oran_var.get()),
                "ates_tolerans_min_px": float(self.asama2_ates_min_var.get()),
                "ates_onay_suresi": float(self.asama2_ates_onay_var.get()),

                "donus_min_derece": float(self.asama2_donus_min_var.get()),
                "donus_max_derece": float(self.asama2_donus_max_var.get()),
            }

            self.asama2.parametreleri_ayarla(**parametreler)

        except Exception:
            pass

    def _asama2_ayarlari_kaydet(self):
        self._asama2_parametre_guncelle()

        basarili = self.asama2.kalici_kaydet()

        if basarili:
            self.asama2_kayit_label.config(text="✓ Ayarlar Kalıcı Olarak Kaydedildi", fg="green")
            self.kok.after(2500, lambda: self.asama2_kayit_label.config(text=""))
        else:
            self.asama2_kayit_label.config(text="✗ Kayıt Hatası!", fg="red")

    def _asama2_test_atesi(self):
        if self._acil_durum_kontrol():
            return

        if not self.pico.bagli_mi():
            messagebox.showwarning("Bağlantı Hatası", "Önce Pico'ya bağlanın.")
            return

        self.pico.servo_kontrol(0, 120, 100)

    # ================= AŞAMA 3 KONTROL SEKMESİ =================
    def _asama3_sekmesi_doldur(self, sekme):
        asama3_canvas = Canvas(sekme, bg="lightgray", highlightthickness=0)
        asama3_scrollbar = Scrollbar(sekme, orient="vertical", command=asama3_canvas.yview)

        self.asama3_scroll_frame = Frame(asama3_canvas, bg="lightgray")
        self.asama3_scroll_frame.bind(
            "<Configure>",
            lambda e: asama3_canvas.configure(scrollregion=asama3_canvas.bbox("all"))
        )

        asama3_canvas.create_window((0, 0), window=self.asama3_scroll_frame, anchor="nw")
        asama3_canvas.configure(yscrollcommand=asama3_scrollbar.set)

        asama3_scrollbar.pack(side="right", fill="y")
        asama3_canvas.pack(side="left", fill="both", expand=True)

        self._canvas_scroll_bagla(asama3_canvas)

        tk.Label(
            self.asama3_scroll_frame,
            text="Aşama 3: Düşman Balon İmha Kontrol Paneli",
            bg="lightgray",
            font=("Arial", 11, "bold")
        ).pack(pady=5)

        p = self.asama3.parametreleri_al()

        pid_x_frame = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="X Ekseni PID (Pan)",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        pid_x_frame.pack(fill="x", padx=6, pady=3)

        self.asama3_kpx_var = tk.StringVar(value=f"{p.get('kp_x', 7.0):.1f}")
        self.asama3_kdx_var = tk.StringVar(value=f"{p.get('kd_x', 0.5):.1f}")
        self.asama3_kix_var = tk.StringVar(value=f"{p.get('ki_x', 0.00):.2f}")

        self._spin_satir_asama3(pid_x_frame, "Kp X (Pan):", self.asama3_kpx_var, 0, 100, 1.0)
        self._spin_satir_asama3(pid_x_frame, "Kd X (Pan):", self.asama3_kdx_var, 0, 50, 0.1)
        self._spin_satir_asama3(pid_x_frame, "Ki X (Pan):", self.asama3_kix_var, 0, 10, 0.01)

        pid_y_frame = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="Y Ekseni PID (Tilt)",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        pid_y_frame.pack(fill="x", padx=6, pady=3)

        self.asama3_kpy_var = tk.StringVar(value=f"{p.get('kp_y', 10.5):.1f}")
        self.asama3_kdy_var = tk.StringVar(value=f"{p.get('kd_y', 2.5):.1f}")
        self.asama3_kiy_var = tk.StringVar(value=f"{p.get('ki_y', 0.00):.2f}")

        self._spin_satir_asama3(pid_y_frame, "Kp Y (Tilt):", self.asama3_kpy_var, 0, 100, 1.0)
        self._spin_satir_asama3(pid_y_frame, "Kd Y (Tilt):", self.asama3_kdy_var, 0, 50, 0.5)
        self._spin_satir_asama3(pid_y_frame, "Ki Y (Tilt):", self.asama3_kiy_var, 0, 10, 0.01)

        lead_frame = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="Lead Point / Feedforward Ayarları",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        lead_frame.pack(fill="x", padx=6, pady=3)

        self.asama3_lead_oran_var = tk.StringVar(value=f"{p.get('lead_kayma_orani', 140.0):.0f}")
        self.asama3_lead_esik_var = tk.StringVar(value=f"{p.get('lead_esik', 8.5):.1f}")
        self.asama3_lead_filtre_var = tk.StringVar(value=f"{p.get('lead_filtre', 0.25):.2f}")

        self._spin_satir_asama3(lead_frame, "Maks. Kayma (%):", self.asama3_lead_oran_var, 0, 250, 5)
        self._spin_satir_asama3(lead_frame, "Hız Eşiği (px/s):", self.asama3_lead_esik_var, 0.5, 30.0, 0.5)
        self._spin_satir_asama3(lead_frame, "Lead Filtre (Alpha):", self.asama3_lead_filtre_var, 0.05, 1.0, 0.05)

        boyut_frame = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="Boyuta Göre Kayma Kazancı",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        boyut_frame.pack(fill="x", padx=6, pady=3)

        self.asama3_kutu_esik1_var = tk.StringVar(value=f"{p.get('kutu_esik_1', 100.0):.0f}")
        self.asama3_kazanc1_var = tk.StringVar(value=f"{p.get('kazanc_1', 0.05):.3f}")
        self.asama3_kutu_esik2_var = tk.StringVar(value=f"{p.get('kutu_esik_2', 200.0):.0f}")
        self.asama3_kazanc2_var = tk.StringVar(value=f"{p.get('kazanc_2', 0.03):.3f}")
        self.asama3_kutu_esik3_var = tk.StringVar(value=f"{p.get('kutu_esik_3', 300.0):.0f}")
        self.asama3_kazanc3_var = tk.StringVar(value=f"{p.get('kazanc_3', 0.02):.3f}")
        self.asama3_kazanc4_var = tk.StringVar(value=f"{p.get('kazanc_4', 0.01):.3f}")

        self._spin_satir_asama3(boyut_frame, "Eşik 1 (px, ≤):", self.asama3_kutu_esik1_var, 20, 800, 10)
        self._spin_satir_asama3(boyut_frame, "Kazanç 1 (≤E1):", self.asama3_kazanc1_var, 0.001, 2.0, 0.005)
        self._spin_satir_asama3(boyut_frame, "Eşik 2 (px):", self.asama3_kutu_esik2_var, 40, 900, 10)
        self._spin_satir_asama3(boyut_frame, "Kazanç 2 (E1-E2):", self.asama3_kazanc2_var, 0.001, 2.0, 0.005)
        self._spin_satir_asama3(boyut_frame, "Eşik 3 (px):", self.asama3_kutu_esik3_var, 60, 1000, 10)
        self._spin_satir_asama3(boyut_frame, "Kazanç 3 (E2-E3):", self.asama3_kazanc3_var, 0.001, 2.0, 0.005)
        self._spin_satir_asama3(boyut_frame, "Kazanç 4 (>E3):", self.asama3_kazanc4_var, 0.001, 2.0, 0.005)

        tarama_frame = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="Tarama & Ateş Toleransı",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        tarama_frame.pack(fill="x", padx=6, pady=3)

        self.asama3_pan_min_var = tk.StringVar(value=f"{p.get('tarama_min_derece', 90.0):.0f}")
        self.asama3_pan_max_var = tk.StringVar(value=f"{p.get('tarama_max_derece', 180.0):.0f}")
        self.asama3_hiz_var = tk.StringVar(value=f"{p.get('tarama_hizi', 10.0):.0f}")
        self.asama3_ates_oran_var = tk.StringVar(value=f"{p.get('ates_tolerans_oran', 15.0):.0f}")
        self.asama3_ates_min_var = tk.StringVar(value=f"{p.get('ates_tolerans_min_px', 10.0):.0f}")
        self.asama3_ates_onay_var = tk.StringVar(value=f"{p.get('ates_onay_suresi', 0.15):.2f}")

        self._spin_satir_asama3(tarama_frame, "Tarama Min (°):", self.asama3_pan_min_var, 0, 270, 5)
        self._spin_satir_asama3(tarama_frame, "Tarama Max (°):", self.asama3_pan_max_var, 0, 270, 5)
        self._spin_satir_asama3(tarama_frame, "Tarama Hızı (°/s):", self.asama3_hiz_var, 5, 120, 5)
        self._spin_satir_asama3(tarama_frame, "Ateş Toleransı (%):", self.asama3_ates_oran_var, 5, 100, 5)
        self._spin_satir_asama3(tarama_frame, "Min Tolerans (px):", self.asama3_ates_min_var, 2, 60, 2)
        self._spin_satir_asama3(tarama_frame, "Kilit Süresi (s):", self.asama3_ates_onay_var, 0.15, 3.0, 0.05)

        donus_frame_a3 = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="Dönüş Limitleri (Yasak Bölge)",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        donus_frame_a3.pack(fill="x", padx=6, pady=3)

        self.asama3_donus_min_var = tk.StringVar(value=f"{p.get('donus_min_derece', 50.0):.0f}")
        self.asama3_donus_max_var = tk.StringVar(value=f"{p.get('donus_max_derece', 200.0):.0f}")

        self._spin_satir_asama3(donus_frame_a3, "Dönüş Min (°):", self.asama3_donus_min_var, 0, 270, 5)
        self._spin_satir_asama3(donus_frame_a3, "Dönüş Max (°):", self.asama3_donus_max_var, 0, 270, 5)

        roi_frame = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="Aşama 3 ROI / Dost Koruma",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        roi_frame.pack(fill="x", padx=6, pady=3)

        self.asama3_roi_yatay_var = tk.StringVar(value=f"{p.get('roi_yatay_pay', 0.20) * 100:.0f}")
        self.asama3_roi_dikey_var = tk.StringVar(value=f"{p.get('roi_dikey_pay', 1.50) * 100:.0f}")
        self.asama3_dost_roi_yatay_var = tk.StringVar(value=f"{p.get('dost_roi_yatay_pay', 0.25) * 100:.0f}")
        self.asama3_dost_roi_dikey_var = tk.StringVar(value=f"{p.get('dost_roi_dikey_pay', 1.80) * 100:.0f}")
        self.asama3_reset_var = tk.StringVar(value=f"{p.get('reset_suresi', 0.25):.2f}")

        self._spin_satir_asama3(roi_frame, "Düşman ROI Yatay (%):", self.asama3_roi_yatay_var, 0, 100, 5)
        self._spin_satir_asama3(roi_frame, "Düşman ROI Dikey (%):", self.asama3_roi_dikey_var, 50, 500, 10)
        self._spin_satir_asama3(roi_frame, "Dost ROI Yatay (%):", self.asama3_dost_roi_yatay_var, 0, 100, 5)
        self._spin_satir_asama3(roi_frame, "Dost ROI Dikey (%):", self.asama3_dost_roi_dikey_var, 50, 500, 10)
        self._spin_satir_asama3(roi_frame, "Reset Süresi (s):", self.asama3_reset_var, 0.05, 2.0, 0.05)

        durum_frame = tk.LabelFrame(
            self.asama3_scroll_frame,
            text="Canlı Durum",
            bg="lightgray",
            font=("Arial", 9, "bold")
        )
        durum_frame.pack(fill="x", padx=6, pady=4)

        self.asama3_durum_label = tk.Label(
            durum_frame,
            text="⏸️ Beklemede",
            bg="lightgray",
            font=("Arial", 10, "bold"),
            fg="orange"
        )
        self.asama3_durum_label.pack(pady=4)

        btn_f = tk.Frame(self.asama3_scroll_frame, bg="lightgray")
        btn_f.pack(pady=6)

        tk.Button(
            btn_f,
            text="🔥 Test Ateşi",
            command=self._asama3_test_atesi,
            bg="orange",
            fg="white",
            font=("Arial", 9, "bold"),
            width=11
        ).pack(side="left", padx=2)

        tk.Button(
            btn_f,
            text="💾 Kaydet",
            command=self._asama3_ayarlari_kaydet,
            bg="darkgreen",
            fg="white",
            font=("Arial", 9, "bold"),
            width=11
        ).pack(side="left", padx=2)

        self.asama3_kayit_label = tk.Label(
            self.asama3_scroll_frame,
            text="",
            bg="lightgray",
            font=("Arial", 8, "bold")
        )
        self.asama3_kayit_label.pack(pady=(0, 4))

        self._asama3_parametre_guncelle()

    def _spin_satir_asama3(self, parent, text, var, f, t, inc):
        row = tk.Frame(parent, bg="lightgray")
        row.pack(fill="x", padx=5, pady=2)

        tk.Label(row, text=text, bg="lightgray", width=18, anchor="w", font=("Arial", 8)).pack(side="left")

        sp = tk.Spinbox(
            row,
            from_=f,
            to=t,
            increment=inc,
            width=8,
            textvariable=var,
            command=self._asama3_parametre_guncelle
        )
        sp.pack(side="left", padx=2)

        sp.bind("<KeyRelease>", self._asama3_parametre_guncelle)
        sp.bind("<FocusOut>", self._asama3_parametre_guncelle)

    def _asama3_parametre_guncelle(self, event=None):
        try:
            parametreler = {
                "kp_x": float(self.asama3_kpx_var.get()),
                "kd_x": float(self.asama3_kdx_var.get()),
                "ki_x": float(self.asama3_kix_var.get()),

                "kp_y": float(self.asama3_kpy_var.get()),
                "kd_y": float(self.asama3_kdy_var.get()),
                "ki_y": float(self.asama3_kiy_var.get()),

                "lead_kayma_orani": float(self.asama3_lead_oran_var.get()),
                "lead_esik": float(self.asama3_lead_esik_var.get()),
                "lead_filtre": float(self.asama3_lead_filtre_var.get()),

                "kutu_esik_1": float(self.asama3_kutu_esik1_var.get()),
                "kazanc_1": float(self.asama3_kazanc1_var.get()),
                "kutu_esik_2": float(self.asama3_kutu_esik2_var.get()),
                "kazanc_2": float(self.asama3_kazanc2_var.get()),
                "kutu_esik_3": float(self.asama3_kutu_esik3_var.get()),
                "kazanc_3": float(self.asama3_kazanc3_var.get()),
                "kazanc_4": float(self.asama3_kazanc4_var.get()),

                "tarama_min_derece": float(self.asama3_pan_min_var.get()),
                "tarama_max_derece": float(self.asama3_pan_max_var.get()),
                "tarama_hizi": float(self.asama3_hiz_var.get()),

                "ates_tolerans_oran": float(self.asama3_ates_oran_var.get()),
                "ates_tolerans_min_px": float(self.asama3_ates_min_var.get()),
                "ates_onay_suresi": float(self.asama3_ates_onay_var.get()),

                "roi_yatay_pay": float(self.asama3_roi_yatay_var.get()) / 100.0,
                "roi_dikey_pay": float(self.asama3_roi_dikey_var.get()) / 100.0,

                "dost_roi_yatay_pay": float(self.asama3_dost_roi_yatay_var.get()) / 100.0,
                "dost_roi_dikey_pay": float(self.asama3_dost_roi_dikey_var.get()) / 100.0,

                "reset_suresi": float(self.asama3_reset_var.get()),

                "donus_min_derece": float(self.asama3_donus_min_var.get()),
                "donus_max_derece": float(self.asama3_donus_max_var.get()),
            }

            self.asama3.parametreleri_ayarla(**parametreler)

        except Exception:
            pass

    def _asama3_ayarlari_kaydet(self):
        self._asama3_parametre_guncelle()

        basarili = self.asama3.kalici_kaydet()

        if basarili:
            self.asama3_kayit_label.config(text="✓ Aşama 3 ayarları kalıcı olarak kaydedildi", fg="green")
            self.kok.after(2500, lambda: self.asama3_kayit_label.config(text=""))
        else:
            self.asama3_kayit_label.config(text="✗ Aşama 3 kayıt hatası!", fg="red")

    def _asama3_test_atesi(self):
        if self._acil_durum_kontrol():
            return

        if not self.pico.bagli_mi():
            messagebox.showwarning("Bağlantı Hatası", "Önce Pico'ya bağlanın.")
            return

        try:
            p = self.asama3.parametreleri_al()

            self.pico.servo_kontrol(
                int(p.get("servo_baslangic", 0)),
                int(p.get("servo_bitis", 120)),
                int(p.get("servo_bekleme", 100))
            )

        except Exception:
            self.pico.servo_kontrol(0, 120, 100)

    def _asama_butonlarini_guncelle(self, aktif_asama=None):
        try:
            self.asama1_baslat_buton_ana.config(state="disabled" if aktif_asama == 1 else "normal")
            self.asama1_durdur_buton_ana.config(state="normal" if aktif_asama == 1 else "disabled")

            self.asama2_baslat_buton_ana.config(state="disabled" if aktif_asama == 2 else "normal")
            self.asama2_durdur_buton_ana.config(state="normal" if aktif_asama == 2 else "disabled")

            self.asama3_baslat_buton_ana.config(state="disabled" if aktif_asama == 3 else "normal")
            self.asama3_durdur_buton_ana.config(state="normal" if aktif_asama == 3 else "disabled")

        except Exception:
            pass

    # ================= AŞAMA / OTONOM MOD YÖNETİMİ =================
    def _asama_modu_aktif(self):
        try:
            return self.asama2.aktif_mi() or self.asama3.aktif_mi()
        except Exception:
            return False

    def _asama_otonom_mod_ac(self):
        if self._mod == "homing":
            return

        try:
            self.otonom.dur()
        except Exception:
            pass

        if not self.pico.bagli_mi():
            messagebox.showwarning("Uyarı", "Otonom mod için önce Pico'ya bağlanın.")
            return

        self._mod = "otonom_asama"
        self._komut_gonder("MODE,AUTO")
        self._komut_gonder("MOTOR,ON")

    # ================= DÖNÜŞ LİMİTİ YARDIMCILARI =================
    def _donus_limit_degisti(self, event=None):
        try:
            min_val = float(self.donus_min_spin.get())
            max_val = float(self.donus_max_spin.get())

            if min_val < 0:
                min_val = 0

            if max_val > 270:
                max_val = 270

            if max_val <= min_val:
                max_val = min_val + 5

            self._donus_limit_min = min_val
            self._donus_limit_max = max_val

            try:
                self.asama2.parametreleri_ayarla(donus_min_derece=min_val, donus_max_derece=max_val)
            except Exception:
                pass

            try:
                self.asama3.parametreleri_ayarla(donus_min_derece=min_val, donus_max_derece=max_val)
            except Exception:
                pass

            try:
                self.asama2_donus_min_var.set(f"{min_val:.0f}")
                self.asama2_donus_max_var.set(f"{max_val:.0f}")
            except Exception:
                pass

            try:
                self.asama3_donus_min_var.set(f"{min_val:.0f}")
                self.asama3_donus_max_var.set(f"{max_val:.0f}")
            except Exception:
                pass

        except Exception:
            pass

    def _limit_gonder(self):
        if not self.pico.bagli_mi():
            return

        min_val = self._donus_limit_min
        max_val = self._donus_limit_max

        if max_val <= min_val:
            max_val = min_val + 5

        self._donus_limit_min = min_val
        self._donus_limit_max = max_val

        self._komut_gonder(f"LIMIT,PAN,{min_val:.1f},{max_val:.1f}")

        try:
            self.donus_limit_durum_label.config(
                text=f"⏳ Gönderiliyor: {min_val:.0f}°-{max_val:.0f}°",
                fg="orange"
            )
        except Exception:
            pass

    # ================= POZ TELEMETRİ =================
    def _pan_telemetri_isle(self, x_aci, y_aci):
        self._motor_aci_x = x_aci
        self._motor_aci_y = y_aci

        try:
            self.asama2.pan_acisi_bildir(y_aci)
        except Exception:
            pass

        try:
            self.asama3.pan_acisi_bildir(y_aci)
        except Exception:
            pass

    # ================= HEDEF TIKLAMA =================
    def _goruntu_tiklama(self, event):
        if self._asama_modu_aktif():
            messagebox.showwarning(
                "Hedef Seçimi Kapalı",
                "Aşama 2 / Aşama 3 aktifken manuel hedef seçimi kapalıdır.\n\nHedefi aşama modülü kendisi seçer."
            )
            return

        if self._mod != "otonom":
            messagebox.showwarning("Mod Uyarısı", "Hedef seçimi için otonom modda olmalısınız.")
            return

        tik_x, tik_y = event.x, event.y

        gercek_x = tik_x * self._son_oran_x
        gercek_y = tik_y * self._son_oran_y

        tespitler = self._son_tespitler

        if not tespitler:
            messagebox.showinfo("Hedef Yok", "Ekranda tespit edilmiş hedef yok.")
            return

        for tespit in tespitler:
            x1, y1, x2, y2 = tespit["kutu"]

            if x1 <= gercek_x <= x2 and y1 <= gercek_y <= y2:
                self.otonom.hedef_sec(tespit["kutu"])
                self.mod_etiketi.config(text="Hedef Kilitlendi ✓", bg="blue")
                return

        messagebox.showinfo("Hedef Seçimi", "Tıklanan noktada hedef bulunamadı.")

    # ================= YARDIMCI =================
    def _komut_gonder(self, komut):
        self.pico.komut_gonder(komut)

    def _gpu_kullanimi_al(self):
        try:
            cikti = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                timeout=1
            )

            return cikti.decode().strip().splitlines()[0].strip()

        except Exception:
            return "N/A"

    def _port_listesi_yenile(self):
        self._port_listesi = self.pico.portlari_bul()

        if not self._port_listesi:
            self._port_listesi = ["COM1", "COM2", "COM3"]

        self.com_port_combobox.configure(values=self._port_listesi)

        if self._port_listesi:
            self.com_port_combobox.set(self._port_listesi[0])

    # ================= KAMERA / MODEL =================
    def _ayarlari_uygula(self):
        try:
            port = int(self.port_dropdown.get())
            genislik, yukseklik = map(int, self.cozunurluk_dropdown.get().split("x"))

            self.motor.ayar_degistir(port=port, genislik=genislik, yukseklik=yukseklik)

        except Exception as hata:
            messagebox.showerror("Ayar Hatası", str(hata))

    def _model_sec(self):
        yol = filedialog.askopenfilename(
            title="TensorRT Model Seç",
            filetypes=[("TensorRT Engine", "*.engine"), ("Tüm Dosyalar", "*.*")]
        )

        if not yol:
            return

        basarili, mesaj = self.motor.model_yukle(yol)

        if basarili:
            self.hedef_model_durum_label.config(
                text=f"Hedef Model Yüklendi Başarılı: {os.path.basename(yol)}",
                fg="green"
            )
            messagebox.showinfo("Model Yüklendi", f"{os.path.basename(yol)} başarılı şekilde yüklendi.")
        else:
            self.hedef_model_durum_label.config(text="Hedef Model: Yüklenemedi", fg="red")
            messagebox.showerror("Model Yüklenemedi", mesaj)

    def _conf_degisti(self, deger):
        try:
            conf = float(deger)
            self.conf_deger_label.config(text=f"{conf:.2f}")
            self.motor.set_conf_threshold(conf)
        except Exception:
            pass

    def _ikinci_model_sec(self):
        yol = filedialog.askopenfilename(
            title="Balon Tespit Modeli Seç",
            filetypes=[("TensorRT Engine", "*.engine"), ("PyTorch Model", "*.pt"), ("Tüm Dosyalar", "*.*")]
        )

        if not yol:
            return

        basarili, mesaj = self.motor.ikinci_model_yukle(yol)

        if basarili:
            self._balon_model_yuklu_mu = True
            self.balon_model_durum_label.config(
                text=f"Balon Modeli Yüklendi Başarılı: {os.path.basename(yol)}",
                fg="green"
            )
            messagebox.showinfo("Balon Modeli Yüklendi", f"{os.path.basename(yol)} başarılı şekilde yüklendi.")
        else:
            self._balon_model_yuklu_mu = False
            self.balon_model_durum_label.config(text="Balon Modeli: Yüklenemedi", fg="red")
            messagebox.showerror("Balon Modeli Yüklenemedi", mesaj)

    def _balon_conf_degisti(self, deger):
        try:
            conf = float(deger)
            self.balon_conf_deger_label.config(text=f"{conf:.2f}")
            self.motor.set_ikinci_conf_threshold(conf)
        except Exception:
            pass

    def _balon_model_aktif(self):
        hazir, yol = self.motor.ikinci_model_durumunu_al()

        if hazir:
            self._balon_model_yuklu_mu = True
            self.balon_model_durum_label.config(
                text=f"Balon Modeli: Aktif ({os.path.basename(yol)})",
                fg="green"
            )
            messagebox.showinfo("Balon Modeli", "Balon modeli zaten yüklü ve aktif.")
        else:
            messagebox.showwarning("Model Yok", "Önce balon modelini yükleyin.")

    def _balon_model_pasif(self):
        hazir, yol = self.motor.ikinci_model_durumunu_al()

        if hazir:
            self.motor.set_ikinci_conf_threshold(1.0)
            self._balon_model_yuklu_mu = False
            self.balon_model_durum_label.config(text="Balon Modeli: Pasif", fg="orange")
            self.balon_conf_scale.set(1.0)
            messagebox.showinfo("Balon Modeli", "Balon modeli pasif edildi.")
        else:
            messagebox.showinfo("Model Yok", "Balon modeli zaten yüklü değil.")

    def _hedef_model_aktif(self):
        hazir, yol = self.motor.model_durumunu_al()

        if hazir:
            self.hedef_model_durum_label.config(
                text=f"Hedef Model: Aktif ({os.path.basename(yol)})",
                fg="green"
            )

            conf = float(self.conf_scale.get())
            self.motor.set_conf_threshold(conf)

            messagebox.showinfo("Hedef Modeli", "Hedef modeli zaten yüklü ve aktif.")
        else:
            messagebox.showwarning("Model Yok", "Önce hedef modelini yükleyin.")

    def _hedef_model_pasif(self):
        hazir, yol = self.motor.model_durumunu_al()

        if hazir:
            self.motor.set_conf_threshold(1.0)
            self.hedef_model_durum_label.config(text="Hedef Model: Pasif", fg="orange")
            self.conf_scale.set(1.0)
            self.conf_deger_label.config(text="1.00")
            messagebox.showinfo("Hedef Modeli", "Hedef modeli pasif edildi.")
        else:
            messagebox.showinfo("Model Yok", "Hedef modeli zaten yüklü değil.")

    # ================= AŞAMA KONTROL FONKSİYONLARI =================
    def _asama1_baslat(self):
        if self._acil_durum_kontrol():
            return

        if not self.pico.bagli_mi():
            messagebox.showwarning("Bağlantı Hatası", "Aşama 1 için önce Pico'ya bağlanın.")
            return

        if self._mod != "manuel":
            self._manuel_mod_ac()

        try:
            self.asama2.dur()
        except Exception:
            pass

        try:
            self.asama3.dur()
        except Exception:
            pass

        self.asama1.baslat()

        self._asama_butonlarini_guncelle(1)
        self.balon_patlat_buton.config(state="normal")

        self.asama_durum_label.config(text="Durum: Aşama 1 Aktif - Manuel Kontrol", fg="green")

    def _asama1_durdur(self):
        self.asama1.dur()

        self._asama_butonlarini_guncelle(None)
        self.asama_durum_label.config(text="Durum: Aşama 1 Durduruldu", fg="orange")

    def _asama2_baslat(self):
        if self._acil_durum_kontrol():
            return

        if not self.pico.bagli_mi():
            messagebox.showwarning("Bağlantı Hatası", "Aşama 2 için önce Pico'ya bağlanın.")
            return

        self._asama_otonom_mod_ac()

        try:
            self.asama1.dur()
        except Exception:
            pass

        try:
            self.asama3.dur()
        except Exception:
            pass

        self.asama2.baslat()

        self._asama_butonlarini_guncelle(2)
        self.asama_durum_label.config(text="Durum: Aşama 2 Aktif - Otonom Tarama", fg="blue")

    def _asama2_durdur(self):
        self.asama2.dur()

        self._asama_butonlarini_guncelle(None)
        self.asama_durum_label.config(text="Durum: Aşama 2 Durduruldu", fg="orange")

        try:
            self.asama2_durum_label.config(text="⏸️ Durduruldu", fg="orange")
        except Exception:
            pass

        if not self._asama_modu_aktif():
            self._manuel_mod_ac()

    def _asama3_baslat(self):
        if self._acil_durum_kontrol():
            return

        if not self.pico.bagli_mi():
            messagebox.showwarning("Bağlantı Hatası", "Aşama 3 için önce Pico'ya bağlanın.")
            return

        self._asama_otonom_mod_ac()

        try:
            self.asama1.dur()
        except Exception:
            pass

        try:
            self.asama2.dur()
        except Exception:
            pass

        self.asama3.baslat()

        self._asama_butonlarini_guncelle(3)
        self.asama_durum_label.config(text="Durum: Aşama 3 Aktif - Düşman Balon İmhası", fg="purple")

    def _asama3_durdur(self):
        self.asama3.dur()

        self._asama_butonlarini_guncelle(None)
        self.asama_durum_label.config(text="Durum: Aşama 3 Durduruldu", fg="orange")

        try:
            self.asama3_durum_label.config(text="⏸️ Durduruldu", fg="orange")
        except Exception:
            pass

        if not self._asama_modu_aktif():
            self._manuel_mod_ac()

    # ================= SERVO KONTROL =================
    def _servo_bas_degisti(self, deger):
        try:
            self.servo_bas_deger_label_servo.config(text=f"{int(float(deger))}°")
        except Exception:
            pass

    def _servo_bit_degisti(self, deger):
        try:
            self.servo_bit_deger_label_servo.config(text=f"{int(float(deger))}°")
        except Exception:
            pass

    def _servo_bekle_degisti(self, deger):
        try:
            self.servo_bekle_deger_label_servo.config(text=f"{int(float(deger))}ms")
        except Exception:
            pass

    def _balon_patlat(self):
        if self._acil_durum_kontrol():
            return

        if not self.pico.bagli_mi():
            messagebox.showerror("Bağlantı Hatası", "Balon patlatma için önce Pico'ya bağlanın.")
            return

        baslangic_aci = int(self.servo_bas_scale_servo.get())
        bitis_aci = int(self.servo_bit_scale_servo.get())
        bekleme_ms = int(self.servo_bekle_scale_servo.get())

        basarili = self.pico.servo_kontrol(baslangic_aci, bitis_aci, bekleme_ms)

        if basarili:
            durum_mesaji = f"✓ Servo: {baslangic_aci}° → {bitis_aci}° ({bekleme_ms}ms)"

            self.servo_durum_label_ana.config(text=durum_mesaji, fg="green")
            self.servo_durum_label_servo.config(text=durum_mesaji, fg="green")
        else:
            hata_mesaji = "✗ Servo komutu gönderilemedi"

            self.servo_durum_label_ana.config(text=hata_mesaji, fg="red")
            self.servo_durum_label_servo.config(text=hata_mesaji, fg="red")

    # ================= BAĞLANTI & MODLAR =================
    def _seri_baglan(self):
        port = self.com_port_combobox.get().strip()

        if not port:
            messagebox.showerror("Hata", "Önce bir COM portu seçin.")
            return

        if self.pico.bagli_mi():
            messagebox.showinfo("Bilgi", "Zaten bağlısınız.")
            return

        self.baglan_buton.config(state="disabled")
        self.durum_etiketi.config(text="Bağlanıyor...", fg="orange")

        self.pico.baglan_async(port)

    def _seri_kes(self):
        self._sistem_kapali()
        self.pico.kes()

        self.durum_etiketi.config(text="Bağlantı Kesildi", fg="red")
        self.baglan_buton.config(state="normal")

    def _sistem_kapali(self):
        try:
            self.otonom.dur()
        except Exception:
            pass

        try:
            self.asama1.dur()
            self.asama2.dur()
            self.asama3.dur()
            self._asama_butonlarini_guncelle(None)
        except Exception:
            pass

        self._mod = "kapali"
        self._gosterilen_x_hiz = 0
        self._gosterilen_y_hiz = 0

        self._komut_gonder("STP")
        self._komut_gonder("0,0,0,0,0")

        self.mod_etiketi.config(text="Sistem Kapalı", bg="red")

    def _manuel_mod_ac(self):
        if self._mod == "homing":
            return

        if self._mod == "manuel":
            return

        try:
            self.otonom.dur()
        except Exception:
            pass

        self._mod = "manuel"

        self._komut_gonder("MODE,MANUAL")
        self._komut_gonder("MOTOR,ON")

        self.mod_etiketi.config(text="Manuel Mod", bg="green")

    def _otonom_mod_ac(self):
        if self._acil_durum_kontrol():
            return

        if self._mod == "homing":
            return

        if self._mod == "otonom":
            return

        if self._asama_modu_aktif():
            messagebox.showwarning(
                "Uyarı",
                "Aşama 2 / Aşama 3 çalışırken genel otonom mod başlatılamaz.\n\nÖnce çalışan aşamayı durdurun."
            )
            return

        if not self.pico.bagli_mi():
            messagebox.showwarning("Uyarı", "Otonom mod için önce Pico'ya bağlanın.")
            return

        self._mod = "otonom"

        self._komut_gonder("MODE,AUTO")
        self._komut_gonder("MOTOR,ON")

        try:
            self.otonom.baslat()
        except Exception as hata:
            messagebox.showerror("Otonom Hatası", str(hata))

        self.mod_etiketi.config(text="Hedef Seçin (Kutuya Tıklayın)", bg="orange")

    def _autohome_baslat(self):
        if self._acil_durum_kontrol():
            return

        if not self.pico.bagli_mi():
            messagebox.showwarning("Uyarı", "AutoHome için önce Pico'ya bağlanın.")
            return

        if self._mod == "homing":
            return

        self._mod = "homing"
        self.mod_etiketi.config(text="Homing...", bg="orange")

        self._komut_gonder("HOME")

    def _hiz_modu_degistir(self, event=None):
        secim = self.hiz_modu_combobox.get()
        self._motor_modu = 1 if secim == "Hızlı" else 0
        self._komut_gonder(f"MODE,SPEED,{self._motor_modu}")

    def _mikrostep_degistir(self, event=None):
        try:
            self._mikrostep_degeri = int(self.mikrostep_combobox.get())
            self._komut_gonder(f"USTEP,{self._mikrostep_degeri}")
        except Exception:
            pass

    # ================= PID AYARLARI =================
    def _pid_entryleri_doldur(self):
        pid = self.otonom.pid_degerlerini_al()

        self.pid_kp_x_pid.delete(0, tk.END)
        self.pid_kp_x_pid.insert(0, f"{pid['kp_x']:.2f}")

        self.pid_ki_x_pid.delete(0, tk.END)
        self.pid_ki_x_pid.insert(0, f"{pid['ki_x']:.2f}")

        self.pid_kd_x_pid.delete(0, tk.END)
        self.pid_kd_x_pid.insert(0, f"{pid['kd_x']:.2f}")

        self.pid_kp_y_pid.delete(0, tk.END)
        self.pid_kp_y_pid.insert(0, f"{pid['kp_y']:.2f}")

        self.pid_ki_y_pid.delete(0, tk.END)
        self.pid_ki_y_pid.insert(0, f"{pid['ki_y']:.2f}")

        self.pid_kd_y_pid.delete(0, tk.END)
        self.pid_kd_y_pid.insert(0, f"{pid['kd_y']:.2f}")

    def _pid_entry_degisti_pid(self, event=None):
        try:
            kp_x = float(self.pid_kp_x_pid.get())
            ki_x = float(self.pid_ki_x_pid.get())
            kd_x = float(self.pid_kd_x_pid.get())

            kp_y = float(self.pid_kp_y_pid.get())
            ki_y = float(self.pid_ki_y_pid.get())
            kd_y = float(self.pid_kd_y_pid.get())

            basarili = self.otonom.pid_guncelle(
                kp_x=kp_x,
                ki_x=ki_x,
                kd_x=kd_x,
                kp_y=kp_y,
                ki_y=ki_y,
                kd_y=kd_y
            )

            if basarili:
                self.pid_durum_label_pid.config(text="✓ Kaydedildi", fg="green")
            else:
                self.pid_durum_label_pid.config(text="✗ Geçersiz değer", fg="red")

        except ValueError:
            self.pid_durum_label_pid.config(text="✗ Geçersiz değer", fg="red")

        except Exception:
            pass

    def _pid_sifirla(self):
        self.otonom.pid_sifirla()
        self._pid_entryleri_doldur()
        self.pid_durum_label_pid.config(text="✓ Varsayılanlara sıfırlandı", fg="green")

    def _pid_ayarlari_kaydet(self):
        self._pid_entry_degisti_pid()

    def _pid_event_bagla(self):
        for entry in [
            self.pid_kp_x_pid,
            self.pid_ki_x_pid,
            self.pid_kd_x_pid,
            self.pid_kp_y_pid,
            self.pid_ki_y_pid,
            self.pid_kd_y_pid
        ]:
            entry.bind("<KeyRelease>", self._pid_entry_degisti_pid)
            entry.bind("<FocusOut>", self._pid_entry_degisti_pid)

    # =========================================================
    # EKRAN ETİKET YARDIMCILARI
    # =========================================================
    def _etiket_yaz(self, img, metin, x, y, renk, olcek=0.55, kalinlik=1):
        cv2.putText(img, metin, (x, y), cv2.FONT_HERSHEY_SIMPLEX, olcek, renk, kalinlik, cv2.LINE_AA)

    def _sag_etiket_yaz(self, img, metin, y, renk, olcek=0.55, kalinlik=1, marj=10):
        (tw, th), baseline = cv2.getTextSize(metin, cv2.FONT_HERSHEY_SIMPLEX, olcek, kalinlik)
        x = img.shape[1] - tw - marj
        cv2.putText(img, metin, (x, y), cv2.FONT_HERSHEY_SIMPLEX, olcek, renk, kalinlik, cv2.LINE_AA)

    def _kutu_etiket_yerlestir(self, img, metin, x1, y1, x2, y2, renk, ekran_yukseklik, pozisyon="auto"):
        font = cv2.FONT_HERSHEY_SIMPLEX
        olcek = 0.55
        kalinlik = 1

        (tw, th), baseline = cv2.getTextSize(metin, font, olcek, kalinlik)

        if pozisyon == "auto":
            pozisyon = "ust" if y1 >= (th + baseline + 8) else "alt"

        if pozisyon == "ust":
            y = max(th + baseline + 2, y1 - 6)
        else:
            y = min(ekran_yukseklik - 4, y2 + th + baseline + 6)

        x = x1

        if x + tw + 4 > img.shape[1]:
            x = max(2, img.shape[1] - tw - 6)

        self._etiket_yaz(img, metin, x, y, renk, olcek, kalinlik)

    # ================= ANA GÜNCELLEME DÖNGÜSÜ =================
    def _guncelle(self):
        try:
            self._display_sayac += 1
            simdi = time.time()

            if simdi - self._display_baslangic >= 1.0:
                self._display_fps_guncel = self._display_sayac
                self._display_sayac = 0
                self._display_baslangic = simdi

            asama3 = getattr(self, "asama3", None)

            if asama3 is not None and asama3.aktif_mi():
                istatistik = asama3.istatistikleri_al()
                txt = istatistik.get("durum_bilgisi", "Çalışıyor")

                self.asama_durum_label.config(text=f"Aşama 3: {txt}", fg="purple")

                if hasattr(self, "asama3_durum_label"):
                    self.asama3_durum_label.config(text=txt, fg="purple")

            elif self.asama2.aktif_mi():
                istatistik = self.asama2.istatistikleri_al()
                txt = istatistik.get("durum_bilgisi", "Çalışıyor")

                self.asama_durum_label.config(text=f"Aşama 2: {txt}", fg="blue")

                if hasattr(self, "asama2_durum_label"):
                    self.asama2_durum_label.config(text=txt, fg="blue")

            komut, x_hiz, y_hiz = self.manuel.komut_uret()

            try:
                self.joy_x_etiketi.config(text=f"X Ekseni: {x_hiz}")
                self.joy_y_etiketi.config(text=f"Y Ekseni: {y_hiz}")
            except Exception:
                pass

            if self._mod == "manuel" and komut:
                self._komut_gonder(komut)

                self._gosterilen_x_hiz = x_hiz
                self._gosterilen_y_hiz = y_hiz

            elif self._mod != "manuel":
                self._gosterilen_x_hiz = 0
                self._gosterilen_y_hiz = 0

            if self.pico.bagli_mi() and simdi - self._son_hb_zamani >= 0.1:
                self._komut_gonder("HB")
                self._son_hb_zamani = simdi

            if self.pico.bagli_mi() and simdi - self._son_poz_sorgu_zamani >= 0.25:
                self._komut_gonder("POZ")
                self._son_poz_sorgu_zamani = simdi

            balon_hazir, balon_yolu = self.motor.ikinci_model_durumunu_al()

            if balon_hazir and self._balon_model_yuklu_mu:
                self.balon_model_durum_label.config(
                    text=f"Balon Modeli: Aktif ({os.path.basename(balon_yolu)})",
                    fg="green"
                )
            elif balon_hazir and not self._balon_model_yuklu_mu:
                self.balon_model_durum_label.config(text="Balon Modeli: Pasif", fg="orange")
            elif not balon_hazir:
                self.balon_model_durum_label.config(text="Balon Modeli: Yüklenmedi", fg="red")

            hedef_hazir, hedef_yolu = self.motor.model_durumunu_al()

            if hedef_hazir:
                self.hedef_model_durum_label.config(
                    text=f"Hedef Model: Aktif ({os.path.basename(hedef_yolu)})",
                    fg="green"
                )
            else:
                self.hedef_model_durum_label.config(text="Hedef Model: Yüklenmedi", fg="red")

            if self._mod == "otonom":
                kilitli, merkez = self.otonom.hedef_durumunu_al()

                if kilitli:
                    self.mod_etiketi.config(text="Hedef Kilitlendi ✓", bg="blue")
                else:
                    self.mod_etiketi.config(text="Hedef Seçin (Kutuya Tıklayın)", bg="orange")

            elif self._mod == "otonom_asama":
                if self.asama3.aktif_mi():
                    self.mod_etiketi.config(text="Aşama 3 Otonom - Hedef Seçimi Kapalı", bg="purple")
                elif self.asama2.aktif_mi():
                    self.mod_etiketi.config(text="Aşama 2 Otonom - Hedef Seçimi Kapalı", bg="blue")
                else:
                    self.mod_etiketi.config(text="Aşama Modu (Boşta)", bg="gray")

            kare, tespitler, kamera_fps, inference_ms, kare_zamani = self.motor.verileri_al()

            if kare is None:
                self.kok.after(15, self._guncelle)
                return

            kare = kare.copy()

            orijinal_y, orijinal_x = kare.shape[:2]

            et_g = self.goruntu_etiketi.winfo_width()
            et_y = self.goruntu_etiketi.winfo_height()

            if et_g <= 1 or et_y <= 1:
                et_g, et_y = 960, 540

            hedef_g = max(640, min(et_g, 1280))
            hedef_y = max(480, min(et_y, 720))

            oran_x = hedef_g / orijinal_x
            oran_y = hedef_y / orijinal_y

            if orijinal_x != hedef_g or orijinal_y != hedef_y:
                kare = cv2.resize(kare, (hedef_g, hedef_y), interpolation=cv2.INTER_AREA)

            self._son_oran_x = orijinal_x / hedef_g if hedef_g > 0 else 1.0
            self._son_oran_y = orijinal_y / hedef_y if hedef_y > 0 else 1.0

            self._son_tespitler = tespitler

            yukseklik, genislik = kare.shape[:2]

            mx, my = genislik // 2, yukseklik // 2

            cv2.line(kare, (mx - 50, my), (mx + 50, my), (0, 255, 0), 2)
            cv2.line(kare, (mx, my - 50), (mx, my + 50), (0, 255, 0), 2)
            cv2.circle(kare, (mx, my), 5, (0, 255, 0), -1)

            kilitli, hedef_merkez = self.otonom.hedef_durumunu_al()

            simdi_zaman = time.time()

            try:
                oran_ayar = float(self.asama2_lead_oran_var.get()) / 100.0
                esik_ayar = float(self.asama2_lead_esik_var.get())
                filtre_ayar = float(self.asama2_lead_filtre_var.get())
            except Exception:
                oran_ayar, esik_ayar, filtre_ayar = 1.40, 8.5, 0.25

            for i, tespit in enumerate(tespitler):
                x1, y1, x2, y2 = tespit["kutu"]

                x1, y1 = int(x1 * oran_x), int(y1 * oran_y)
                x2, y2 = int(x2 * oran_x), int(y2 * oran_y)

                merkez_x = (x1 + x2) / 2.0
                merkez_y = (y1 + y2) / 2.0

                kutu_genislik = x2 - x1

                okut_x1, okut_y1, okut_x2, okut_y2 = tespit["kutu"]

                kutu_w_px = int(okut_x2 - okut_x1)
                kutu_h_px = int(okut_y2 - okut_y1)

                try:
                    kazanc_ayar = self.asama2.kazanc_sec(tespit["kutu"][2] - tespit["kutu"][0])
                except Exception:
                    kazanc_ayar = 0.03

                key = f"hedef_{i}"

                if key not in self._gecmis_kutular:
                    self._gecmis_kutular[key] = {
                        "x": merkez_x,
                        "y": merkez_y,
                        "vx": 0.0,
                        "offset_x": 0.0,
                        "zaman": simdi_zaman
                    }

                eski = self._gecmis_kutular[key]

                dt_kare = simdi_zaman - eski["zaman"]

                anlik_vx_bagil = 0.0

                if dt_kare > 0.001:
                    anlik_vx_bagil = (merkez_x - eski["x"]) / dt_kare

                taret_deg_s = self._gosterilen_x_hiz / self.asama2.PAN_ADIM_DERECESI
                v_taret_px = taret_deg_s * 52.0

                anlik_vx_gercek = anlik_vx_bagil + v_taret_px

                alpha_v = max(0.05, min(1.0, filtre_ayar))
                vx = alpha_v * anlik_vx_gercek + (1.0 - alpha_v) * eski["vx"]

                hiz_abs = abs(vx)

                if hiz_abs > esik_ayar:
                    yon = np.sign(vx)

                    maks_pay = (kutu_genislik / 2.0) * oran_ayar

                    kayma = min((hiz_abs - esik_ayar) * kazanc_ayar * 0.1, 1.0)
                    hedef_offset = yon * maks_pay * kayma
                else:
                    hedef_offset = 0.0

                alpha_l = max(0.05, min(1.0, filtre_ayar))

                yeni_offset = alpha_l * hedef_offset + (1.0 - alpha_l) * eski.get("offset_x", 0.0)

                self._gecmis_kutular[key] = {
                    "x": merkez_x,
                    "y": merkez_y,
                    "vx": vx,
                    "offset_x": yeni_offset,
                    "zaman": simdi_zaman
                }

                daire_x = merkez_x + yeni_offset
                daire_y = merkez_y

                durum = tespit.get("durum", "düşman")
                sinif = tespit.get("sinif", "")

                if durum == "balon" or "balon" in sinif.lower() or tespit.get("model") == "ikinci":
                    renk = (255, 255, 0)
                    etiket_metni = f"BALON %{tespit['guven'] * 100:.0f} [{kutu_w_px}x{kutu_h_px}]"
                elif durum == "dost":
                    renk = (0, 255, 0)
                    etiket_metni = f"{sinif.upper()} (DOST) %{tespit['guven'] * 100:.0f} [{kutu_w_px}x{kutu_h_px}]"
                else:
                    renk = (0, 0, 255)
                    etiket_metni = f"{sinif.upper()} (DUSMAN) %{tespit['guven'] * 100:.0f} [{kutu_w_px}x{kutu_h_px}]"

                kutu_merkez_x = (tespit["kutu"][0] + tespit["kutu"][2]) / 2.0
                kutu_merkez_y = (tespit["kutu"][1] + tespit["kutu"][3]) / 2.0

                if kilitli and hedef_merkez is not None:
                    mesafe = ((kutu_merkez_x - hedef_merkez[0]) ** 2 + (kutu_merkez_y - hedef_merkez[1]) ** 2) ** 0.5

                    if mesafe < 50:
                        cv2.rectangle(kare, (x1, y1), (x2, y2), (255, 0, 0), 3)

                        self._kutu_etiket_yerlestir(
                            kare,
                            "KILITLI " + etiket_metni,
                            x1,
                            y1,
                            x2,
                            y2,
                            (255, 0, 0),
                            yukseklik
                        )

                        cv2.circle(kare, (int(merkez_x), int(merkez_y)), 3, (200, 200, 200), -1)
                        cv2.line(kare, (int(merkez_x), int(merkez_y)), (int(daire_x), int(daire_y)), (0, 200, 255), 1)
                        cv2.circle(kare, (int(daire_x), int(daire_y)), 6, (0, 165, 255), -1)
                        cv2.circle(kare, (int(daire_x), int(daire_y)), 8, (255, 255, 255), 1)

                        continue

                cv2.rectangle(kare, (x1, y1), (x2, y2), renk, 2)

                self._kutu_etiket_yerlestir(kare, etiket_metni, x1, y1, x2, y2, renk, yukseklik)

                cv2.circle(kare, (int(merkez_x), int(merkez_y)), 3, (200, 200, 200), -1)
                cv2.line(kare, (int(merkez_x), int(merkez_y)), (int(daire_x), int(daire_y)), (0, 200, 255), 1)
                cv2.circle(kare, (int(daire_x), int(daire_y)), 6, (0, 165, 255), -1)
                cv2.circle(kare, (int(daire_x), int(daire_y)), 8, (255, 255, 255), 1)

            # AŞAMA 2 TEŞHİS
            a2_aktif = self.asama2.aktif_mi()

            if a2_aktif:
                secili = getattr(self.asama2, "_secili_balon", None)

                if secili is not None:
                    skx1, sky1, skx2, sky2 = secili["kutu"]

                    sapma_x = (skx1 + skx2) / 2.0 - orijinal_x / 2.0
                    sapma_y = (sky1 + sky2) / 2.0 - orijinal_y / 2.0

                    cv2.putText(
                        kare,
                        f"A2 Sapma X: {sapma_x:+.0f} px  Y: {sapma_y:+.0f} px",
                        (10, 110),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 255, 255),
                        2,
                        cv2.LINE_AA
                    )

            # AŞAMA 3 TEŞHİS
            a3_aktif = (asama3 is not None and asama3.aktif_mi())

            if a3_aktif:
                secili = getattr(asama3, "_hedef_balon", None)
                dusman = getattr(asama3, "_dusman_hedef", None)

                if dusman is not None:
                    try:
                        dkx1, dky1, dkx2, dky2 = dusman["kutu"]

                        dx1 = int(dkx1 * oran_x)
                        dy1 = int(dky1 * oran_y)
                        dx2 = int(dkx2 * oran_x)
                        dy2 = int(dky2 * oran_y)

                        cv2.rectangle(kare, (dx1, dy1), (dx2, dy2), (0, 0, 255), 2)

                        self._kutu_etiket_yerlestir(
                            kare,
                            "DUSMAN",
                            dx1,
                            dy1,
                            dx2,
                            dy2,
                            (0, 0, 255),
                            yukseklik
                        )

                        roi = asama3._roi_olustur(
                            dusman["kutu"],
                            getattr(asama3, "_roi_yatay_pay", 0.20),
                            getattr(asama3, "_roi_dikey_pay", 1.50),
                            orijinal_x,
                            orijinal_y
                        )

                        if roi is not None:
                            rx1 = int(roi[0] * oran_x)
                            ry1 = int(roi[1] * oran_y)
                            rx2 = int(roi[2] * oran_x)
                            ry2 = int(roi[3] * oran_y)

                            cv2.rectangle(kare, (rx1, ry1), (rx2, ry2), (0, 165, 255), 1)

                            roi_metin = "A3 ROI"

                            font = cv2.FONT_HERSHEY_SIMPLEX
                            olcek = 0.5
                            kal = 1

                            (tw, th), baseline = cv2.getTextSize(roi_metin, font, olcek, kal)

                            etiket_x = max(rx1 + 3, rx2 - tw - 6)
                            etiket_y = ry1 + th + 6

                            cv2.putText(kare, roi_metin, (etiket_x, etiket_y), font, olcek, (0, 165, 255), kal, cv2.LINE_AA)

                    except Exception:
                        pass

                if secili is not None:
                    try:
                        skx1, sky1, skx2, sky2 = secili["kutu"]

                        sapma_x = (skx1 + skx2) / 2.0 - orijinal_x / 2.0
                        sapma_y = (sky1 + sky2) / 2.0 - orijinal_y / 2.0

                        bx1 = int(skx1 * oran_x)
                        by1 = int(sky1 * oran_y)
                        bx2 = int(skx2 * oran_x)
                        by2 = int(sky2 * oran_y)

                        cv2.rectangle(kare, (bx1, by1), (bx2, by2), (255, 0, 255), 3)

                        self._kutu_etiket_yerlestir(
                            kare,
                            "A3 HEDEF BALON",
                            bx1,
                            by1,
                            bx2,
                            by2,
                            (255, 0, 255),
                            yukseklik,
                            pozisyon="alt"
                        )

                        cv2.putText(
                            kare,
                            f"A3 Sapma X: {sapma_x:+.0f} px  Y: {sapma_y:+.0f} px",
                            (10, 135),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            (255, 0, 255),
                            2,
                            cv2.LINE_AA
                        )

                    except Exception:
                        pass

            # SOL ÜST BİLGİ PANELİ
            satir_y = 25
            satir_adim = 22

            self._etiket_yaz(kare, f"FPS: {kamera_fps:.1f}", 10, satir_y, (0, 255, 255), 0.6, 1)
            satir_y += satir_adim

            self._etiket_yaz(kare, f"Display FPS: {self._display_fps_guncel}", 10, satir_y, (0, 255, 255), 0.55, 1)
            satir_y += satir_adim

            e2e = (simdi - kare_zamani) * 1000 if kare_zamani > 0 else 0.0
            self._sag_etiket_yaz(kare, f"Gecikme: {e2e:.1f} ms", 25, (0, 255, 255), 0.6, 1)

            if simdi - self._son_gpu_zamani >= 1.0:
                self._gpu_kullanimi = self._gpu_kullanimi_al()
                self._son_gpu_zamani = simdi

            # SAĞ ALT YIĞIN
            self._sag_etiket_yaz(
                kare,
                f"Limit: {self._donus_limit_min:.0f}°-{self._donus_limit_max:.0f}°",
                yukseklik - 10,
                (255, 200, 100),
                0.50,
                1
            )

            self._sag_etiket_yaz(
                kare,
                f"Eksen X: {self._motor_aci_x:+.1f}°  Eksen Y: {self._motor_aci_y:+.1f}°",
                yukseklik - 32,
                (255, 255, 0),
                0.55,
                1
            )

            self._sag_etiket_yaz(
                kare,
                f"GPU: %{self._gpu_kullanimi}",
                yukseklik - 54,
                (150, 255, 150),
                0.55,
                1
            )

            # 24V / Acil durdur göstergesi
            if self._acil_durum_aktif:
                self._sag_etiket_yaz(
                    kare,
                    "ACIL DURDUR AKTIF / 24V YOK",
                    yukseklik - 76,
                    (0, 0, 255),
                    0.60,
                    2
                )
            else:
                self._sag_etiket_yaz(
                    kare,
                    "24V: AKTIF",
                    yukseklik - 76,
                    (150, 255, 150),
                    0.55,
                    1
                )

            self._etiket_yaz(
                kare,
                f"X: {self._gosterilen_x_hiz}  Y: {self._gosterilen_y_hiz}",
                10,
                yukseklik - 10,
                (0, 255, 0),
                0.55,
                1
            )

            if a3_aktif:
                self._etiket_yaz(kare, "[ ASAMA 3 AKTIF ]", 10, satir_y, (255, 0, 255), 0.6, 2)
            elif a2_aktif:
                self._etiket_yaz(kare, "[ ASAMA 2 AKTIF ]", 10, satir_y, (0, 255, 255), 0.6, 2)

            rgb = cv2.cvtColor(kare, cv2.COLOR_BGR2RGB)

            photo = ImageTk.PhotoImage(Image.fromarray(rgb))

            self.goruntu_etiketi.configure(image=photo)
            self.goruntu_etiketi.image = photo

        except Exception as e:
            print(f"[Hata] Güncelleme hatası: {e}")

        self.kok.after(15, self._guncelle)

    # ================= KAPATMA =================
    def _atexit_temizlik(self):
        try:
            self.pico.kes()
        except Exception:
            pass

        try:
            self.motor.dur()
        except Exception:
            pass

        try:
            self.manuel.kapat()
        except Exception:
            pass

    def kapat(self):
        try:
            self._sistem_kapali()
        except Exception:
            pass

        try:
            self.asama1.dur()
        except Exception:
            pass

        try:
            self.asama2.dur()
        except Exception:
            pass

        try:
            self.asama3.dur()
        except Exception:
            pass

        try:
            self.pico.kes()
        except Exception:
            pass

        try:
            self.motor.dur()
        except Exception:
            pass

        try:
            self.manuel.kapat()
        except Exception:
            pass

        try:
            self.kok.destroy()
        except Exception:
            pass
