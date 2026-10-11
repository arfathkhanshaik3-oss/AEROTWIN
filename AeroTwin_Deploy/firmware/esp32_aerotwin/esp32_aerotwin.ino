/*
 * AeroTwin telemetry node for ESP32  (Arduino core for ESP32)
 * Streams 1 Hz engine telemetry to the AeroTwin server over WiFi (HTTP POST /api/ingest).
 *
 * !!! NOT TESTED ON HARDWARE by the author - verify every sensor reading on the bench before flight !!!
 *
 * Suggested sensors (replace with yours; only the 4 values at the bottom matter to AeroTwin):
 *   CHT temperature : K-type thermocouple + MAX6675      (SPI: SCK=18, CS=5, SO=19)
 *   RPM             : hall / optical pickup, 1 pulse per revolution -> GPIO 27 (interrupt)
 *   Vibration       : MPU6050 on I2C (SDA=21, SCL=22), RMS of |a| - 1 g over the last second
 *   Oil pressure    : 0.5-4.5 V transducer (0-10 bar) -> voltage divider -> GPIO 34 (ADC1)
 * Optional: throttle (0..1) from your RC/servo signal, ambient temperature.
 */
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <SPI.h>

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER    = "http://192.168.1.50:8000/api/ingest";   // machine running AeroTwin
const char* API_KEY   = "";                                       // same as AEROTWIN_API_KEY, or empty

const int PIN_CS = 5, PIN_RPM = 27, PIN_PRESS = 34;
const int PULSES_PER_REV = 1;
const float PRESS_FULLSCALE_BAR = 10.0;           // 4.5 V = full scale
const float ADC_DIVIDER = 2.0 / 3.0;               // set to your divider ratio (Vadc / Vsensor)

volatile uint32_t pulses = 0;
void IRAM_ATTR onPulse() { pulses++; }

// ---- MAX6675 thermocouple (returns NAN if open circuit)
float readCHT() {
  digitalWrite(PIN_CS, LOW);
  uint16_t v = SPI.transfer16(0x0000);
  digitalWrite(PIN_CS, HIGH);
  if (v & 0x4) return NAN;
  return (v >> 3) * 0.25f;
}

// ---- MPU6050 vibration: RMS of (|a| - 1 g) over ~1 s, in g
const int MPU = 0x68;
float readVibrationRms(unsigned long ms = 900) {
  double acc = 0; int n = 0; unsigned long t0 = millis();
  while (millis() - t0 < ms) {
    Wire.beginTransmission(MPU); Wire.write(0x3B); if (Wire.endTransmission(false) != 0) return NAN;
    if (Wire.requestFrom(MPU, 6, true) != 6) return NAN;
    int16_t ax = Wire.read() << 8 | Wire.read(), ay = Wire.read() << 8 | Wire.read(), az = Wire.read() << 8 | Wire.read();
    float gx = ax / 16384.0f, gy = ay / 16384.0f, gz = az / 16384.0f;
    float d = sqrtf(gx * gx + gy * gy + gz * gz) - 1.0f;
    acc += d * d; n++; delay(2);
  }
  return n ? sqrtf(acc / n) + 0.0f : NAN;       // NOTE: calibrate/scale so a healthy engine reads ~0.5-1.0 g in your airframe
}

float readPressureBar() {
  float v = analogReadMilliVolts(PIN_PRESS) / 1000.0f / ADC_DIVIDER;      // sensor volts
  if (v < 0.3f || v > 4.8f) return NAN;                                    // wiring fault
  return (v - 0.5f) / 4.0f * PRESS_FULLSCALE_BAR;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_CS, OUTPUT); digitalWrite(PIN_CS, HIGH); SPI.begin();
  Wire.begin(21, 22); Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true);
  pinMode(PIN_RPM, INPUT_PULLUP); attachInterrupt(digitalPinToInterrupt(PIN_RPM), onPulse, RISING);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.println("\nWiFi OK " + WiFi.localIP().toString());
}

void loop() {
  unsigned long t0 = millis();
  noInterrupts(); pulses = 0; interrupts();
  float vib = readVibrationRms(900);                       // also acts as the ~1 s RPM gate
  noInterrupts(); uint32_t p = pulses; interrupts();
  float rpm = p * 60.0f * 1000.0f / (millis() - t0) / PULSES_PER_REV;
  float cht = readCHT(), press = readPressureBar();

  auto num = [](float x) { return isnan(x) ? String("null") : String(x, 3); };    // null => AeroTwin treats the channel as offline
  String body = "{\"temp\":" + num(cht) + ",\"rpm\":" + num(rpm) + ",\"vib\":" + num(vib) + ",\"press\":" + num(press) + "}";
  Serial.println(body);                                                           // also usable with AEROTWIN_LIVE_INPUT=serial

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http; http.begin(SERVER); http.addHeader("Content-Type", "application/json");
    if (strlen(API_KEY)) http.addHeader("X-API-Key", API_KEY);
    int code = http.POST(body); if (code != 200) Serial.printf("POST failed: %d\n", code);
    http.end();
  }
  long wait = 1000 - (long)(millis() - t0); if (wait > 0) delay(wait);
}
