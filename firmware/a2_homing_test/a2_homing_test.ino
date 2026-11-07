/*
  a2_homing_test.ino - MTG card sorter: base homing test (bottom module only)

  Standalone test (no API). After power-up / reset:

    0. SENSOR CHECK (10 s, motor off): turn the base by hand. Every change of the
       IR is reported ("IR: STRIP" / "IR: no strip"), so you can see whether the
       sensor really switches on the strip and off it before anything moves.
    1. HOMING
       - If the IR already sees the strip, back off (reverse) until it is clear.
       - Seek forward with the same ramp as the stepper test (max 0.5 turn/s)
         until the IR sees the strip, then brake smoothly (this overshoots a bit).
       - Come back at low speed, move off the strip, then approach it slowly so the stop
         is always on the same edge of the strip (repeatable home position).
    2. REPEATABILITY TEST (TEST_TURNS times)
       - From home, go round once until the strip is found again, the same way.
       - Reports the steps of that turn and pauses 3 s on the strip, so you can
         also check by eye (put a pointer mark next to the strip).
    3. Stays at home with the motor energized and repeats the summary every 5 s.

  RESULTS ARE SENT AS TEXT OVER RS-485 (the Arduino has no USB connected).
  On the laptop: stop the API, then open a terminal on the USB-RS485 adapter:
      python -m serial.tools.miniterm /dev/cu.usbserial-110 9600
  Lines look like:  [base] Turn 1: 9600 steps

  If the strip is not found, the driver goes to sleep (motor goes limp) and
  "HOMING FAILED" is repeated every 5 s. Press reset to try again.

  Speed limit: never faster than the stepper test (MAX_RPS = 0.5 motor turn/s at
  1/16 step = 1600 steps/s), same 800-step ramp starting 4x slower.

  Wiring
    A5  homing IR sensor (reflective strip)
    D6  DRV8825 DIR      D5  STEP      D4  SLEEP     D3  RESET     D2  M2 (HIGH = 1/16)
    D13 MAX485 DE+RE (HIGH only while this board sends a report line)
    A0  <- MAX485 RO     A1  -> MAX485 DI            M0/M1 not connected (LOW)
*/

// ---------------- Pins ----------------
const uint8_t HOME_IR_PIN  = A5;
const uint8_t DIR_PIN      = 6;
const uint8_t STEP_PIN     = 5;
const uint8_t SLEEP_PIN    = 4;
const uint8_t RESET_PIN    = 3;
const uint8_t M2_PIN       = 2;
const uint8_t RS485_DE_PIN = 13;

// ---------------- RS-485 report link ----------------
// MAX485 of THIS board: RO -> A0, DI -> A1 (SoftwareSerial; only sending is used here)
//   1 = hardware Serial instead (DI on D1, RO on D0)
#define REPORT_USE_HW_SERIAL 0
const uint8_t REPORT_TX_PIN = A1;      // MAX485 DI
const uint8_t REPORT_RX_PIN = A0;      // MAX485 RO
const long    REPORT_BAUD   = 9600;    // same as the bus

// ---------------- IR sensor ----------------
// Level of the sensor output OVER THE STRIP. Measured on the bench with the sensor
// check: this module goes HIGH on the strip (LOW everywhere else).
const int     IR_ACTIVE  = HIGH;
const uint8_t IR_CONFIRM = 3;          // strip must be seen on 3 consecutive steps

// ---------------- Motion (from the stepper test) ----------------
const long  STEPS_PER_REV = 3200;      // 200 x 16 (1/16 step: M2 HIGH, M0/M1 disconnected)
const float MAX_RPS       = 0.5;       // NEVER faster than this (mechanical limit)
const long  RAMP_STEPS    = 800;       // steps to accelerate / brake
const float FAST_PERIOD_US  = 1000000.0 / (MAX_RPS * STEPS_PER_REV);  // 625 us per step
const float START_PERIOD_US = FAST_PERIOD_US * 4;                      // ramp starts 4x slower
const float SLOW_PERIOD_US  = START_PERIOD_US;                         // final approach: 400 steps/s
const int   HOMING_DIR      = HIGH;    // direction used to approach the strip; flip if needed
const long  BACKOFF_STEPS   = 200;     // distance from the strip before the slow approach
// Give up after this many steps without finding the strip. Must be more than one full
// turn of the BASE (motor turns x gear ratio). 10 motor turns by default.
const long  MAX_SEEK_STEPS  = STEPS_PER_REV * 10;

const int TEST_TURNS = 3;
const unsigned long SENSOR_CHECK_MS = 10000;   // hand-turn sensor check before homing

// ---------------- Report port ----------------
#if REPORT_USE_HW_SERIAL
  #define REPORT Serial
#else
  #include <SoftwareSerial.h>
  SoftwareSerial REPORT(REPORT_RX_PIN, REPORT_TX_PIN);
#endif

long position = 0;                     // steps from home (+ = HOMING_DIR)
long turns[TEST_TURNS];
int  turnsDone = 0;
const __FlashStringHelper *failReason = NULL;

// ---------------------------------------------------------------------------
// Reporting (one text line per call, DE high only while sending)

void report(const char *text) {
  digitalWrite(RS485_DE_PIN, HIGH);
  delay(1);
  REPORT.print(F("[base] "));
  REPORT.print(text);
  REPORT.print('\n');
  REPORT.flush();                      // wait until the last byte has left
  digitalWrite(RS485_DE_PIN, LOW);
}

void reportF(const __FlashStringHelper *text) {
  char buf[96];
  strncpy_P(buf, (const char *)text, sizeof buf - 1);
  buf[sizeof buf - 1] = '\0';
  report(buf);
}

// ---------------------------------------------------------------------------
// Motion

bool stripRaw() {
  return digitalRead(HOME_IR_PIN) == IR_ACTIVE;
}

void stepWithPeriod(int dir, float periodUs) {
  digitalWrite(DIR_PIN, dir);
  digitalWrite(STEP_PIN, HIGH);
  delayMicroseconds((unsigned int)(periodUs / 2));
  digitalWrite(STEP_PIN, LOW);
  delayMicroseconds((unsigned int)(periodUs / 2));
  position += (dir == HOMING_DIR) ? 1 : -1;
}

// Period for a point of the ramp (0 = start, RAMP_STEPS = full speed), like the stepper test
float rampPeriod(long rampPos) {
  float f = (float)rampPos / RAMP_STEPS;
  return START_PERIOD_US - (START_PERIOD_US - FAST_PERIOD_US) * f;
}

// Fast move with ramp until the strip is seen (untilSeen) or clear (!untilSeen),
// then brake over as many steps as it accelerated. Returns steps moved, -1 on timeout.
long fastMoveUntil(int dir, bool untilSeen, long limit) {
  long ramp = 0;
  uint8_t confirm = 0;
  bool hit = false;
  for (long n = 1; n <= limit; n++) {
    stepWithPeriod(dir, rampPeriod(ramp));
    if (!hit) {
      if (stripRaw() == untilSeen) {
        if (++confirm >= IR_CONFIRM) hit = true;
      } else {
        confirm = 0;
      }
      if (!hit && ramp < RAMP_STEPS) ramp++;
    }
    if (hit) {
      if (ramp == 0) return n;
      ramp--;                          // braking
    }
  }
  return -1;
}

// Slow constant-speed move until the strip is seen / clear. Returns steps, -1 on timeout.
long slowMoveUntil(int dir, bool untilSeen, long limit) {
  uint8_t confirm = 0;
  for (long n = 1; n <= limit; n++) {
    stepWithPeriod(dir, SLOW_PERIOD_US);
    if (stripRaw() == untilSeen) {
      if (++confirm >= IR_CONFIRM) return n;
    } else {
      confirm = 0;
    }
  }
  return -1;
}

void slowMoveSteps(int dir, long steps) {
  for (long n = 0; n < steps; n++) stepWithPeriod(dir, SLOW_PERIOD_US);
}

bool fail(const __FlashStringHelper *reason) {
  digitalWrite(SLEEP_PIN, LOW);        // driver off: motor goes limp
  failReason = reason;
  return false;
}

// Fast seek + brake, come back, slow approach. Ends on the strip edge.
bool approachStrip() {
  int back = !HOMING_DIR;
  if (fastMoveUntil(HOMING_DIR, true, MAX_SEEK_STEPS) < 0) return fail(F("strip not found (fast seek)"));
  // braking may have carried us past the strip: come back onto it slowly
  if (slowMoveUntil(back, true, RAMP_STEPS * 2 + STEPS_PER_REV / 4) < 0) return fail(F("lost the strip after braking"));
  if (slowMoveUntil(back, false, STEPS_PER_REV) < 0) return fail(F("strip wider than a motor turn? IR stuck on"));
  slowMoveSteps(back, BACKOFF_STEPS);
  if (slowMoveUntil(HOMING_DIR, true, BACKOFF_STEPS * 3) < 0) return fail(F("strip not found (slow approach)"));
  return true;
}

bool home() {
  if (stripRaw()) {
    reportF(F("already on the strip: backing off first"));
    if (fastMoveUntil(!HOMING_DIR, false, STEPS_PER_REV * 2) < 0) return fail(F("IR always sees strip: IR_ACTIVE inverted or no contrast"));
  }
  if (!approachStrip()) return false;
  position = 0;
  return true;
}

// ---------------------------------------------------------------------------

void reportSummary() {
  char line[96];
  if (failReason != NULL) {
    char reason[60];
    strncpy_P(reason, (const char *)failReason, sizeof reason - 1);
    reason[sizeof reason - 1] = '\0';
    snprintf(line, sizeof line, "HOMING FAILED after %d turn(s): %s", turnsDone, reason);
    report(line);
    reportF(F("check IR_ACTIVE, sensor distance, HOMING_DIR, MAX_SEEK_STEPS. Press reset to retry."));
    return;
  }
  long lo = turns[0], hi = turns[0];
  for (int i = 0; i < TEST_TURNS; i++) {
    snprintf(line, sizeof line, "Turn %d: %ld steps", i + 1, turns[i]);
    report(line);
    if (turns[i] < lo) lo = turns[i];
    if (turns[i] > hi) hi = turns[i];
  }
  long spreadCentiDeg = (hi - lo) * 36000L / lo;
  snprintf(line, sizeof line, "Spread: %ld steps (%ld.%02ld deg)", hi - lo, spreadCentiDeg / 100, spreadCentiDeg % 100);
  report(line);
  snprintf(line, sizeof line, "Steps per tray (turn / 6): %ld", (lo + 3) / 6);
  report(line);
  reportF((hi - lo) <= 32 ? F("RESULT: homing is repeatable. Holding at home.")
                          : F("RESULT: NOT repeatable - check the strip / sensor height."));
}

// Motor off: turn the base by hand and watch the IR changes in the terminal
void sensorCheck() {
  reportF(F("SENSOR CHECK: motor is off, turn the base by hand for 10 s"));
  bool last = stripRaw();
  reportF(last ? F("IR: STRIP") : F("IR: no strip"));
  int changes = 0;
  unsigned long t0 = millis();
  while (millis() - t0 < SENSOR_CHECK_MS) {
    bool now = stripRaw();
    if (now != last) {
      delay(5);                            // debounce
      if (stripRaw() == now) {
        last = now;
        changes++;
        reportF(now ? F("IR: STRIP") : F("IR: no strip"));
      }
    }
  }
  char line[80];
  snprintf(line, sizeof line, "sensor check done: %d change(s). Homing starts now.", changes);
  report(line);
}

void setup() {
  pinMode(RS485_DE_PIN, OUTPUT);
  digitalWrite(RS485_DE_PIN, LOW);     // listen until we have something to say
  pinMode(HOME_IR_PIN, INPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(SLEEP_PIN, OUTPUT);
  pinMode(RESET_PIN, OUTPUT);
  pinMode(M2_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(M2_PIN, HIGH);          // 1/16 step (M0 and M1 disconnected)
  digitalWrite(RESET_PIN, HIGH);       // out of reset
  digitalWrite(SLEEP_PIN, LOW);        // asleep until the test starts

  REPORT.begin(REPORT_BAUD);
  sensorCheck();

  digitalWrite(SLEEP_PIN, HIGH);       // wake the driver
  delay(5);
  reportF(F("homing..."));
  if (home()) {
    reportF(F("HOME found. Starting repeatability turns."));
    for (int i = 0; i < TEST_TURNS; i++) {
      delay(3000);                     // look: the base must stop on the strip each time
      if (fastMoveUntil(HOMING_DIR, false, STEPS_PER_REV * 2) < 0) { fail(F("could not leave the strip")); break; }
      if (!approachStrip()) break;
      turns[i] = position;
      position = 0;
      turnsDone = i + 1;
      char line[40];
      snprintf(line, sizeof line, "Turn %d: %ld steps", i + 1, turns[i]);
      report(line);
    }
  }
  reportSummary();
}

void loop() {
  delay(5000);
  reportSummary();                     // repeated so you can open the terminal any time
}
