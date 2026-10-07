#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <BLEDevice.h>
#include <BLESecurity.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

#include <mutex>

#include "secrets.h"

#if ARDUINOJSON_VERSION_MAJOR != 7
#error "This sketch requires ArduinoJson 7.x"
#endif

// GPIO13 drives an S8050 NPN low-side switch. This circuit has no relay.
constexpr uint8_t OUTPUT_PIN_1 = 13;
constexpr uint8_t OUTPUT_ON_LEVEL = HIGH;
constexpr uint8_t OUTPUT_OFF_LEVEL = LOW;
constexpr uint32_t MAX_PULSE_MS = 10000;
constexpr uint32_t MAX_LEASE_MS = 10000;
constexpr uint32_t EVENT_DEDUPE_TTL_MS = 10UL * 60UL * 1000UL;
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 25000;
constexpr size_t EVENT_CACHE_SIZE = 32;
constexpr size_t MAX_COMMAND_BYTES = 768;

static const char* BLE_SERVICE_UUID = "8a7c0001-3f72-4a1d-9c10-56495347554e";
static const char* BLE_INFO_UUID = "8a7c0002-3f72-4a1d-9c10-56495347554e";
static const char* BLE_RX_UUID = "8a7c0003-3f72-4a1d-9c10-56495347554e";
static const char* BLE_RESULT_UUID = "8a7c0004-3f72-4a1d-9c10-56495347554e";

WebServer server(80);
Preferences preferences;
BLECharacteristic* infoCharacteristic = nullptr;
BLECharacteristic* resultCharacteristic = nullptr;

struct RelayState {
  bool on = false;
  uint32_t started_at_ms = 0;
  uint32_t duration_ms = 0;
  String mode;
};

struct ProcessedEvent {
  String id;
  uint32_t accepted_at_ms = 0;
  bool valid = false;
};

enum class WifiChangeStage : uint8_t { Idle, ConnectingNew, RollingBack };

RelayState relayState;
ProcessedEvent processedEvents[EVENT_CACHE_SIZE];
size_t nextEventSlot = 0;
String deviceId;
String activeSsid;
String activePassword;
String oldSsid;
String oldPassword;
String requestedSsid;
String requestedPassword;
String wifiRequestId;
String lastWifiRequestId;
String lastWifiFinalResult;
String lastBleResult = "{\"state\":\"idle\"}";
WifiChangeStage wifiChangeStage = WifiChangeStage::Idle;
uint32_t wifiChangeStartedAt = 0;
uint32_t lastWifiAttempt = 0;
bool serverStarted = false;
bool mdnsStarted = false;
std::mutex commandMutex;
String pendingCommand;
bool hasPendingCommand = false;

uint32_t relayRemainingMs() {
  if (!relayState.on) return 0;
  const uint32_t elapsed = static_cast<uint32_t>(millis() - relayState.started_at_ms);
  return elapsed >= relayState.duration_ms ? 0 : relayState.duration_ms - elapsed;
}

void turnOffRelay() {
  digitalWrite(OUTPUT_PIN_1, OUTPUT_OFF_LEVEL);
  relayState.on = false;
  relayState.duration_ms = 0;
  relayState.mode = "off";
}

void turnOnRelay(uint32_t durationMs, const String& mode) {
  digitalWrite(OUTPUT_PIN_1, OUTPUT_ON_LEVEL);
  relayState.on = true;
  relayState.started_at_ms = millis();
  relayState.duration_ms = durationMs;
  relayState.mode = mode;
}

void updateRelayTimer() {
  if (relayState.on && static_cast<uint32_t>(millis() - relayState.started_at_ms) >= relayState.duration_ms) {
    turnOffRelay();
    Serial.println("[output] auto OFF (timer expired)");
  }
}

String makeDeviceId() {
  char buffer[24];
  const uint64_t mac = ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL;
  snprintf(buffer, sizeof(buffer), "VG-ESP32-%012llX", static_cast<unsigned long long>(mac));
  return String(buffer);
}

String makeInfoJson() {
  JsonDocument doc;
  doc["device_id"] = deviceId;
  doc["name"] = deviceId;
  doc["wifi_connected"] = WiFi.status() == WL_CONNECTED;
  doc["wifi_ssid"] = WiFi.status() == WL_CONNECTED ? WiFi.SSID() : "";
  doc["ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "";
  doc["relay_state"] = relayState.on ? "on" : "off";
  doc["remaining_ms"] = relayRemainingMs();
  String output;
  serializeJson(doc, output);
  return output;
}

void publishBleResult(JsonDocument& doc) {
  serializeJson(doc, lastBleResult);
  if (resultCharacteristic != nullptr) {
    resultCharacteristic->setValue(lastBleResult.c_str());
    resultCharacteristic->notify();
  }
  if (infoCharacteristic != nullptr) infoCharacteristic->setValue(makeInfoJson().c_str());
}

void publishBleError(const String& requestId, const char* code, const char* message) {
  JsonDocument doc;
  doc["ok"] = false;
  doc["request_id"] = requestId;
  doc["state"] = "error";
  doc["error"] = code;
  doc["message"] = message;
  publishBleResult(doc);
}

void sendHttpJson(int statusCode, JsonDocument& doc) {
  String output;
  serializeJson(doc, output);
  server.send(statusCode, "application/json", output);
}

void sendHttpError(int statusCode, const char* code, const char* message) {
  JsonDocument doc;
  doc["ok"] = false;
  doc["error"] = code;
  doc["message"] = message;
  sendHttpJson(statusCode, doc);
}

bool secureKeyEquals(const String& provided, const char* expected) {
  const size_t expectedLength = strlen(expected);
  if (provided.length() != expectedLength) return false;
  uint8_t difference = 0;
  for (size_t i = 0; i < expectedLength; ++i) {
    difference |= static_cast<uint8_t>(provided[i]) ^ static_cast<uint8_t>(expected[i]);
  }
  return difference == 0;
}

bool isHttpAuthenticated() {
  return secureKeyEquals(server.header("X-API-Key"), VG_API_KEY);
}

bool parseHttpBody(JsonDocument& doc) {
  if (!server.hasArg("plain") || server.arg("plain").length() == 0 || server.arg("plain").length() > MAX_COMMAND_BYTES) {
    sendHttpError(400, "invalid_body", "Expected a non-empty JSON object");
    return false;
  }
  const DeserializationError error = deserializeJson(doc, server.arg("plain"));
  if (error || !doc.is<JsonObject>()) {
    sendHttpError(400, "invalid_json", "Expected a JSON object");
    return false;
  }
  return true;
}

bool eventAlreadyProcessed(const String& id) {
  const uint32_t now = millis();
  for (ProcessedEvent& item : processedEvents) {
    if (!item.valid) continue;
    if (static_cast<uint32_t>(now - item.accepted_at_ms) >= EVENT_DEDUPE_TTL_MS) {
      item.valid = false;
      item.id = "";
    } else if (item.id == id) {
      return true;
    }
  }
  return false;
}

void rememberEvent(const String& id) {
  ProcessedEvent& item = processedEvents[nextEventSlot];
  item.id = id;
  item.accepted_at_ms = millis();
  item.valid = true;
  nextEventSlot = (nextEventSlot + 1) % EVENT_CACHE_SIZE;
}

bool parsePulse(JsonDocument& doc, const String& requestId, bool viaBle) {
  const char* eventValue = doc["event_id"] | "";
  const String eventId(eventValue);
  const uint32_t durationMs = doc["duration_ms"] | 0;
  if (eventId.isEmpty() || eventId.length() > 96 || durationMs == 0 || durationMs > MAX_PULSE_MS) {
    if (viaBle) publishBleError(requestId, "invalid_pulse", "event_id or duration_ms is invalid");
    else sendHttpError(400, "invalid_pulse", "event_id and duration_ms (1..10000) are required");
    return false;
  }
  if (eventAlreadyProcessed(eventId)) {
    JsonDocument response;
    response["ok"] = true;
    response["request_id"] = requestId;
    response["event_id"] = eventId;
    response["already_processed"] = true;
    response["commanded_state"] = relayState.on ? "on" : "off";
    if (viaBle) publishBleResult(response); else sendHttpJson(200, response);
    return true;
  }
  if (relayState.on) {
    if (viaBle) {
      JsonDocument response;
      response["ok"] = false;
      response["request_id"] = requestId;
      response["error"] = "relay_busy";
      response["retry_after_ms"] = relayRemainingMs();
      publishBleResult(response);
    } else {
      JsonDocument response;
      response["ok"] = false;
      response["error"] = "relay_busy";
      response["retry_after_ms"] = relayRemainingMs();
      sendHttpJson(409, response);
    }
    return false;
  }
  turnOnRelay(durationMs, "pulse");
  rememberEvent(eventId);
  JsonDocument response;
  response["ok"] = true;
  response["request_id"] = requestId;
  response["event_id"] = eventId;
  response["relay_id"] = "1";
  response["commanded_state"] = "on";
  response["pulse_ms"] = durationMs;
  if (viaBle) publishBleResult(response); else sendHttpJson(200, response);
  return true;
}

void handleHealth() {
  JsonDocument doc;
  doc["ok"] = true;
  doc["device_id"] = deviceId;
  doc["wifi_connected"] = WiFi.status() == WL_CONNECTED;
  doc["uptime_ms"] = millis();
  sendHttpJson(200, doc);
}

void handleStatus() {
  if (!isHttpAuthenticated()) {
    sendHttpError(401, "unauthorized", "Missing or invalid X-API-Key");
    return;
  }
  JsonDocument doc;
  doc["ok"] = true;
  doc["device_id"] = deviceId;
  JsonObject relay = doc["relays"]["1"].to<JsonObject>();
  relay["commanded_state"] = relayState.on ? "on" : "off";
  relay["remaining_ms"] = relayRemainingMs();
  sendHttpJson(200, doc);
}

void handleHttpPulse() {
  if (!isHttpAuthenticated()) {
    sendHttpError(401, "unauthorized", "Missing or invalid X-API-Key");
    return;
  }
  JsonDocument doc;
  if (parseHttpBody(doc)) parsePulse(doc, String(doc["event_id"] | ""), false);
}

void handleOccupancy() {
  if (!isHttpAuthenticated()) {
    sendHttpError(401, "unauthorized", "Missing or invalid X-API-Key");
    return;
  }
  JsonDocument doc;
  if (!parseHttpBody(doc)) return;
  const String state = doc["state"] | "";
  if (state == "off") {
    turnOffRelay();
    JsonDocument response;
    response["ok"] = true;
    response["commanded_state"] = "off";
    sendHttpJson(200, response);
    return;
  }
  const uint32_t leaseMs = doc["lease_ms"] | 0;
  if (state != "on" || leaseMs == 0 || leaseMs > MAX_LEASE_MS) {
    sendHttpError(400, "invalid_state", "Use state=on with lease_ms 1..10000, or state=off");
    return;
  }
  if (relayState.on && relayState.mode == "pulse") {
    sendHttpError(409, "relay_busy", "A pulse is running");
    return;
  }
  turnOnRelay(leaseMs, "occupancy");
  JsonDocument response;
  response["ok"] = true;
  response["commanded_state"] = "on";
  response["lease_ms"] = leaseMs;
  sendHttpJson(200, response);
}

void onInfoRead(BLECharacteristic* characteristic) {
  characteristic->setValue(makeInfoJson().c_str());
}

class InfoCallbacks : public BLECharacteristicCallbacks {
  void onRead(BLECharacteristic* characteristic) override {
    onInfoRead(characteristic);
  }
};

class CommandCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    const String frame = characteristic->getValue();
    if (frame.length() < 3) return;
    const uint8_t index = static_cast<uint8_t>(frame[0]);
    const uint8_t count = static_cast<uint8_t>(frame[1]);
    if (count == 0 || index >= count) return;

    static String assembled;
    static uint8_t expectedIndex = 0;
    static uint8_t expectedCount = 0;
    if (index == 0) {
      assembled = "";
      expectedIndex = 0;
      expectedCount = count;
    }
    if (index != expectedIndex || count != expectedCount) {
      assembled = "";
      expectedIndex = 0;
      expectedCount = 0;
      return;
    }
    for (size_t i = 2; i < frame.length(); ++i) assembled += static_cast<char>(frame[i]);
    if (assembled.length() > MAX_COMMAND_BYTES) {
      assembled = "";
      expectedIndex = 0;
      expectedCount = 0;
      return;
    }
    expectedIndex++;
    if (expectedIndex < expectedCount) return;

    {
      std::lock_guard<std::mutex> guard(commandMutex);
      pendingCommand = assembled;
      hasPendingCommand = true;
    }
    assembled = "";
    expectedIndex = 0;
    expectedCount = 0;
  }
};

void startBleService() {
  deviceId = makeDeviceId();
  BLEDevice::init(deviceId);
  BLESecurity::setAuthenticationMode(true, false, true);  // bonding + LE Secure Connections
  BLESecurity::setCapability(ESP_IO_CAP_NONE);
  BLEServer* bleServer = BLEDevice::createServer();
  BLEService* service = bleServer->createService(BLE_SERVICE_UUID);

  infoCharacteristic = service->createCharacteristic(BLE_INFO_UUID, BLECharacteristic::PROPERTY_READ);
  infoCharacteristic->setCallbacks(new InfoCallbacks());
  infoCharacteristic->setValue(makeInfoJson().c_str());

  BLECharacteristic* rx = service->createCharacteristic(
      BLE_RX_UUID, BLECharacteristic::PROPERTY_WRITE);
  rx->setAccessPermissions(ESP_GATT_PERM_WRITE_ENCRYPTED);
  rx->setCallbacks(new CommandCallbacks());

  resultCharacteristic = service->createCharacteristic(
      BLE_RESULT_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  resultCharacteristic->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED);
  resultCharacteristic->setValue(lastBleResult.c_str());

  service->start();
  BLEAdvertising* advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->start();
  Serial.printf("[BLE] advertising as %s\n", deviceId.c_str());
}

void queueWifiChange(const JsonDocument& doc, const String& requestId) {
  const String ssid = doc["ssid"] | "";
  const String password = doc["password"] | "";
  if (ssid.isEmpty() || ssid.length() > 32 || password.length() > 63 ||
      (password.length() > 0 && password.length() < 8)) {
    publishBleError(requestId, "invalid_wifi", "SSID or password length is invalid");
    return;
  }
  if (wifiChangeStage != WifiChangeStage::Idle) {
    if (requestId == wifiRequestId) {
      JsonDocument response;
      response["ok"] = true;
      response["request_id"] = requestId;
      response["state"] = "wifi_connecting";
      response["ssid"] = requestedSsid;
      publishBleResult(response);
      return;
    }
    publishBleError(requestId, "busy", "A Wi-Fi change is already running");
    return;
  }
  if (!lastWifiRequestId.isEmpty() && requestId == lastWifiRequestId && !lastWifiFinalResult.isEmpty()) {
    lastBleResult = lastWifiFinalResult;
    if (resultCharacteristic != nullptr) {
      resultCharacteristic->setValue(lastBleResult.c_str());
      resultCharacteristic->notify();
    }
    return;
  }
  requestedSsid = ssid;
  requestedPassword = password;
  wifiRequestId = requestId;
  wifiChangeStage = WifiChangeStage::ConnectingNew;
  wifiChangeStartedAt = 0;
  JsonDocument response;
  response["ok"] = true;
  response["request_id"] = requestId;
  response["state"] = "wifi_connecting";
  response["ssid"] = requestedSsid;
  publishBleResult(response);
}

void processBleCommand() {
  String raw;
  {
    std::lock_guard<std::mutex> guard(commandMutex);
    if (!hasPendingCommand) return;
    raw = pendingCommand;
    pendingCommand = "";
    hasPendingCommand = false;
  }
  JsonDocument doc;
  if (deserializeJson(doc, raw) || !doc.is<JsonObject>()) {
    publishBleError("", "invalid_json", "Command must be a JSON object");
    return;
  }
  const String op = doc["op"] | "";
  const String requestId = doc["request_id"] | "";
  if (requestId.isEmpty() || requestId.length() > 96) {
    publishBleError("", "invalid_request_id", "request_id is required");
    return;
  }
  if (op == "pulse") {
    parsePulse(doc, requestId, true);
  } else if (op == "configure_wifi") {
    queueWifiChange(doc, requestId);
  } else {
    publishBleError(requestId, "unknown_operation", "Unsupported BLE command");
  }
}

void beginWifiChange() {
  oldSsid = activeSsid;
  oldPassword = activePassword;
  WiFi.disconnect(false, false);
  WiFi.begin(requestedSsid.c_str(), requestedPassword.c_str());
  wifiChangeStartedAt = millis();
  Serial.printf("[wifi] trying configured network: %s\n", requestedSsid.c_str());
}

void finishWifiChange(bool connected) {
  JsonDocument response;
  response["ok"] = connected;
  response["request_id"] = wifiRequestId;
  response["state"] = connected ? "wifi_configured" : "wifi_failed";
  response["ssid"] = connected ? requestedSsid : oldSsid;
  response["wifi_connected"] = WiFi.status() == WL_CONNECTED;
  response["ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "";
  if (!connected) response["error"] = "새 Wi-Fi에 연결하지 못했습니다. 이전 설정으로 복귀했습니다.";
  publishBleResult(response);
  lastWifiRequestId = wifiRequestId;
  lastWifiFinalResult = lastBleResult;
  if (connected) Serial.printf("[wifi] connected: %s\n", requestedSsid.c_str());
  else Serial.println("[wifi] new credentials rejected; previous credentials restored");
  requestedSsid = "";
  requestedPassword = "";
  wifiRequestId = "";
  oldSsid = "";
  oldPassword = "";
  wifiChangeStage = WifiChangeStage::Idle;
}

void updateWifiChange() {
  if (wifiChangeStage == WifiChangeStage::Idle) return;
  if (wifiChangeStartedAt == 0) {
    beginWifiChange();
    return;
  }
  if (WiFi.status() == WL_CONNECTED) {
    if (wifiChangeStage == WifiChangeStage::ConnectingNew) {
      activeSsid = requestedSsid;
      activePassword = requestedPassword;
      preferences.putString("ssid", activeSsid);
      preferences.putString("password", activePassword);
      finishWifiChange(true);
      return;
    }
    if (wifiChangeStage == WifiChangeStage::RollingBack) {
      activeSsid = oldSsid;
      activePassword = oldPassword;
      finishWifiChange(false);
      return;
    }
  }
  if (static_cast<uint32_t>(millis() - wifiChangeStartedAt) < WIFI_CONNECT_TIMEOUT_MS) return;
  if (wifiChangeStage == WifiChangeStage::ConnectingNew) {
    wifiChangeStage = WifiChangeStage::RollingBack;
    WiFi.disconnect(false, false);
    if (oldSsid.length() > 0) {
      WiFi.begin(oldSsid.c_str(), oldPassword.c_str());
      wifiChangeStartedAt = millis();
    } else {
      finishWifiChange(false);
    }
  } else {
    // No old network was reachable. BLE remains available for another settings attempt.
    activeSsid = "";
    activePassword = "";
    finishWifiChange(false);
  }
}

void startHttpServices() {
  if (!serverStarted) {
    server.begin();
    serverStarted = true;
    Serial.println("[http] server started on port 80 (diagnostics/local fallback)");
  }
  if (!mdnsStarted && MDNS.begin(VG_MDNS_HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
    mdnsStarted = true;
  }
}

void maintainWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    startHttpServices();
    server.handleClient();
    if (infoCharacteristic != nullptr) infoCharacteristic->setValue(makeInfoJson().c_str());
    return;
  }
  if (mdnsStarted) {
    MDNS.end();
    mdnsStarted = false;
  }
  if (serverStarted) {
    server.stop();
    serverStarted = false;
  }
  if (wifiChangeStage != WifiChangeStage::Idle || activeSsid.isEmpty()) return;
  if (static_cast<uint32_t>(millis() - lastWifiAttempt) >= 10000) {
    lastWifiAttempt = millis();
    WiFi.begin(activeSsid.c_str(), activePassword.c_str());
  }
}

void setup() {
  Serial.begin(115200);
  digitalWrite(OUTPUT_PIN_1, OUTPUT_OFF_LEVEL);
  pinMode(OUTPUT_PIN_1, OUTPUT);
  turnOffRelay();

  deviceId = makeDeviceId();
  preferences.begin("vg-wifi", false);
  activeSsid = preferences.getString("ssid", VG_WIFI_SSID);
  activePassword = preferences.getString("password", VG_WIFI_PASSWORD);
  if (activeSsid == "YOUR_WIFI_SSID") activeSsid = "";

  const char* headerKeys[] = {"X-API-Key"};
  server.collectHeaders(headerKeys, 1);
  server.on("/api/v1/health", HTTP_GET, handleHealth);
  server.on("/api/v1/status", HTTP_GET, handleStatus);
  server.on("/api/v1/relays/1/pulse", HTTP_POST, handleHttpPulse);
  server.on("/api/v1/relays/1", HTTP_PUT, handleOccupancy);
  server.onNotFound([]() { sendHttpError(404, "not_found", "Unknown endpoint"); });

  startBleService();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);  // controlled reconnect/rollback is handled below
  if (!activeSsid.isEmpty()) {
    WiFi.begin(activeSsid.c_str(), activePassword.c_str());
    lastWifiAttempt = millis();
    Serial.printf("[wifi] connecting to saved network: %s\n", activeSsid.c_str());
  } else {
    Serial.println("[wifi] no saved SSID; configure Wi-Fi from the dashboard over BLE");
  }
  Serial.printf("[device] id=%s, output=GPIO13\n", deviceId.c_str());
}

void loop() {
  processBleCommand();
  updateWifiChange();
  maintainWifi();
  updateRelayTimer();
  delay(2);
}
