// Dedicated USB flow-sensor node for the soda dispenser.
// Connect up to nine digital pulse-output sensors to D2-D10; keep USB serial
// pins D0/D1 free. Sensor ground and Uno ground must be common.
// This reports pulse counts only. Calibrate each sensor before interpreting
// pulses as volume or flow rate.

#include <string.h>

const uint8_t FLOW_SENSOR_COUNT = 9;
const uint8_t flowSensorPins[FLOW_SENSOR_COUNT] = {2, 3, 4, 5, 6, 7, 8, 9, 10};
const unsigned long REPORT_INTERVAL_MS = 1000;
const unsigned long MIN_EDGE_INTERVAL_US = 1000;
const uint8_t COMMAND_BUFFER_SIZE = 48;

uint32_t windowPulseCounts[FLOW_SENSOR_COUNT] = {0};
uint32_t totalPulseCounts[FLOW_SENSOR_COUNT] = {0};
unsigned long lastEdgeUs[FLOW_SENSOR_COUNT] = {0};
uint8_t previousLevels[FLOW_SENSOR_COUNT] = {0};
char commandBuffer[COMMAND_BUFFER_SIZE];
uint8_t commandLength = 0;
bool commandOverflow = false;
unsigned long lastReportMs = 0;

void printStatus() {
  for (uint8_t sensorIndex = 0; sensorIndex < FLOW_SENSOR_COUNT; sensorIndex++) {
    Serial.print("FLOW ");
    Serial.print(sensorIndex + 1);
    Serial.print(' ');
    Serial.print(windowPulseCounts[sensorIndex]);
    Serial.print(' ');
    Serial.println(totalPulseCounts[sensorIndex]);
    windowPulseCounts[sensorIndex] = 0;
  }
}

void handleCommand(char* command) {
  if (strcmp(command, "PING") == 0) {
    Serial.println("PONG FLOW=9");
  } else if (strcmp(command, "STATUS") == 0) {
    printStatus();
    Serial.println("OK");
  } else if (strcmp(command, "RESETCOUNTS") == 0) {
    for (uint8_t sensorIndex = 0; sensorIndex < FLOW_SENSOR_COUNT; sensorIndex++) {
      windowPulseCounts[sensorIndex] = 0;
      totalPulseCounts[sensorIndex] = 0;
    }
    Serial.println("OK");
  } else {
    Serial.println("ERR unknown_command");
  }
}

void readCommands() {
  while (Serial.available() > 0) {
    char nextChar = (char)Serial.read();
    if (nextChar == '\n') {
      if (commandOverflow) {
        Serial.println("ERR line_too_long");
      } else {
        commandBuffer[commandLength] = '\0';
        handleCommand(commandBuffer);
      }
      commandLength = 0;
      commandOverflow = false;
    } else if (nextChar != '\r') {
      if (commandLength < COMMAND_BUFFER_SIZE - 1) {
        commandBuffer[commandLength++] = nextChar;
      } else {
        commandOverflow = true;
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  for (uint8_t sensorIndex = 0; sensorIndex < FLOW_SENSOR_COUNT; sensorIndex++) {
    pinMode(flowSensorPins[sensorIndex], INPUT_PULLUP);
    previousLevels[sensorIndex] = (uint8_t)digitalRead(flowSensorPins[sensorIndex]);
  }
  Serial.println("READY FLOW9");
  lastReportMs = millis();
}

void loop() {
  readCommands();

  unsigned long nowUs = micros();
  for (uint8_t sensorIndex = 0; sensorIndex < FLOW_SENSOR_COUNT; sensorIndex++) {
    uint8_t currentLevel = (uint8_t)digitalRead(flowSensorPins[sensorIndex]);
    if (previousLevels[sensorIndex] == HIGH && currentLevel == LOW &&
        (unsigned long)(nowUs - lastEdgeUs[sensorIndex]) >= MIN_EDGE_INTERVAL_US) {
      windowPulseCounts[sensorIndex]++;
      totalPulseCounts[sensorIndex]++;
      lastEdgeUs[sensorIndex] = nowUs;
    }
    previousLevels[sensorIndex] = currentLevel;
  }

  unsigned long nowMs = millis();
  if ((unsigned long)(nowMs - lastReportMs) >= REPORT_INTERVAL_MS) {
    printStatus();
    lastReportMs = nowMs;
  }
}