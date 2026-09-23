/**
 * SMARTPARK - Entry Gate IoT Controller Firmware
 * Target Microcontroller: AI-Thinker ESP32-CAM
 * Peripherals:
 *  - Onboard OV2640 Camera
 *  - 1 x IR Obstacle Sensor (Vehicle Proximity Detection)
 *  - 1 x SG90 / MG995 5V Servo Motor (Barrier Boom Gate)
 *
 * NOTE: ESP32 communicates exclusively with Flask REST API via Wi-Fi.
 * It NEVER connects directly to Supabase PostgreSQL.
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>

// ============================================================
// NETWORK & SERVER CONFIGURATION
// ============================================================
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Replace with your Flask backend server IP address and port
const char* SERVER_URL    = "http://192.168.1.100:5000/api/iot/entry-camera";
const char* IOT_TOKEN     = "CHANGE_ME_IOT_TOKEN";

// ============================================================
// HARDWARE PIN DEFINITIONS (AI-Thinker Model)
// ============================================================
#define IR_SENSOR_PIN   13    // GPIO 13: Entry Proximity IR Sensor (Active LOW)
#define SERVO_PIN       12    // GPIO 12: Boom Barrier Servo Control (PWM)
#define FLASH_LED_PIN    4    // GPIO 4: High-brightness LED Flash (optional)

// Camera Pinout for AI-Thinker ESP32-CAM
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

Servo barrierServo;
bool vehiclePresent = false;
unsigned long lastTriggerTime = 0;
const unsigned long DEBOUNCE_DELAY_MS = 6000; // 6s debounce between entries

// ============================================================
// CAMERA INITIALIZATION
// ============================================================
bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size   = FRAMESIZE_VGA;  // 640x480 suitable for ANPR
    config.jpeg_quality = 12;
    config.fb_count     = 2;
  } else {
    config.frame_size   = FRAMESIZE_CIF;
    config.jpeg_quality = 14;
    config.fb_count     = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  return (err == ESP_OK);
}

// ============================================================
// BARRIER CONTROL FUNCTIONS
// ============================================================
void openBarrier() {
  Serial.println("[GATE] Opening Entry Barrier (90 degrees)");
  barrierServo.write(90);
  delay(5000); // Keep open for 5 seconds for car to pass
  Serial.println("[GATE] Closing Entry Barrier (0 degrees)");
  barrierServo.write(0);
}

// ============================================================
// IMAGE CAPTURE AND HTTP POST TO FLASK API
// ============================================================
void captureAndUploadVehicle() {
  Serial.println("[ANPR] Capturing license plate snapshot...");

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[ERROR] Camera capture failed!");
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "image/jpeg");
    http.addHeader("X-IoT-Token", IOT_TOKEN);

    int httpResponseCode = http.POST(fb->buf, fb->len);

    if (httpResponseCode == 200) {
      String response = http.getString();
      Serial.println("[SUCCESS] Entry approved by Flask server:");
      Serial.println(response);
      // Lift boom barrier
      openBarrier();
    } else {
      Serial.printf("[HTTP ERROR] Server responded with code: %d\n", httpResponseCode);
    }
    http.end();
  } else {
    Serial.println("[ERROR] Wi-Fi Disconnected!");
  }

  esp_camera_fb_return(fb);
}

// ============================================================
// SETUP & MAIN LOOP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== SMARTPARK Entry Gate ESP32-CAM Booting ===");

  pinMode(IR_SENSOR_PIN, INPUT_PULLUP);
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  // Initialize Servo
  barrierServo.attach(SERVO_PIN);
  barrierServo.write(0); // Start closed (0 deg)

  // Initialize Camera
  if (!initCamera()) {
    Serial.println("[FATAL] Camera initialization failed!");
  } else {
    Serial.println("[OK] OV2640 Camera Ready.");
  }

  // Connect to Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[OK] Wi-Fi Connected. IP: " + WiFi.localIP().toString());
  } else {
    Serial.println("\n[WARN] Wi-Fi timeout. Running in standalone loop.");
  }
}

void loop() {
  // Read IR Sensor (Active LOW when vehicle blocks the beam)
  int irState = digitalRead(IR_SENSOR_PIN);

  if (irState == LOW && !vehiclePresent) {
    unsigned long now = millis();
    if (now - lastTriggerTime > DEBOUNCE_DELAY_MS) {
      lastTriggerTime = now;
      vehiclePresent = true;
      Serial.println("\n[SENSOR] Vehicle detected at Entry Gate IR Sensor!");

      // Flash light briefly for illumination if dark
      digitalWrite(FLASH_LED_PIN, HIGH);
      delay(80);
      digitalWrite(FLASH_LED_PIN, LOW);

      // Capture and transmit image to Flask ANPR pipeline
      captureAndUploadVehicle();
    }
  } else if (irState == HIGH && vehiclePresent) {
    // Vehicle cleared sensor beam
    vehiclePresent = false;
  }

  delay(100);
}
