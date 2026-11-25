/*
  a2_tray_move_test.ino - MTG card sorter: base tray positioning test (bottom module only)

  Standalone test (no API). After power-up / reset:
    1. Homing on the reflective strip (same method as a2_homing_test.ino).
       Home = the strip = tray 3.
    2. Goes to tray 1, 2, 3, 4, 5, 6, stopping STOP_MS at each one.
    3. Goes back to tray 1 and repeats 1 ... 6 once more.
    4. DRIFT CHECK: homes again and reports how many steps were lost during all the
       moves (0-2 is normal; more means the motor skipped steps), then returns to tray 6.
    5. Stays at tray 6 with the motor energized; the summary is repeated every 5 s.

  Every time the base stops at tray 3 it also checks that the IR sees the strip:
  "NOT SEEN" means the motor lost steps on the way there.
  Every time a move CROSSES the strip, it reports where the strip was found compared
  with where it should be ("strip crossed: off by N steps"). The first move with a
  big number is where the motor stalled.
  If "tray move test: starting" appears again in the middle of a run, the Arduino
  RESET (usually the 5 V supply dipping when the motor pulls current).

  Moves never wrap around: the base goes from tray 1 to tray 6 through home (tray 3),
  never "the short way" behind it.

  Results are sent as text over RS-485 (no USB). On the laptop, with the API stopped:
      python -m serial.tools.miniterm /dev/cu.usbserial-110 9600

  TRAY ANGLES: TRAY_DEG holds each tray's angle from home (degrees, + = clockwise),
  taken from the mechanical design (packed ring, 46.495 deg between slots). If a tray
  stops off-centre on the real base, correct its angle. 1 degree = 35.556 steps.

  Wiring (same as a2_homing_test.ino)
    A5  homing IR (HIGH = strip)
    D6  DRV8825 DIR      D5  STEP      D4  SLEEP     D3  RESET     D2  M2 (HIGH = 1/16)
    A0  <- MAX485 RO     A1  -> MAX485 DI            D13 MAX485 DE+RE
*/

#include <SoftwareSerial.h>

// ---------------- Trays ----------------
// Packed ring (mechanical design): neighbouring slots are 46.4953575 deg apart, slot
// centres at 0, 46.495, 92.991, 139.486, 185.981, 232.477 deg; open gap 6 -> 1 = 127.523 deg.
// Home (the strip) is at tray 3. Trays 2 and 1 are clockwise from it, 4, 5 and 6
// counter-clockwise. Angles below are from HOME, + = clockwise.
const float SLOT_DEG = 46.4953575;
const float TRAY_DEG[6] = {
   2 * SLOT_DEG,   // tray 1   +92.991 deg
   1 * SLOT_DEG,   // tray 2   +46.495 deg
   0.0,            // tray 3   home (adjust if the strip edge is not at the tray centre)
  -1 * SLOT_DEG,   // tray 4   -46.495 deg
  -2 * SLOT_DEG,   // tray 5   -92.991 deg
  -3 * SLOT_DEG,   // tray 6  -139.486 deg
};
const long STEPS_PER_BASE_TURN = 12800;   // measured with a2_homing_test.ino (4:1 gearing)
const unsigned long STOP_MS = 3000;       // pause at each tray
const int PASSES = 2;                     // 1..6, then back to 1 and 1..6 again

// Which DIR level turns the base CLOCKWISE (seen from above)? If the first move
// (home -> tray 1) goes counter-clockwise, flip this.
const int CW_DIR = HIGH;
// Direction used to approach the strip during homing (worked in a2_homing_test.ino)
const int HOMING_DIR = HIGH;

// ---------------- Pins ----------------
const uint8_t HOME_IR_PIN  = A5;
const uint8_t DIR_PIN      = 6;
const uint8_t STEP_PIN     = 5;
const uint8_t SLEEP_PIN    = 4;
const uint8_t RESET_PIN    = 3;
const uint8_t M2_PIN       = 2;
const uint8_t RS485_DE_PIN = 13;
const uint8_t RS485_RX_PIN = A0;          // MAX485 RO
const uint8_t RS485_TX_PIN = A1;          // MAX485 DI
const long    RS485_BAUD   = 9600;

// ---------------- IR sensor ----------------
const int     IR_ACTIVE  = HIGH;          // this module goes HIGH over the strip
const uint8_t IR_CONFIRM = 3;

// ---------------- Motion (limits from the stepper test) ----------------
const long  STEPS_PER_REV   = 3200;       // motor: 200 x 16
const float MAX_RPS         = 0.5;        // NEVER faster than this (mechanical limit)
const long  RAMP_STEPS      = 1600;       // gentle: the geared carousel has a lot of inertia
const float FAST_PERIOD_US  = 1000000.0 / (MAX_RPS * STEPS_PER_REV);  // 625 us
const float START_PERIOD_US = FAST_PERIOD_US * 8;                      // ramp starts 8x slower (200 steps/s)
const float SLOW_PERIOD_US  = FAST_PERIOD_US * 4;                      // homing approach: 400 steps/s
const long  BACKOFF_STEPS   = 200;
const long  MAX_SEEK_STEPS  = STEPS_PER_BASE_TURN + STEPS_PER_BASE_TURN / 4;  // 1.25 base turns

SoftwareSerial bus(RS485_RX_PIN, RS485_TX_PIN);

long position = 0;                        // steps from home, + = clockwise
long homePositionBeforeReset = 0;
long stripWidth = 0;                       // measured at homing (steps)
long crossError = 0;                       // error of the last strip crossing
bool crossed = false;
int  bigCrossings = 0;
const __FlashStringHelper *failReason = NULL;
int  stopsDone = 0;
int  strip3Misses = 0;
long driftSteps = 0;
bool driftChecked = false;

// ---------------------------------------------------------------------------
// Reporting over RS-485

void report(const char *text) {
  digitalWrite(RS485_DE_PIN, HIGH);
  delay(1);
  bus.print(F("[base] "));
  bus.print(text);
  bus.print('\n');
  bus.flush();
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
  position += (dir == CW_DIR) ? 1 : -1;
}

float rampPeriod(long rampPos) {
  return START_PERIOD_US - (START_PERIOD_US - FAST_PERIOD_US) * ((float)rampPos / RAMP_STEPS);
}

long traySteps(int tray) {                // tray 1..6 -> steps from home
  float s = TRAY_DEG[tray - 1] * STEPS_PER_BASE_TURN / 360.0;
  return (long)(s < 0 ? s - 0.5 : s + 0.5);
}

// Ramp up, cruise, ramp down (like girar() in the stepper test) to an absolute position
void moveTo(long target) {
  long delta = target - position;
  if (delta == 0) return;
  int  dir   = delta > 0 ? CW_DIR : !CW_DIR;
  long total = delta > 0 ? delta : -delta;
  long ramp  = total / 2 < RAMP_STEPS ? total / 2 : RAMP_STEPS;   // short move: triangle
  bool seen = stripRaw();
  uint8_t confirm = 0;
  crossed = false;
  for (long i = 0; i < total; i++) {
    long r = RAMP_STEPS;
    if (i < ramp) r = i;                           // accelerating
    else if (i >= total - ramp) r = total - 1 - i; // braking
    stepWithPeriod(dir, rampPeriod(r));
    // strip crossing check: entering the strip should happen at a known position
    if (stripRaw() != seen) {
      if (++confirm >= IR_CONFIRM) {
        seen = !seen;
        confirm = 0;
        if (seen && !crossed) {
          long expected = (dir == HOMING_DIR) ? 0 : stripWidth;   // which edge we enter by
          crossError = position - expected;
          crossed = true;
        }
      }
    } else {
      confirm = 0;
    }
  }
}

// --- homing (from a2_homing_test.ino) ---

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
      ramp--;
    }
  }
  return -1;
}

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

bool fail(const __FlashStringHelper *reason) {
  digitalWrite(SLEEP_PIN, LOW);           // driver off: motor goes limp
  failReason = reason;
  return false;
}

bool home() {
  int back = !HOMING_DIR;
  if (stripRaw()) {
    reportF(F("already on the strip: backing off first"));
    if (fastMoveUntil(back, false, STEPS_PER_REV * 2) < 0) return fail(F("IR always sees strip: check IR_ACTIVE"));
  }
  if (fastMoveUntil(HOMING_DIR, true, MAX_SEEK_STEPS) < 0) return fail(F("strip not found (fast seek)"));
  if (slowMoveUntil(back, true, RAMP_STEPS * 2 + STEPS_PER_REV / 4) < 0) return fail(F("lost the strip after braking"));
  if (slowMoveUntil(back, false, STEPS_PER_REV) < 0) return fail(F("strip never cleared"));
  for (long n = 0; n < BACKOFF_STEPS; n++) stepWithPeriod(back, SLOW_PERIOD_US);
  if (slowMoveUntil(HOMING_DIR, true, BACKOFF_STEPS * 3) < 0) return fail(F("strip not found (slow approach)"));
  homePositionBeforeReset = position;   // ~0 if no steps were lost since the last homing
  position = 0;
  // measure the strip width (needed to check crossings in the other direction)
  long w = slowMoveUntil(HOMING_DIR, false, STEPS_PER_REV);
  if (w < 0) return fail(F("strip wider than a motor turn?"));
  stripWidth = position;
  while (position > 0) stepWithPeriod(!HOMING_DIR, SLOW_PERIOD_US);   // back to home
  return true;
}

// ---------------------------------------------------------------------------

void reportTray(int tray, int pass) {
  char line[96];
  long deg10 = (long)(TRAY_DEG[tray - 1] * 10 + (TRAY_DEG[tray - 1] < 0 ? -0.5 : 0.5));
  snprintf(line, sizeof line, "pass %d: at tray %d  (%ld steps from home, %s%ld.%ld deg)",
           pass, tray, position, deg10 < 0 ? "-" : "", labs(deg10) / 10, labs(deg10) % 10);
  report(line);
  if (crossed) {
    snprintf(line, sizeof line, "  strip crossed on the way: off by %ld steps%s", crossError,
             (crossError >= -12 && crossError <= 12) ? "" : "  <-- LOST STEPS before here");
    report(line);
    if (crossError < -12 || crossError > 12) bigCrossings++;
  }
  if (tray == 3) {
    if (stripRaw()) {
      reportF(F("  strip check at tray 3: OK"));
    } else {
      strip3Misses++;
      reportF(F("  strip check at tray 3: NOT SEEN -> motor lost steps"));
    }
  }
}

void reportSummary() {
  if (failReason != NULL) {
    char reason[60], line[96];
    strncpy_P(reason, (const char *)failReason, sizeof reason - 1);
    reason[sizeof reason - 1] = '\0';
    snprintf(line, sizeof line, "FAILED after %d stop(s): %s", stopsDone, reason);
    report(line);
    reportF(F("press reset to retry"));
    return;
  }
  char line[96];
  snprintf(line, sizeof line, "DONE: %d stops, holding at tray 6 (%ld steps)", stopsDone, position);
  report(line);
  if (driftChecked) {
    snprintf(line, sizeof line, "drift after all moves: %ld steps (%s)", driftSteps,
             (driftSteps >= -12 && driftSteps <= 12) ? "OK" : "LOST STEPS");
    report(line);
  }
  snprintf(line, sizeof line, "strip not seen at tray 3: %d time(s), bad crossings: %d, strip width: %ld steps",
           strip3Misses, bigCrossings, stripWidth);
  report(line);
  reportF(F("adjust TRAY_DEG for any tray that was off-centre and run again"));
}

void setup() {
  pinMode(RS485_DE_PIN, OUTPUT);
  digitalWrite(RS485_DE_PIN, LOW);
  pinMode(HOME_IR_PIN, INPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(SLEEP_PIN, OUTPUT);
  pinMode(RESET_PIN, OUTPUT);
  pinMode(M2_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(M2_PIN, HIGH);             // 1/16 step
  digitalWrite(RESET_PIN, HIGH);
  digitalWrite(SLEEP_PIN, LOW);

  bus.begin(RS485_BAUD);
  reportF(F("tray move test: starting in 3 s"));
  reportF(stripRaw() ? F("IR sees the strip now: YES") : F("IR sees the strip now: no"));
  delay(3000);

  digitalWrite(SLEEP_PIN, HIGH);          // wake the driver
  delay(5);
  reportF(F("homing..."));
  if (!home()) {
    reportSummary();
    return;
  }
  reportF(F("HOME found (tray 3 reference)"));
  delay(STOP_MS);

  for (int pass = 1; pass <= PASSES; pass++) {
    for (int tray = 1; tray <= 6; tray++) {
      moveTo(traySteps(tray));
      stopsDone++;
      reportTray(tray, pass);
      delay(STOP_MS);
    }
  }

  // Drift check: home again; where the strip is found tells how many steps were lost
  reportF(F("drift check: homing again..."));
  if (!home()) {
    reportSummary();
    return;
  }
  driftSteps = homePositionBeforeReset;
  driftChecked = true;
  moveTo(traySteps(6));
  reportSummary();
}

void loop() {
  delay(5000);
  reportSummary();
}
