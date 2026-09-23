/**
 * SMARTPARK - Exit Gate IoT Controller Firmware
 * Target Microcontroller: AI-Thinker ESP32-CAM
 * Peripherals:
 *  - Onboard OV2640 Camera
 *  - 1 x IR Obstacle Sensor (Exit Detection)
 *  - 1 x SG90 / MG995 5V Servo Motor (Exit Barrier Boom Gate)
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
const char* SERVER_URL    = "http://192.168.1.100:5000/api/exit/simulate";
const char* IOT_TOKEN     = "CHANGE_ME_IOT_TOKEN";

// Hardware Pins
#define IR_SENSOR_PIN   13    // GPIO 13: Exit Proximity IR Sensor (Active LOW)
#define SERVO_PIN       12    // GPIO 12: Exit Boom Barrier Servo Control (PWM)
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

Servo exitBarrierServo;
bool vehiclePresent = false;
unsigned long lastTriggerTime = 0;
const unsigned long DEBOUNCE_DELAY_MS = 6000;

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_1;
  config.ledc_timer   = LEDC_TIMER_1;
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
    config.frame_size   = FRAMESIZE_VGA;
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

void openExitBarrier() {
  Serial.println("[EXIT GATE] Opening Barrier Boom (90 degrees)");
  exitBarrierServo.write(90);
  delay(5000);
  Serial.println("[EXIT GATE] Closing Barrier Boom (0 degrees)");
  exitBarrierServo.write(0);
}

void processExitVehicle() {
  Serial.println("[ANPR] Capturing exit vehicle snapshot...");

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[ERROR] Exit camera capture failed!");
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-IoT-Token", IOT_TOKEN);

    // Send exit validation request (in production, plate is recognized via backend)
    String jsonPayload = "{\"trigger_source\":\"ESP32_CAM\"}";
    int httpResponseCode = http.POST(jsonPayload);

    if (httpResponseCode == 200) {
      Serial.println("[SUCCESS] Exit payment verified. Releasing barrier.");
      openExitBarrier();
    } else if (httpResponseCode == 402) {
      Serial.println("[DENIED] Parking fee unpaid! Exit barrier remains closed.");
    } else {
      Serial.printf("[HTTP ERROR] Exit code: %d\n", httpResponseCode);
    }
    http.end();
  }

  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== SMARTPARK Exit Gate ESP32-CAM Booting ===");

  pinMode(IR_SENSOR_PIN, INPUT_PULLUP);
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  exitBarrierServo.attach(SERVO_PIN);
  exitBarrierServo.write(0);

  if (!initCamera()) {
    Serial.println("[FATAL] Exit Camera initialization failed!");
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[OK] Wi-Fi Connected. IP: " + WiFi.localIP().toString());
  }
}

void loop() {
  int irState = digitalRead(IR_SENSOR_PIN);

  if (irState == LOW && !vehiclePresent) {
    unsigned long now = millis();
    if (now - lastTriggerTime > DEBOUNCE_DELAY_MS) {
      lastTriggerTime = now;
      vehiclePresent = true;
      Serial.println("\n[SENSOR] Vehicle detected at Exit Gate!");

      processExitVehicle();
    }
  } else if (irState == HIGH && vehiclePresent) {
    vehiclePresent = false;
  }

  delay(100);
}
