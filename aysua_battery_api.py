#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = os.getenv("BATTERY_API_HOST", "127.0.0.1")
PORT = int(os.getenv("BATTERY_API_PORT", "8095"))
I2C_BUS = int(os.getenv("BATTERY_I2C_BUS", "1"))

MAX17048_ADDR = int(os.getenv("MAX17048_ADDR", "0x36"), 0)
BQ25792_ADDR = int(os.getenv("BQ25792_ADDR", "0x6B"), 0)

DIVIDER_RATIO = float(os.getenv("BATTERY_DIVIDER_RATIO", "2.0"))
VOLTAGE_CAL = float(os.getenv("BATTERY_VOLTAGE_CAL", "1.0"))
VOLTAGE_OFFSET_V = float(os.getenv("BATTERY_VOLTAGE_OFFSET_V", "0.42"))

PACK_EMPTY_V = float(os.getenv("BATTERY_PACK_EMPTY_V", "6.40"))
PACK_FULL_V = float(os.getenv("BATTERY_PACK_FULL_V", "8.40"))
EMA_ALPHA = float(os.getenv("BATTERY_EMA_ALPHA", "0.25"))

# BQ25792: REG 0x1C, bit 5-7
BQ_CHG_STATUS_REG = int(os.getenv("BQ_CHG_STATUS_REG", "0x1C"), 0)
BQ_CHG_STATUS_SHIFT = int(os.getenv("BQ_CHG_STATUS_SHIFT", "5"))
BQ_CHG_STATUS_MASK = int(os.getenv("BQ_CHG_STATUS_MASK", "0x07"), 0)

I2C_SLAVE = 0x0703


class I2CError(Exception):
    pass


def i2c_path(bus):
    return f"/dev/i2c-{bus}"


def i2c_read_block(bus, addr, reg, length):
    try:
        with open(i2c_path(bus), "r+b", buffering=0) as f:
            fcntl.ioctl(f, I2C_SLAVE, addr)
            f.write(bytes([reg & 0xFF]))
            data = f.read(length)
            if len(data) != length:
                raise I2CError(f"Kisa I2C okuma: {len(data)}/{length}")
            return data
    except FileNotFoundError:
        raise I2CError(f"/dev/i2c-{bus} yok. I2C aktif degil veya bus numarasi yanlis.")
    except PermissionError:
        raise I2CError(f"/dev/i2c-{bus} izin hatasi. Servisi root calistirin.")
    except OSError as e:
        raise I2CError(f"I2C okuma hatasi addr=0x{addr:02X} reg=0x{reg:02X}: {e}")


def i2c_read_u8(bus, addr, reg):
    return i2c_read_block(bus, addr, reg, 1)[0]


def read_max17048_input_v():
    # MAX17048 VCELL register: 0x02-0x03, 12-bit, LSB=1.25mV
    data = i2c_read_block(I2C_BUS, MAX17048_ADDR, 0x02, 2)
    raw16 = (data[0] << 8) | data[1]
    return (raw16 >> 4) * 0.00125


def read_voltage_values():
    max_input_v = read_max17048_input_v()
    raw_pack_v = max_input_v * DIVIDER_RATIO
    calibrated_pack_v = raw_pack_v * VOLTAGE_CAL
    corrected_pack_v = calibrated_pack_v + VOLTAGE_OFFSET_V
    return {
        "max17048_input_v": max_input_v,
        "raw_pack_voltage_v": raw_pack_v,
        "calibrated_pack_voltage_v": calibrated_pack_v,
        "corrected_pack_voltage_v": corrected_pack_v,
    }


def percent_from_voltage(v):
    if v <= PACK_EMPTY_V:
        return 0
    if v >= PACK_FULL_V:
        return 100
    points = [
        (6.40, 0), (7.00, 10), (7.20, 20), (7.40, 40),
        (7.70, 60), (8.00, 80), (8.20, 90), (8.40, 100),
    ]
    for (v0, p0), (v1, p1) in zip(points, points[1:]):
        if v0 <= v <= v1:
            pct = p0 + (v - v0) * (p1 - p0) / (v1 - v0)
            return int(round(max(0, min(100, pct))))
    linear = (v - PACK_EMPTY_V) / (PACK_FULL_V - PACK_EMPTY_V) * 100.0
    return int(round(max(0, min(100, linear))))


def bars_from_percent(percent):
    if percent <= 0:
        return 0
    if percent <= 20:
        return 1
    if percent <= 40:
        return 2
    if percent <= 60:
        return 3
    if percent <= 80:
        return 4
    return 5


def interpret_chg_stat(chg_stat):
    if chg_stat == 0:
        return {
            "charging": False,
            "charge_done": False,
            "charge_state": "not_charging",
            "charge_state_tr": "Sarj olmuyor",
        }
    if chg_stat == 7:
        return {
            "charging": False,
            "charge_done": True,
            "charge_state": "charge_done",
            "charge_state_tr": "Sarj doldu",
        }
    return {
        "charging": True,
        "charge_done": False,
        "charge_state": "charging",
        "charge_state_tr": "Sarj oluyor",
    }


def read_bq25792_charge_status():
    reg_value = i2c_read_u8(I2C_BUS, BQ25792_ADDR, BQ_CHG_STATUS_REG)
    chg_stat = (reg_value >> BQ_CHG_STATUS_SHIFT) & BQ_CHG_STATUS_MASK
    interp = interpret_chg_stat(chg_stat)
    return {
        "available": True,
        "source": f"i2c-reg-0x{BQ_CHG_STATUS_REG:02X}-bits-5-7",
        "register": f"0x{BQ_CHG_STATUS_REG:02X}",
        "reg_value": reg_value,
        "reg_value_hex": f"0x{reg_value:02X}",
        "reg_value_bin": format(reg_value, "08b"),
        "bit_range": "5-7",
        "chg_stat": chg_stat,
        "chg_stat_bin": format(chg_stat, "03b"),
        **interp,
    }


class BatteryState:
    def __init__(self):
        self.filtered_v = None
        self.last = None

    def read(self):
        now = int(time.time())
        try:
            bq = read_bq25792_charge_status()
        except Exception as e:
            bq = {
                "available": False,
                "source": f"i2c-reg-0x{BQ_CHG_STATUS_REG:02X}-bits-5-7",
                "register": f"0x{BQ_CHG_STATUS_REG:02X}",
                "bit_range": "5-7",
                "chg_stat": None,
                "charging": False,
                "charge_done": False,
                "charge_state": "unknown",
                "charge_state_tr": "Bilinmiyor",
                "error": str(e),
            }

        try:
            vv = read_voltage_values()
            pack_v = vv["corrected_pack_voltage_v"]
            if self.filtered_v is None:
                self.filtered_v = pack_v
            else:
                self.filtered_v = EMA_ALPHA * pack_v + (1.0 - EMA_ALPHA) * self.filtered_v

            percent = percent_from_voltage(self.filtered_v)
            result = {
                "ok": True,
                "battery": percent,
                "percent": percent,
                "bars": bars_from_percent(percent),
                "charging": bool(bq.get("charging", False)),
                "charge_done": bool(bq.get("charge_done", False)),
                "charge_state": bq.get("charge_state"),
                "charge_state_tr": bq.get("charge_state_tr"),
                "voltage_v": round(pack_v, 3),
                "filtered_voltage_v": round(self.filtered_v, 3),
                "cell_voltage_est_v": round(self.filtered_v / 2.0, 3),
                "max17048_input_v": round(vv["max17048_input_v"], 4),
                "raw_pack_voltage_v": round(vv["raw_pack_voltage_v"], 3),
                "calibrated_pack_voltage_v": round(vv["calibrated_pack_voltage_v"], 3),
                "voltage_offset_v": round(VOLTAGE_OFFSET_V, 3),
                "divider_ratio": DIVIDER_RATIO,
                "voltage_cal": VOLTAGE_CAL,
                "i2c_bus": I2C_BUS,
                "max17048_addr": f"0x{MAX17048_ADDR:02X}",
                "bq25792_addr": f"0x{BQ25792_ADDR:02X}",
                "bq": bq,
                "timestamp": now,
            }
            self.last = result
            return result
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "battery": self.last.get("battery", 0) if self.last else 0,
                "charging": bool(bq.get("charging", False)),
                "charge_done": bool(bq.get("charge_done", False)),
                "charge_state": bq.get("charge_state"),
                "charge_state_tr": bq.get("charge_state_tr"),
                "bq": bq,
                "timestamp": now,
            }


STATE = BatteryState()


class Handler(BaseHTTPRequestHandler):
    server_version = "AysuaBatteryAPI/1.3"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/health"):
            self.send_json(200, {
                "ok": True,
                "service": "aysua-battery-api",
                "version": "1.3",
                "endpoint": "/api/battery",
                "bq_register": f"0x{BQ_CHG_STATUS_REG:02X}",
                "bq_bits": "5-7",
            })
            return
        if path == "/api/battery":
            payload = STATE.read()
            self.send_json(200 if payload.get("ok") else 503, payload)
            return
        self.send_json(404, {"ok": False, "error": "not_found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    print(f"Aysua Battery API v1.3: http://{HOST}:{PORT}/api/battery", flush=True)
    print(f"BQ CHG_STAT: reg=0x{BQ_CHG_STATUS_REG:02X}, bits=5-7", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
