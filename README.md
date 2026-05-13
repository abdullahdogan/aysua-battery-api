# Aysua Battery API

MAX17048 + BQ25792 kullanan AysuaSpect cihazları için bağımsız batarya API servisidir.

Bu servis mevcut AysuaSpect backend dosyasına dokunmadan ayrı bir portta çalışır ve batarya bilgisini JSON olarak yayınlar.

```text
API endpoint:
http://127.0.0.1:8095/api/battery
```

## Özellikler

- MAX17048 üzerinden batarya gerilimi okuma
- 2S bataryayı gerilim bölücü ile ölçme desteği
- BQ25792 üzerinden şarj durumunu okuma
- 5 bar batarya göstergesi için yüzde üretme
- AysuaSpect arayüzü ile kullanılabilecek `battery` alanı
- systemd servisi ile otomatik başlatma
- Mevcut AysuaSpect backend dosyasına müdahale etmeden çalışma

## Donanım varsayımları

Varsayılan yapı:

```text
Batarya       : 2S Li-ion / LiPo
Fuel gauge    : MAX17048
Charger IC    : BQ25792
MAX17048 I2C  : 0x36
BQ25792 I2C   : 0x6B
Gerilim bölücü: 15k / 15k
```

15k / 15k gerilim bölücü için:

```text
BATTERY_DIVIDER_RATIO=2.0
```

Ölçümde sabit düşük okuma varsa:

```text
BATTERY_VOLTAGE_OFFSET_V=0.42
```

Paket gerilimi şu şekilde hesaplanır:

```text
voltage_v =
    (MAX17048_input_v × BATTERY_DIVIDER_RATIO × BATTERY_VOLTAGE_CAL)
    + BATTERY_VOLTAGE_OFFSET_V
```

## BQ25792 şarj durumu

Şarj durumu BQ25792 register `0x1C` içindeki bit `5-7` alanından okunur.

```text
reg_value = read(0x1C)
chg_stat = (reg_value >> 5) & 0x07
```

Değerlendirme:

| chg_stat | Anlam | API |
|---:|---|---|
| 0 | Şarj olmuyor | `charging=false` |
| 1-6 | Şarj oluyor | `charging=true` |
| 7 | Şarj doldu | `charging=false`, `charge_done=true` |

Elle register kontrolü:

```bash
sudo i2cget -y 1 0x6b 0x1c
```

I2C bus 10 ise:

```bash
sudo i2cget -y 10 0x6b 0x1c
```

## Yüzde hesabı

Kodda kullanılan yaklaşık 2S Li-ion gerilim/yüzde tablosu:

| 2S paket gerilimi | Yüzde |
|---:|---:|
| 6.40 V | 0 |
| 7.00 V | 10 |
| 7.20 V | 20 |
| 7.40 V | 40 |
| 7.70 V | 60 |
| 8.00 V | 80 |
| 8.20 V | 90 |
| 8.40 V | 100 |

Ara değerlerde doğrusal interpolasyon yapılır.

Örneğin:

```text
7.85 V ≈ %70
```

Bu yöntem coulomb-counting değildir. Gerilim tabanlı yaklaşık göstergedir.

## Kurulum

```bash
git clone https://github.com/abdullahdogan/aysua-battery-api.git
cd aysua-battery-api
chmod +x install_battery_api.sh
sudo bash install_battery_api.sh
```

Kurulumdan sonra test:

```bash
curl -s http://127.0.0.1:8095/api/battery
```

Örnek çıktı:

```json
{
  "ok": true,
  "battery": 72,
  "percent": 72,
  "bars": 4,
  "charging": true,
  "charge_done": false,
  "charge_state": "charging",
  "charge_state_tr": "Şarj oluyor",
  "voltage_v": 7.88,
  "filtered_voltage_v": 7.86,
  "max17048_input_v": 3.73,
  "raw_pack_voltage_v": 7.46,
  "voltage_offset_v": 0.42,
  "bq": {
    "register": "0x1C",
    "reg_value_hex": "0x40",
    "reg_value_bin": "01000000",
    "bit_range": "5-7",
    "chg_stat": 2,
    "chg_stat_bin": "010"
  }
}
```

## Servis ayarları

Servis dosyası:

```bash
/etc/systemd/system/aysua-battery-api.service
```

Düzenleme:

```bash
sudo nano /etc/systemd/system/aysua-battery-api.service
```

Değişiklik sonrası:

```bash
sudo systemctl daemon-reload
sudo systemctl restart aysua-battery-api.service
```

Log kontrolü:

```bash
journalctl -u aysua-battery-api.service -n 100 --no-pager
```

Durum kontrolü:

```bash
systemctl status aysua-battery-api.service
```

## I2C bus değiştirme

Servis dosyasında şu satırı değiştir:

```ini
Environment=BATTERY_I2C_BUS=1
```

Örnek bus 10 için:

```ini
Environment=BATTERY_I2C_BUS=10
```

Adres kontrolü:

```bash
sudo i2cdetect -y 1
sudo i2cdetect -y 10
```

Beklenen adresler:

```text
0x36 -> MAX17048
0x6B -> BQ25792
```

## AysuaSpect arayüz entegrasyonu

Bu servis 8095 portunda çalışır.

AysuaSpect ana backend genelde 8080 portundadır:

```text
AysuaSpect backend : http://127.0.0.1:8080
Battery API        : http://127.0.0.1:8095
```

Bu yüzden AysuaSpect arayüzünün batarya verisini bu servisten alması için iki frontend dosyasında küçük değişiklik gerekir.

### 1. `top_battery.js` değişikliği

Dosya:

```bash
~/AysuaSpect/web/static/top_battery.js
```

Eski satır:

```javascript
const resp = await fetch('/api/battery', { cache: 'no-store' });
```

Yeni satır:

```javascript
const batteryApiUrl = `http://${window.location.hostname}:8095/api/battery`;
const resp = await fetch(batteryApiUrl, { cache: 'no-store' });
```

### 2. `scan.html` değişikliği

Scan sayfası kendi batarya göstergesini kullandığı için ayrıca düzenlenmelidir.

Dosya:

```bash
~/AysuaSpect/web/pages/scan.html
```

Eski satır:

```javascript
const resp = await fetch('/api/battery');
```

Yeni satır:

```javascript
const resp = await fetch(`http://${window.location.hostname}:8095/api/battery`, { cache: 'no-store' });
```

Tek komutla değiştirmek için:

```bash
cp ~/AysuaSpect/web/pages/scan.html ~/AysuaSpect/web/pages/scan.html.bak.$(date +%Y%m%d_%H%M%S)

sed -i "s|const resp = await fetch('/api/battery');|const resp = await fetch(\`http://\${window.location.hostname}:8095/api/battery\`, { cache: 'no-store' });|g" ~/AysuaSpect/web/pages/scan.html
```

Sonra kiosk yeniden başlatılır:

```bash
sudo systemctl restart aysuaspect-kiosk.service
```

Cache sorunu olursa:

```bash
pkill chromium
sudo systemctl restart aysuaspect-kiosk.service
```

## GitHub'a yükleme

Yeni repo oluşturduktan sonra:

```bash
git init
git add .
git commit -m "Add Aysua battery API service"
git branch -M main
git remote add origin https://github.com/KULLANICI_ADIN/aysua-battery-api.git
git push -u origin main
```

GitHub CLI kullanıyorsan:

```bash
gh repo create aysua-battery-api --public --source=. --remote=origin --push
```

Private repo için:

```bash
gh repo create aysua-battery-api --private --source=. --remote=origin --push
```

## Dosya yapısı

```text
aysua-battery-api/
├── aysua_battery_api.py
├── install_battery_api.sh
├── aysua-battery-api.service
├── README.md
├── LICENSE
└── .gitignore
```

## Notlar

- Bu servis AysuaSpect backend binary dosyasını değiştirmez.
- `/api/battery` endpointini 8095 portunda sağlar.
- AysuaSpect frontend tarafında 8095'e yönlendirme yapılmalıdır.
- Yüzde hesabı gerilim tabanlı yaklaşık hesaplamadır.
- Hassas kapasite hesabı için 2S destekli fuel gauge veya coulomb-counting gerekir.
