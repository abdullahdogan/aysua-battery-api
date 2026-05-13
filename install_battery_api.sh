#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/aysua-battery-api"
SERVICE_FILE="/etc/systemd/system/aysua-battery-api.service"

echo "[1/5] I2C araçları kuruluyor..."
sudo apt-get update
sudo apt-get install -y i2c-tools python3 curl

echo "[2/5] Uygulama klasörü hazırlanıyor..."
sudo mkdir -p "$APP_DIR"
sudo cp aysua_battery_api.py "$APP_DIR/aysua_battery_api.py"
sudo chmod +x "$APP_DIR/aysua_battery_api.py"

echo "[3/5] systemd servisi kuruluyor..."
sudo cp aysua-battery-api.service "$SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable aysua-battery-api.service

echo "[4/5] Servis başlatılıyor..."
sudo systemctl restart aysua-battery-api.service

echo "[5/5] Test ediliyor..."
sleep 1
systemctl --no-pager --full status aysua-battery-api.service || true
echo
curl -s http://127.0.0.1:8095/api/battery || true
echo
