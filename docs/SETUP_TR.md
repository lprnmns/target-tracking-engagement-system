# ISTIKLAL Command Center

TEKNOFEST balon takip, Pico 2 motor kontrol, YOLO görüntü işleme ve servo tetik kalibrasyon arayüzü.

Bu repo geliştirme/kontrol yazılımını içerir. Büyük model dosyaları ve çalışma çıktıları repoya eklenmez:

- `models/**/*.pt`
- `logs/**`
- `config/runtime/**`
- `frontend/node_modules`
- Python sanal ortamları

## Windows'ta Sıfırdan Başlatma

Bu bölüm `uv` kurulu olmayan bir Windows bilgisayarda sistemi tamamen ayağa kaldırmak içindir.

### 1. Gerekli Programlar

Kurulu olmalı:

- Python 3.12 veya üstü
- Git
- Node.js LTS

Kontrol:

```bat
python --version
git --version
node --version
```

Node ile gelen Corepack'i aç:

```bat
corepack enable
corepack prepare pnpm@11.0.8 --activate
pnpm --version
```

### 2. Projeyi İndir

```bat
git clone https://github.com/lprnmns/teknofest-balon-takip-sistemi.git
cd teknofest-balon-takip-sistemi
```

### 3. YOLO Model Dosyasını Yerleştir

Model dosyaları GitHub'a konulmadı. Balon modeli şu path'te olmalı:

```text
models\packages\legacy-balloon-yolo-0.1.0\model.pt
```

Klasör yoksa oluştur:

```bat
mkdir models\packages\legacy-balloon-yolo-0.1.0
```

Sonra `model.pt` dosyasını bu klasöre koy.

### 4. Backend Kurulumu

`uv` olmadan standart Python venv kullan:

```bat
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e .
cd ..
```

İsteğe bağlı test:

```bat
backend\.venv\Scripts\python -m pip install -e backend[dev]
cd backend
.venv\Scripts\python -m pytest
cd ..
```

### 5. Frontend Build

Backend tek başına arayüzü servis edebilsin diye frontend'i build et:

```bat
cd frontend
pnpm install
pnpm build
cd ..
```

Bu işlem `frontend\dist\index.html` üretmeli.

### 6. Donanım Portlarını Kontrol Et

Pico ve USB kamera takılıyken Windows Aygıt Yöneticisi'nden portları kontrol et.

Config dosyası:

```text
config\config.yaml
```

Gerekirse şu alanları değiştir:

```yaml
pico:
  port: "COM3"

serial:
  port: "COM3"

camera:
  source: 0
  camera_source: 0
```

Windows'ta kamera genelde `/dev/video2` değil, `0`, `1`, `2` gibi indekslerle açılır. İlk deneme için `0` kullan.

### 7. Sistemi Başlat

Tek pencere:

```bat
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tarayıcıdan aç:

```text
http://127.0.0.1:8000
```

Backend `frontend/dist` klasörünü bulursa arayüzü aynı porttan servis eder.

## Windows Geliştirme Modu

Frontend'i canlı geliştirme sunucusuyla çalıştırmak istersen iki terminal aç.

Terminal 1, backend:

```bat
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal 2, frontend:

```bat
cd frontend
echo VITE_BACKEND_API_URL=http://127.0.0.1:8000> .env.local
echo VITE_BACKEND_WS_URL=ws://127.0.0.1:8000/ws>> .env.local
pnpm dev --host 127.0.0.1 --port 5173
```

Tarayıcı:

```text
http://127.0.0.1:5173
```

## uv ile Kurulum

`uv` kullanmak istersen:

```bat
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Yeni terminal aç, sonra:

```bat
cd backend
uv sync --extra dev
uv run pytest
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Linux/macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd backend
uv sync --extra dev
uv run pytest
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Hızlı Kontrol

Backend ayakta mı:

```text
http://127.0.0.1:8000/api/serial/status
http://127.0.0.1:8000/api/camera/status
http://127.0.0.1:8000/api/vision/status
```

Arayüzde önemli ekranlar:

- `Vision`: kamera, YOLO kutuları, takip seçimi
- `Motion`: tracking durumu ve PID ayarları
- `Pico`: Pico bağlantısı ve servo tetik kalibrasyon paneli
- `Yarışma Konsolu`: yarışma sırasında ana ekran; canlı görüntü, hedef seçimi, PID/conf/kamera tuning, darboğaz, port yönetimi, şarjör ve görev puanı

## Yarışma Günü Operatör Akışı

1. Pico, USB kamera ve güç bağlantılarını tak.
2. Backend'i başlat ve tarayıcıda `http://127.0.0.1:8000` adresini aç.
3. `Yarışma Konsolu` ekranına geç.
4. `Cihaz ve Port Yönetimi` altında aktif kamerayı ve Pico portunu kontrol et. USB girişini değiştirdiysen ilgili kamerayı veya Pico portunu seçip uygula.
5. Üst durum rozetlerinde `KAMERA AKIŞI VAR`, `FRAME TAZE`, `PICO SAĞLIKLI` ve `KOMUT HATTI TEMİZ` durumlarını görmeden denemeye başlama.
6. `Canlı Tuning` panelinden YOLO `conf`, kamera parlaklık/kontrast ve PID değerlerini ayarla. Canlı checkbox açıkken değerler otomatik uygulanır; şüphede ilgili `uygula` butonuna bas.
7. Kamera görüntüsünde balon bbox'una tıkla. Seçilen hedef yeşile döner ve tracking hedefi olur.
8. `Görev ve Angajman` bölümünde yarışma aşamasını seç. Aşama 1 manuel atış, Aşama 2 otonom takip, Aşama 3 dost/düşman ve menzil kapısı mantığıyla değerlendirilir.
9. Şarjör/lazer kapasitesi varsayılan `8`dir. Her tetikte azalır; sıfıra düşünce atış yapma. Yeni deneme için `Şarjörü 8’e Resetle`.
10. `Performans ve Darboğaz` kartında kırmızı/sarı teşhis varsa önce önerilen aksiyonları uygula. Kamera frame yaşı, YOLO inference, tracking loop, serial ACK, Pico heartbeat, TX queue ve toplam gecikme ayrı ayrı izlenir.
11. Test veya jüri gösterimi bitince `KTR Kanıt Merkezi` üzerinden KTR/demo/readiness export üret. Görev puanı ve görev kaydı `mission_evidence.md/json` içine girer.

### Hızlı Hata Kararı

- `Kamera frame akışı gecikiyor`: Kamera portu, FPS/çözünürlük ve USB kablo/port kontrol edilir.
- `YOLO inference bütçeyi aşıyor`: `imgsz`, `frame_skip`, GPU seçimi ve model presetleri kontrol edilir.
- `Serial komut kuyruğu birikiyor`: Tracking komut Hz azaltılır, manuel ve otomatik komut aynı anda kullanılmaz.
- `Pico ACK gecikiyor` veya `Pico heartbeat sağlıksız`: Pico portu yeniden seçilir, kablo/baudrate/firmware kontrol edilir.
- `Şarjör boş`: Atış yapılmaz; kapasite panelinden resetlenmeden devam edilmez.

## Pico Firmware

Aktif yarışma yolu, 30 Temmuz gerçek taret/kamera takip testinde PASS veren
MicroPython field baseline'dır:

```text
firmware/pico2/main.py
firmware/ACTIVE_FIRMWARE.json
```

Bu yol 1/8 mikrostep, doğrudan `SPD` hız eşleme ve JSON-line
`PING/STAT/DRV/ARM/SPD/STP/LZR` ACK sözleşmesini kullanır. TMC_STEALTH_2.1
Arduino build'i (`eski_sistem_arayüz/pico_arduino/...`) arşiv/A-B adayıdır; aktif
profil olarak kullanılmaz.

Firmware varsayılan tetik ayarı:

```text
Başlangıç: 0 derece
Bitiş: 175 derece
Süre: backend tarafında 1.00 saniye
Cooldown: 5 saniye
```

Pico ekranındaki servo kalibrasyon paneli runtime'da derece gönderebilir; her deneme için firmware upload gerekmez.

## Sık Sorunlar

`frontend/dist bulunamadı`:

```bat
cd frontend
pnpm install
pnpm build
```

`Python sürümü yetersiz`:

Python 3.12+ kur ve `python --version` çıktısını kontrol et.

`Pico bağlanmıyor`:

Windows Aygıt Yöneticisi'nde Pico'nun COM portunu bul, `config\config.yaml` içinde `pico.port` ve `serial.port` değerlerini güncelle.

`Kamera açılmıyor`:

`config\config.yaml` içinde `camera.source` ve `camera.camera_source` değerlerini `0`, `1`, `2` olarak sırayla dene.

`YOLO kutu çizmiyor`:

`model.pt` dosyasının doğru yerde olduğundan emin ol:

```text
models\packages\legacy-balloon-yolo-0.1.0\model.pt
```

Sonra Vision ekranından runtime conf değerini düşürmeyi dene.
