// Pump controller + 16x2 I2C LCD (Arduino Uno). No extra library needed: only Wire (built in).
// LCD backpack (PCF8574) wiring: GND->GND, VCC->5V, SDA->A4, SCL->A5. Pump pins are unchanged.
// If you only see solid blocks, turn the blue contrast trimmer on the back of the LCD.
//
// Pump commands: FIRE id ms speed | START id speed | STOP id | STOPALL
// Safety/status: PING | STATUS | BEEP 1/2/3 | RESET_ESTOP
// D8 = isolated NC emergency-stop sense; D12 = active buzzer driver.
// LCD commands (sent by display.py, replies OK or ERR ...):
//   LCDINIT                 find the LCD (0x27 or 0x3F) and initialise it
//   LCDBL <0|1>             backlight
//   LCDCLR                  clear
//   LCDCHR <slot 0-7> <16 hex chars>    define a custom character
//   LCDROW <row 0-1> <32 hex chars>     write 16 character codes to a row

#include <Wire.h>
#define LCD_I2C_HZ 400000L   // if the LCD shows garbage, change to 100000L

const int PUMP_COUNT = 9;
const int pumpPins[PUMP_COUNT] = {3, 5, 6, 9, 10, 11, 2, 4, 7};
const uint8_t ESTOP_SENSE_PIN = 8;
const uint8_t BUZZER_PIN = 12;
const unsigned long HOST_TIMEOUT_MS = 5000;
const unsigned long MAX_PUMP_MS = 120000;

unsigned long endTime[PUMP_COUNT] = {0};
bool running[PUMP_COUNT] = {false};
bool manualHold[PUMP_COUNT] = {false};
String buf = "";
bool inputOverflow = false;
bool estopLatched = false;
bool hostSeen = false;
bool hostOnline = false;
unsigned long lastHostMs = 0;
unsigned long lastStatusMs = 0;
unsigned long bootMs = 0;
bool offlineAnnounced = false;
uint16_t beepSegments[7] = {0};
uint8_t beepSegmentCount = 0;
uint8_t beepSegmentIndex = 0;
unsigned long beepDeadline = 0;
bool buzzerOn = false;

uint8_t lcdAddr = 0;
uint8_t lcdBL = 0x08;
bool lcdOK = false;

// ---------- minimal HD44780 over PCF8574 ----------
void expWrite(uint8_t v) {
  Wire.beginTransmission(lcdAddr);
  Wire.write(v | lcdBL);
  Wire.endTransmission();
}
void lcdNibble(uint8_t nib, uint8_t rs) {
  uint8_t v = (nib << 4) | rs;        // P0=RS P1=RW P2=E P3=BL P4-7=D4-D7
  expWrite(v | 0x04);
  expWrite(v & ~0x04);
  delayMicroseconds(40);
}
void lcdByte(uint8_t b, uint8_t rs) { lcdNibble(b >> 4, rs); lcdNibble(b & 15, rs); }
void lcdCmd(uint8_t c) { lcdByte(c, 0); if (c < 4) delay(2); }
bool i2cPresent(uint8_t a) { Wire.beginTransmission(a); return Wire.endTransmission() == 0; }

bool lcdInit() {
  lcdAddr = 0;
  if (i2cPresent(0x27)) lcdAddr = 0x27;
  else if (i2cPresent(0x3F)) lcdAddr = 0x3F;
  if (!lcdAddr) return false;
  delay(50);
  expWrite(0);
  delay(5);
  lcdNibble(3, 0); delay(5);
  lcdNibble(3, 0); delay(5);
  lcdNibble(3, 0); delay(1);
  lcdNibble(2, 0);                    // 4-bit mode
  lcdCmd(0x28); lcdCmd(0x0C); lcdCmd(0x01); lcdCmd(0x06);
  return true;
}
void lcdText(uint8_t row, const char* s) {
  lcdCmd(0x80 | (row ? 0x40 : 0));
  for (uint8_t column = 0; column < 16; column++) {
    lcdByte(*s ? *s++ : ' ', 1);
  }
}

void showSafetyScreen(const char* line1, const char* line2) {
  if (!lcdOK) return;
  lcdText(0, line1);
  lcdText(1, line2);
}

void startBeepPattern(const uint16_t* segments, uint8_t count) {
  if (count > 7) count = 7;
  for (uint8_t i = 0; i < count; i++) beepSegments[i] = segments[i];
  beepSegmentCount = count;
  beepSegmentIndex = 0;
  buzzerOn = count > 0;
  digitalWrite(BUZZER_PIN, buzzerOn ? HIGH : LOW);
  beepDeadline = millis() + (count ? beepSegments[0] : 0);
}

void serviceBuzzer() {
  while (beepSegmentIndex < beepSegmentCount && (long)(millis() - beepDeadline) >= 0) {
    beepSegmentIndex++;
    if (beepSegmentIndex >= beepSegmentCount) {
      beepSegmentCount = 0;
      buzzerOn = false;
      digitalWrite(BUZZER_PIN, LOW);
      return;
    }
    buzzerOn = (beepSegmentIndex % 2) == 0;
    digitalWrite(BUZZER_PIN, buzzerOn ? HIGH : LOW);
    beepDeadline += beepSegments[beepSegmentIndex];
  }
}

void stopAllPumps() {
  for (int i = 0; i < PUMP_COUNT; i++) {
    analogWrite(pumpPins[i], 0);
    running[i] = false;
    manualHold[i] = false;
    endTime[i] = 0;
  }
}

void tripEmergencyStop() {
  if (estopLatched) return;
  estopLatched = true;
  stopAllPumps();
  const uint16_t pattern[] = {90, 80, 90, 80, 90, 140, 700};
  startBeepPattern(pattern, 7);
  showSafetyScreen("EMERGENCY STOP", "PUMPS DISABLED");
  Serial.println("EVENT ESTOP");
}

int hv(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}
bool hexBytes(const String& s, int from, uint8_t* out, int n) {
  if ((int)s.length() < from + 2 * n) return false;
  for (int i = 0; i < n; i++) {
    int a = hv(s[from + 2 * i]), b = hv(s[from + 2 * i + 1]);
    if (a < 0 || b < 0) return false;
    out[i] = (a << 4) | b;
  }
  return true;
}

void handleLcd(const String& line) {
  if (line == "LCDINIT") {
    lcdOK = lcdInit();
    Serial.println(lcdOK ? "OK" : "ERR no_lcd");
    return;
  }
  if (!lcdOK) { Serial.println("ERR no_lcd"); return; }
  if (line.startsWith("LCDBL ")) {
    lcdBL = line.substring(6).toInt() ? 0x08 : 0;
    expWrite(0);
    Serial.println("OK");
    return;
  }
  if (line == "LCDCLR") { lcdCmd(0x01); Serial.println("OK"); return; }
  if (line.startsWith("LCDCHR ")) {
    int slot = line.substring(7, 8).toInt();
    uint8_t d[8];
    if (slot < 0 || slot > 7 || !hexBytes(line, 9, d, 8)) { Serial.println("ERR bad_format"); return; }
    lcdCmd(0x40 | (slot << 3));
    for (int i = 0; i < 8; i++) lcdByte(d[i], 1);
    lcdCmd(0x80);
    Serial.println("OK");
    return;
  }
  if (line.startsWith("LCDROW ")) {
    if (estopLatched) { Serial.println("OK"); return; }
    int row = line.substring(7, 8).toInt();
    uint8_t d[16];
    if (row < 0 || row > 1 || !hexBytes(line, 9, d, 16)) { Serial.println("ERR bad_format"); return; }
    lcdCmd(0x80 | (row ? 0x40 : 0));
    for (int i = 0; i < 16; i++) lcdByte(d[i], 1);
    Serial.println("OK");
    return;
  }
  Serial.println("ERR unknown_command");
}

// ---------- pumps ----------
void setup() {
  Serial.begin(9600);
  bootMs = millis();
  pinMode(ESTOP_SENSE_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  for (int i = 0; i < PUMP_COUNT; i++) {
    pinMode(pumpPins[i], OUTPUT);
    analogWrite(pumpPins[i], 0);
  }
  Wire.begin();
  Wire.setClock(LCD_I2C_HZ);
  lcdOK = lcdInit();
  showSafetyScreen("SODA DISPENSER", "WAITING FOR HOST");
  Serial.println(lcdOK ? "READY PUMP9 LCD=1" : "READY PUMP9 LCD=0");
}

void startPump(int idx, int speedPct, unsigned long durationMs, bool hold) {
  int pwmVal = map(constrain(speedPct, 0, 100), 0, 100, 0, 255);
  analogWrite(pumpPins[idx], pwmVal);
  running[idx] = true;
  manualHold[idx] = hold;
  endTime[idx] = hold ? 0 : millis() + durationMs;
}

void stopPump(int idx) {
  analogWrite(pumpPins[idx], 0);
  running[idx] = false;
  manualHold[idx] = false;
}

void handleLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  unsigned long now = millis();
  lastHostMs = now;
  bool wasOnline = hostOnline;
  hostSeen = true;
  hostOnline = true;
  if (!wasOnline) {
    offlineAnnounced = false;
    const uint16_t pattern[] = {100, 120, 100};
    startBeepPattern(pattern, 3);
    if (!estopLatched) showSafetyScreen("BACKEND CONNECTED", "PUMPS READY");
    Serial.println("EVENT HOST_CONNECTED");
  }

  if (line == "PING") {
    Serial.print("PONG ESTOP=");
    Serial.println(estopLatched ? 1 : 0);
    return;
  }
  if (line == "STATUS") {
    Serial.print("STATE ESTOP=");
    Serial.print(estopLatched ? 1 : 0);
    Serial.print(" HOST=1 LCD=");
    Serial.print(lcdOK ? 1 : 0);
    Serial.print(" ACTIVE=");
    uint8_t active = 0;
    for (int i = 0; i < PUMP_COUNT; i++) if (running[i]) active++;
    Serial.println(active);
    return;
  }
  if (line.startsWith("BEEP ")) {
    int beepCount = line.substring(5).toInt();
    if (beepCount == 1) {
      const uint16_t pattern[] = {120};
      startBeepPattern(pattern, 1);
    } else if (beepCount == 2) {
      const uint16_t pattern[] = {100, 100, 100};
      startBeepPattern(pattern, 3);
    } else if (beepCount == 3) {
      const uint16_t pattern[] = {700};
      startBeepPattern(pattern, 1);
    } else {
      Serial.println("ERR bad_beep");
      return;
    }
    Serial.println("OK");
    return;
  }
  if (line == "RESET_ESTOP") {
    if (digitalRead(ESTOP_SENSE_PIN) != LOW) {
      Serial.println("ERR estop_switch_open");
      return;
    }
    estopLatched = false;
    showSafetyScreen("SODA DISPENSER", "BACKEND CONNECTED");
    const uint16_t pattern[] = {120};
    startBeepPattern(pattern, 1);
    Serial.println("EVENT ESTOP_RESET");
    Serial.println("OK");
    return;
  }

  if (line.startsWith("LCD")) { handleLcd(line); return; }

  if (line == "STOPALL") {
    stopAllPumps();
    Serial.println("OK");
    return;
  }

  if (line.startsWith("STOP ")) {
    int pumpId = line.substring(5).toInt();
    if (pumpId < 1 || pumpId > PUMP_COUNT) { Serial.println("ERR bad_pump"); return; }
    stopPump(pumpId - 1);
    Serial.println("OK");
    return;
  }

  if (line.startsWith("START ")) {
    int p1 = line.indexOf(' ');
    int p2 = line.indexOf(' ', p1 + 1);
    if (p2 < 0) { Serial.println("ERR bad_format"); return; }
    int pumpId = line.substring(p1 + 1, p2).toInt();
    int speed = line.substring(p2 + 1).toInt();
    if (pumpId < 1 || pumpId > PUMP_COUNT) { Serial.println("ERR bad_pump"); return; }
    if (speed < 1 || speed > 100) { Serial.println("ERR bad_speed"); return; }
    if (estopLatched) { Serial.println("ERR estop_latched"); return; }
    startPump(pumpId - 1, speed, 0, true);
    Serial.println("OK");
    return;
  }

  if (line.startsWith("FIRE")) {
    int p1 = line.indexOf(' ');
    int p2 = line.indexOf(' ', p1 + 1);
    int p3 = line.indexOf(' ', p2 + 1);
    if (p1 < 0 || p2 < 0 || p3 < 0) { Serial.println("ERR bad_format"); return; }
    int pumpId = line.substring(p1 + 1, p2).toInt();
    unsigned long ms = line.substring(p2 + 1, p3).toInt();
    int speed = line.substring(p3 + 1).toInt();
    if (pumpId < 1 || pumpId > PUMP_COUNT) { Serial.println("ERR bad_pump"); return; }
    if (speed < 1 || speed > 100) { Serial.println("ERR bad_speed"); return; }
    if (ms < 1 || ms > MAX_PUMP_MS) { Serial.println("ERR bad_duration"); return; }
    if (estopLatched) { Serial.println("ERR estop_latched"); return; }
    startPump(pumpId - 1, speed, ms, false);
    Serial.println("OK");
    return;
  }

  Serial.println("ERR unknown_command");
}

void loop() {
  serviceBuzzer();
  if (digitalRead(ESTOP_SENSE_PIN) != LOW) tripEmergencyStop();

  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (inputOverflow) Serial.println("ERR line_too_long");
      else handleLine(buf);
      buf = "";
      inputOverflow = false;
    } else if (c != '\r') {
      if (buf.length() < 96) buf += c;
      else inputOverflow = true;
    }
  }

  unsigned long now = millis();
  bool online = hostSeen && now - lastHostMs <= HOST_TIMEOUT_MS;
  if (hostOnline && !online) {
    hostOnline = false;
    offlineAnnounced = true;
    stopAllPumps();
    const uint16_t pattern[] = {650};
    startBeepPattern(pattern, 1);
    showSafetyScreen("BACKEND OFFLINE", "PUMPS STOPPED");
    Serial.println("EVENT BACKEND_LOST");
  }
  if (!hostSeen && !offlineAnnounced && now - bootMs >= HOST_TIMEOUT_MS) {
    offlineAnnounced = true;
    const uint16_t pattern[] = {650};
    startBeepPattern(pattern, 1);
    showSafetyScreen("BACKEND OFFLINE", "CHECK USB LINK");
    Serial.println("EVENT BACKEND_MISSING");
  }
  if (estopLatched && now - lastStatusMs >= 1000) {
    showSafetyScreen("EMERGENCY STOP", "PUMPS DISABLED");
    lastStatusMs = now;
  } else if (hostSeen && !hostOnline && !estopLatched && now - lastStatusMs >= 1000) {
    showSafetyScreen("BACKEND OFFLINE", "PUMPS STOPPED");
    lastStatusMs = now;
  }

  for (int i = 0; i < PUMP_COUNT; i++) {
    if (running[i] && !manualHold[i] && (long)(now - endTime[i]) >= 0) {
      stopPump(i);
      Serial.print("DONE ");
      Serial.println(i + 1);
    }
  }
}
