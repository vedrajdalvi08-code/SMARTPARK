# SMARTPARK - IoT Hardware Integration Guide

This directory contains optional firmware and wiring specifications for integrating physical IoT hardware with the **SMARTPARK** system.

> [!IMPORTANT]
> **SOFTWARE-FIRST PRINCIPLE**:
> The entire SMARTPARK application can operate **100% standalone** when Demo Mode is enabled from the authenticated `/admin` panel.
> You **do not** need physical ESP32 boards, IR sensors, or servo motors to run, test, or demonstrate this project.
> All hardware actions are fully simulatable via the authenticated Admin Web Console (`/admin`).

---

## 1. Hardware Bill of Materials (BOM)

| Component | Quantity | Purpose |
| :--- | :--- | :--- |
| **AI-Thinker ESP32-CAM** | 2 | Entry & Exit gate ANPR cameras with Wi-Fi |
| **FTDI USB-to-TTL Adapter** | 1 | For flashing ESP32-CAM (3.3V/5V) |
| **IR Obstacle Sensors** | 4 | Vehicle detection at Entry, Exit, and sample bays (A-01, B-01) |
| **SG90 / MG995 5V Servo Motors** | 2 | Physical boom barrier arms (0° Closed, 90° Open) |
| **5V / 2A External Power Supply** | 1 | Powers servos and ESP32-CAM stably (avoids brownouts) |
| **Breadboard & Jumper Wires** | 1 Set | Prototyping interconnections |

---

## 2. Pin Mapping & Wiring Diagram

### Entry Gate ESP32-CAM (`entry.ino`)

| ESP32-CAM Pin | Peripheral | Pin | Notes |
| :--- | :--- | :--- | :--- |
| **5V** | External Power | +5V Rail | Recommended 2A supply |
| **GND** | External Power & Sensors | GND Rail | Common ground required |
| **GPIO 13** | IR Proximity Sensor | OUT (Signal) | Active LOW on vehicle detection |
| **GPIO 12** | SG90 Servo Motor | PWM (Orange/Yellow) | Controls barrier boom angle |
| **GPIO 4** | Onboard Flash LED | Internal | Provides lighting in dark conditions |

### Exit Gate ESP32-CAM (`exit.ino`)

| ESP32-CAM Pin | Peripheral | Pin | Notes |
| :--- | :--- | :--- | :--- |
| **5V** | External Power | +5V Rail | Recommended 2A supply |
| **GND** | Common Ground | GND Rail | Common ground required |
| **GPIO 13** | Exit IR Sensor | OUT (Signal) | Triggers exit checkout flow |
| **GPIO 12** | Exit Servo Motor | PWM (Orange/Yellow) | Opens barrier boom upon verified payment |

---

## 3. Communication & Security Architecture

```
[ ESP32-CAM ] 
      │ (Wi-Fi HTTP POST with JPEG / JSON)
      ▼
[ Flask REST API: /api/iot/... ]
      │
      ▼
[ ANPR Pipeline & Min-Heap DSA ]
      │
      ▼
[ Supabase PostgreSQL Database ]
```

> [!CAUTION]
> **Security Rule**: The ESP32 microcontrollers **NEVER** connect directly to Supabase PostgreSQL.
> All hardware telemetry and image captures are routed through the secure Flask REST API gateway with bearer authentication (`X-IoT-Token: CHANGE_ME_IOT_TOKEN`).

---

## 4. How to Flash Firmware

1. Install [Arduino IDE](https://www.arduino.cc/en/software) (2.x recommended).
2. Install **ESP32 Board Package**:
   - Go to `File` -> `Preferences` -> `Additional Board Manager URLs`.
   - Add: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`.
3. Install required libraries from Library Manager:
   - `ESP32Servo` by Kevin Harrington
4. Select Board: **AI Thinker ESP32-CAM**.
5. Connect FTDI programmer:
   - `FTDI TX` -> `ESP32 U0R`
   - `FTDI RX` -> `ESP32 U0T`
   - `FTDI VCC` -> `ESP32 5V`
   - `FTDI GND` -> `ESP32 GND`
   - **Bridge `GPIO 0` to `GND` during flashing!**
6. Open `entry.ino` or `exit.ino`, update `WIFI_SSID`, `WIFI_PASSWORD`, and `SERVER_URL`.
7. Click **Upload**. Once completed, remove the `GPIO 0` to `GND` jumper and press the RESET button.
