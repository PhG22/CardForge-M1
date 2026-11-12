/*
  a2_base.ino - MTG card sorter, BOTTOM module (RS-485 address A2)

  Implements the A2 part of docs/PROTOCOL.md. The Raspberry Pi / laptop API drives
  the run; this sketch only executes commands and reports its state:

    PING                -> OK FW=1.0
    STATUS              -> OK STATE=IDLE|BUSY|ERROR HOMED=0/1 POS=<0-6> ZERO=0/1 TRAYS=<6 digits>
                              STEPS=<position> [ERR=..]
    HOME <timeout_ms>   -> find the reflective strip (= tray 3) -> HOMED=1 POS=3 STEPS=0
    GOTO <1-6>          -> OK T=<ms>: turn to that tray (needs HOMED)
    MOVE <steps>        -> OK T=<ms>: turn to an absolute position in steps from home
                           (needs HOMED), + = clockwise. For testing / calibration.
                           T = how long the move will take. The API waits that long
                           without talking to this board, because every SoftwareSerial
                           byte pauses the step interrupt for ~1 ms (the motor would stagger).
    STOP                -> stop the motor now, cancel any action, clear the error

  Nothing moves at power-up: the base waits for HOME.

  Motion: max 0.5 motor turn/s (1600 steps/s at 1/16 step), starting at 200 steps/s.
    FAST_PROFILE false: the ramp validated on the bench (period falls linearly over 1600
                        steps). Smooth but slow: a tray-to-tray hop takes ~6.4 s.
    FAST_PROFILE true : constant acceleration (ACCEL steps/s^2). Same start speed and top
                        speed, gentler near top speed, a hop takes ~2 s. Check DRIFT (below)
                        before trusting it.
  Step pulses come from a Timer1 interrupt.

  DRIFT: every HOME after the first one measures how far off the position was (in
  STATUS as DRIFT=<steps>). 0..+-5 = fine; more = the motor lost steps since the last HOME.

  Wiring (as tested with a2_homing_test / a2_tray_move_test)
    A5  homing IR (HIGH = strip)
    D6  DRV8825 DIR      D5  STEP      D4  SLEEP     D3  RESET     D2  M2 (HIGH = 1/16)
    A0  <- MAX485 RO     A1  -> MAX485 DI            D13 -> MAX485 DE + RE
    Tray IR sensors (FC-51, LOW = card in the tray), reported in STATUS as TRAYS=<6 digits>:
    tray 1 D10   tray 2 D8   tray 3 D11   tray 4 D7   tray 5 D9   tray 6 D12
    The Pi reads them only before a run starts (RF07), never during a run (RF13).
*/

#include <SoftwareSerial.h>

// ===================== RS-485 =====================
const uint8_t       RS485_RX_PIN        = A0;    // MAX485 RO
const uint8_t       RS485_TX_PIN        = A1;    // MAX485 DI
const uint8_t       RS485_DE_PIN        = 13;    // MAX485 DE + RE
const long          RS485_BAUD          = 9600;  // = MTG_BAUDRATE on the API side
const char          MY_ADDRESS[]        = "A2";
const unsigned long REPLY_DELAY_US      = 3000;
const unsigned long DUPLICATE_WINDOW_MS = 1000;

// ===================== Driver / sensor pins =====================
// STEP must stay on D5 and the homing IR on A5: the interrupt uses their port bits directly.
const uint8_t DIR_PIN     = 6;
const uint8_t STEP_PIN    = 5;      // PD5
const uint8_t SLEEP_PIN   = 4;
const uint8_t RESET_PIN   = 3;
const uint8_t M2_PIN      = 2;
const uint8_t HOME_IR_PIN = A5;     // PC5
const int     IR_ACTIVE   = LOW;   // this module goes HIGH over the strip
const uint8_t IR_CONFIRM  = 3;

// Tray "has cards" sensors, tray 1..6. NO_PIN = not wired (reported as empty).
// Inputs with pull-up: an unplugged sensor reads HIGH = empty instead of floating.
const uint8_t NO_PIN = 255;
const uint8_t TRAY_IR_PINS[6] = {10, 8, 11, 7, 9, 12};
const int     TRAY_IR_ACTIVE  = LOW;
const unsigned long TRAY_SAMPLE_MS = 5;  // 3 equal samples 5 ms apart -> new stable value

// ===================== Geometry (hardware/wiring-and-calibration.md) =====================
const long  STEPS_PER_BASE_TURN = 12800;      // measured: 4 motor turns (76/19 gearing)
const float SLOT_DEG = 46.4953575;            // packed ring: angle between neighbouring trays
// Angle of each tray from HOME (the strip), + = clockwise. Home is tray 3; trays 2, 1
// clockwise, trays 4, 5, 6 counter-clockwise. Correct one value if a tray is off-centre.
const float TRAY_DEG[6] = { 2 * SLOT_DEG, 1 * SLOT_DEG, 0.0, -1 * SLOT_DEG, -2 * SLOT_DEG, -3 * SLOT_DEG };
const uint8_t HOME_TRAY = 3;
const int  CW_DIR     = HIGH;                 // DIR level that turns the base clockwise
const int  HOMING_DIR = HIGH;                 // approach the strip turning this way
const long MOVE_LIMIT = 20000;                // MOVE accepts -20000 .. 20000

// ===================== Motion (limits from the stepper test) =====================
// Timer1 ticks are 0.5 us.
const uint16_t FAST_T       = 1250;           // 625 us/step  = 1600 steps/s = 0.5 motor turn/s (MAX)
const uint16_t START_T      = FAST_T * 8;     // 5000 us/step = 200 steps/s (ramp start)
const uint16_t SLOW_T       = FAST_T * 4;     // 2500 us/step = 400 steps/s (homing approach)
const bool     FAST_PROFILE = false;          // true = constant acceleration (see header)
const float    ACCEL        = 1500;           // steps/s^2 for FAST_PROFILE (min ~1000)
const uint16_t LINEAR_RAMP  = 1600;           // steps of the bench-validated linear ramp
const uint32_t START_FP     = (uint32_t)START_T << 8;
const uint32_t DELTA_FP     = (((uint32_t)(START_T - FAST_T)) << 8) / LINEAR_RAMP;
// FAST_PROFILE: period table, one entry per 8 steps of the ramp (filled in setup)
const uint8_t  TABLE_SHIFT  = 3;
const uint8_t  TABLE_SIZE   = 160;            // -> ramps up to 1280 steps
uint16_t accelTable[TABLE_SIZE];
uint16_t RAMP = LINEAR_RAMP;                  // ramp length of the active profile

inline uint16_t rampPeriod(uint16_t r) {      // timer ticks per step at ramp position r
  if (FAST_PROFILE) {                        // interpolate between table entries
    uint8_t k = r >> TABLE_SHIFT;
    if (k >= TABLE_SIZE - 1) return accelTable[TABLE_SIZE - 1];
    uint16_t a = accelTable[k], b = accelTable[k + 1];
    return a - (uint16_t)(((uint32_t)(a - b) * (r & ((1 << TABLE_SHIFT) - 1))) >> TABLE_SHIFT);
  }
  return (uint16_t)((START_FP - (uint32_t)r * DELTA_FP) >> 8);
}
const long     BACKOFF_STEPS = 200;
const long     MAX_SEEK_STEPS = STEPS_PER_BASE_TURN + STEPS_PER_BASE_TURN / 4;

// ===================== Motion state (shared with the interrupt) =====================
enum MotionMode   { M_TO, M_UNTIL };
enum MotionResult { R_NONE, R_DONE, R_HIT, R_LIMIT };

struct Motion {
  volatile bool     active;
  volatile uint8_t  mode;
  volatile int8_t   dirSign;
  volatile bool     fast;
  volatile bool     untilSeen;
  volatile uint8_t  confirm;
  volatile bool     braking;
  volatile uint8_t  result;
  volatile long     total;        // M_TO: steps to do
  volatile long     limit;        // M_UNTIL: give up after this many steps
  volatile long     done;
  volatile uint16_t r;            // position in the ramp (0 = slowest)
  volatile uint16_t rMax;         // M_TO: ramp length for this move (short moves: triangle)
};
Motion mv;
volatile long position = 0;       // steps from home, + = clockwise

inline bool stripSeenFast() {     // usable inside the interrupt
  bool high = (PINC & _BV(5)) != 0;
  return IR_ACTIVE == HIGH ? high : !high;
}

void motionFinish(uint8_t result) {
  mv.active = false;
  mv.result = result;
  TIMSK1 &= ~_BV(OCIE1A);
}

ISR(TIMER1_COMPA_vect) {
  if (!mv.active) return;
  PORTD |= _BV(5);                         // STEP pulse (>= 1.9 us for the DRV8825)
  TCNT1 = 0;                               // next period counts from THIS step, even if the
                                           // interrupt was late (SoftwareSerial busy): no bursts
  __builtin_avr_delay_cycles(40);
  PORTD &= ~_BV(5);
  position += mv.dirSign;
  mv.done++;

  if (mv.mode == M_TO) {
    long remaining = mv.total - mv.done;
    if (remaining <= 0) { motionFinish(R_DONE); return; }
    if (remaining <= (long)mv.r) {          // braking
      mv.r--;
    } else if (mv.r < mv.rMax) {            // accelerating
      mv.r++;
    }
  } else {                                  // M_UNTIL
    if (!mv.braking) {
      if (stripSeenFast() == mv.untilSeen) {
        if (++mv.confirm >= IR_CONFIRM) {
          if (!mv.fast || mv.r == 0) { motionFinish(R_HIT); return; }
          mv.braking = true;
          mv.result = R_HIT;
        }
      } else {
        mv.confirm = 0;
      }
      if (!mv.braking && mv.done >= mv.limit) {
        if (!mv.fast || mv.r == 0) { motionFinish(R_LIMIT); return; }
        mv.braking = true;
        mv.result = R_LIMIT;
      }
    }
    if (mv.fast) {
      if (mv.braking) {
        if (mv.r == 0) { motionFinish(mv.result); return; }
        mv.r--;
      } else if (mv.r < RAMP) {
        mv.r++;
      }
    }
  }
  OCR1A = mv.fast || mv.mode == M_TO ? rampPeriod(mv.r) : SLOW_T;
  TIFR1 = _BV(OCF1A);                      // drop a match that happened while we were late
}

void timerSetup() {
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11);          // CTC, prescaler 8 -> 0.5 us per tick
  TIMSK1 = 0;
}

void startMotion(uint8_t mode, int dirLevel, bool fast, bool untilSeen, long count) {
  noInterrupts();
  mv.mode      = mode;
  mv.dirSign   = (dirLevel == CW_DIR) ? 1 : -1;
  mv.fast      = fast;
  mv.untilSeen = untilSeen;
  mv.confirm   = 0;
  mv.braking   = false;
  mv.result    = R_NONE;
  mv.done      = 0;
  mv.r         = 0;
  mv.total     = count;
  mv.limit     = count;
  mv.rMax      = mode == M_TO ? (uint16_t)min((long)RAMP, count / 2) : RAMP;
  digitalWrite(DIR_PIN, dirLevel);
  TCNT1  = 0;
  OCR1A  = (fast || mode == M_TO) ? START_T : SLOW_T;
  TIFR1  = _BV(OCF1A);
  mv.active = true;
  TIMSK1 |= _BV(OCIE1A);
  interrupts();
}

long readPosition() {
  noInterrupts();
  long p = position;
  interrupts();
  return p;
}

// Duration of a move of `steps` with the ramp below, in ms (same profile as the interrupt)
unsigned long moveDurationMs(long steps) {
  if (steps < 0) steps = -steps;
  if (steps == 0) return 0;
  uint16_t rMax = (uint16_t)min((long)RAMP, steps / 2);
  uint32_t rampTicks = 0;                    // the interrupt runs period(r) for r = 0..rMax-1 twice
  for (uint16_t r = 0; r < rMax; r++) rampTicks += rampPeriod(r);
  float totalUs = (2.0 * rampTicks + (float)(steps - 2L * rMax) * rampPeriod(rMax)) / 2.0;
  return (unsigned long)(totalUs / 1000.0) + 1;
}

void startMoveTo(long target) {
  long delta = target - readPosition();
  if (delta == 0) { mv.active = false; mv.result = R_DONE; return; }
  startMotion(M_TO, delta > 0 ? CW_DIR : !CW_DIR, true, false, delta > 0 ? delta : -delta);
}

void startUntil(int dirLevel, bool untilSeen, bool fast, long limit) {
  startMotion(M_UNTIL, dirLevel, fast, untilSeen, limit);
}

void stopMotion() {
  noInterrupts();
  mv.active = false;
  TIMSK1 &= ~_BV(OCIE1A);
  interrupts();
}

// ===================== Module state =====================
enum ModuleState { STATE_IDLE, STATE_BUSY, STATE_ERROR };
enum Action      { ACT_NONE, ACT_HOME, ACT_GOTO, ACT_MOVE };
enum HomeStep    { H_LEAVE, H_SEEK, H_BACK_ON, H_BACK_CLEAR, H_BACKOFF, H_APPROACH };

ModuleState   state      = STATE_IDLE;
const char   *errorCode  = NULL;
Action        action     = ACT_NONE;
HomeStep      homeStep   = H_SEEK;
unsigned long homeStartedAt = 0;
unsigned long homeTimeoutMs = 0;
bool          homed      = false;
bool          wasHomed   = false;          // homed before the current HOME (for DRIFT)
long          drift      = 0;              // position error found by the last HOME
uint8_t       trayPos    = 0;              // tray under the platform, 0 = between / unknown
uint8_t       gotoTray   = 0;
long          traySteps[6];

uint8_t trayAt(long steps) {
  for (uint8_t i = 0; i < 6; i++) if (traySteps[i] == steps) return i + 1;
  return 0;
}

void failAction(const char *code) {
  stopMotion();
  if (action == ACT_HOME) homed = false;
  action    = ACT_NONE;
  state     = STATE_ERROR;
  errorCode = code;
}

void beginHome(unsigned long timeoutMs) {
  wasHomed      = homed;
  homed         = false;
  trayPos       = 0;
  action        = ACT_HOME;
  state         = STATE_BUSY;
  errorCode     = NULL;
  homeStartedAt = millis();
  homeTimeoutMs = timeoutMs;
  if (stripSeenFast()) {                    // on the strip: leave it backwards first
    homeStep = H_LEAVE;
    startUntil(!HOMING_DIR, false, true, 3200L * 2);
  } else {
    homeStep = H_SEEK;
    startUntil(HOMING_DIR, true, true, MAX_SEEK_STEPS);
  }
}

// Same sequence as a2_homing_test.ino, one step per finished motion
void updateHome() {
  if (millis() - homeStartedAt > homeTimeoutMs) { failAction("HOME_TIMEOUT"); return; }
  if (mv.active) return;
  uint8_t res = mv.result;
  int back = !HOMING_DIR;
  switch (homeStep) {
    case H_LEAVE:
      if (res != R_HIT) { failAction("HOME_FAILED"); return; }
      homeStep = H_SEEK;
      startUntil(HOMING_DIR, true, true, MAX_SEEK_STEPS);
      break;
    case H_SEEK:                            // fast seek found the strip (and braked past it)
      if (res != R_HIT) { failAction("HOME_FAILED"); return; }
      homeStep = H_BACK_ON;
      startUntil(back, true, false, RAMP * 2L + 800);
      break;
    case H_BACK_ON:
      if (res != R_HIT) { failAction("HOME_FAILED"); return; }
      homeStep = H_BACK_CLEAR;
      startUntil(back, false, false, 3200);
      break;
    case H_BACK_CLEAR:
      if (res != R_HIT) { failAction("HOME_FAILED"); return; }
      homeStep = H_BACKOFF;
      startMoveTo(readPosition() + (back == CW_DIR ? BACKOFF_STEPS : -BACKOFF_STEPS));
      break;
    case H_BACKOFF:
      homeStep = H_APPROACH;
      startUntil(HOMING_DIR, true, false, BACKOFF_STEPS * 3);
      break;
    case H_APPROACH:
      if (res != R_HIT) { failAction("HOME_FAILED"); return; }
      noInterrupts();
      if (wasHomed) drift = position;       // should be ~0 if no steps were lost
      position = 0;
      interrupts();
      homed   = true;
      trayPos = HOME_TRAY;
      action  = ACT_NONE;
      state   = STATE_IDLE;
      break;
  }
}

void updateAction() {
  if (action == ACT_HOME) { updateHome(); return; }
  if ((action == ACT_GOTO || action == ACT_MOVE) && !mv.active) {
    trayPos = action == ACT_GOTO ? gotoTray : trayAt(readPosition());
    action  = ACT_NONE;
    state   = STATE_IDLE;
  }
}

// ===================== Protocol =====================
SoftwareSerial bus(RS485_RX_PIN, RS485_TX_PIN);
const uint8_t MAX_FRAME = 96;
char    lineBuf[MAX_FRAME];
uint8_t lineLen = 0;
bool    lineTooLong = false;
bool          haveLast = false;
int           lastSeq  = -1;
char          lastRequest[MAX_FRAME];
char          lastReply[MAX_FRAME];
unsigned long lastAt   = 0;

uint8_t xorChecksum(const char *text) {
  uint8_t value = 0;
  while (*text) value ^= (uint8_t)*text++;
  return value;
}

void sendFrame(const char *body) {
  delayMicroseconds(REPLY_DELAY_US);
  char tail[6];
  snprintf(tail, sizeof tail, "*%02X\n", xorChecksum(body));
  digitalWrite(RS485_DE_PIN, HIGH);
  bus.write('<');
  bus.print(body);
  bus.print(tail);
  bus.flush();
  digitalWrite(RS485_DE_PIN, LOW);
}

bool parseArg(const char *text, long lo, long hi, long &value) {
  if (text == NULL || *text == '\0') return false;
  char *end;
  value = strtol(text, &end, 10);
  return *end == '\0' && value >= lo && value <= hi;
}

const char *stateName() {
  switch (state) {
    case STATE_BUSY:  return "BUSY";
    case STATE_ERROR: return "ERROR";
    default:          return "IDLE";
  }
}

// Debounced tray sensors: a reading must repeat IR_CONFIRM times (one every TRAY_SAMPLE_MS)
// before it counts, so a card edge or a flicker of the IR module does not toggle TRAYS.
bool          trayFull[6];
uint8_t       trayChanges[6];
unsigned long lastTraySample = 0;

void sampleTrays() {
  if (millis() - lastTraySample < TRAY_SAMPLE_MS) return;
  lastTraySample = millis();
  for (uint8_t i = 0; i < 6; i++) {
    if (TRAY_IR_PINS[i] == NO_PIN) continue;
    bool raw = digitalRead(TRAY_IR_PINS[i]) == TRAY_IR_ACTIVE;
    if (raw == trayFull[i]) {
      trayChanges[i] = 0;
    } else if (++trayChanges[i] >= IR_CONFIRM) {
      trayFull[i] = raw;
      trayChanges[i] = 0;
    }
  }
}

void traysField(char *out) {
  for (uint8_t i = 0; i < 6; i++) out[i] = trayFull[i] ? '1' : '0';
  out[6] = '\0';
}

void execute(const char *cmd, const char *arg, char *out, size_t size) {
  long value;
  if (strcmp(cmd, "PING") == 0) {
    snprintf(out, size, "OK FW=1.0");

  } else if (strcmp(cmd, "STATUS") == 0) {
    char trays[7];
    traysField(trays);
    int n = snprintf(out, size, "OK STATE=%s HOMED=%d POS=%d ZERO=%d TRAYS=%s STEPS=%ld DRIFT=%ld",
                     stateName(), homed ? 1 : 0, trayPos, stripSeenFast() ? 1 : 0, trays, readPosition(), drift);
    if (state == STATE_ERROR && errorCode != NULL && n > 0 && (size_t)n < size) {
      snprintf(out + n, size - n, " ERR=%s", errorCode);
    }

  } else if (strcmp(cmd, "HOME") == 0) {
    if (!parseArg(arg, 100, 120000, value)) { snprintf(out, size, "ERR BAD_ARG"); return; }
    if (state == STATE_BUSY) { snprintf(out, size, "ERR BUSY"); return; }
    beginHome((unsigned long)value);
    snprintf(out, size, "OK");

  } else if (strcmp(cmd, "GOTO") == 0 || strcmp(cmd, "MOVE") == 0) {
    bool isGoto = cmd[0] == 'G';
    if (isGoto ? !parseArg(arg, 1, 6, value) : !parseArg(arg, -MOVE_LIMIT, MOVE_LIMIT, value)) {
      snprintf(out, size, "ERR BAD_ARG");
      return;
    }
    if (state == STATE_BUSY) { snprintf(out, size, "ERR BUSY"); return; }
    if (!homed) { snprintf(out, size, "ERR NOT_HOMED"); return; }
    errorCode = NULL;
    state     = STATE_BUSY;
    trayPos   = 0;
    long target = isGoto ? traySteps[value - 1] : value;
    unsigned long ms = moveDurationMs(target - readPosition());
    if (isGoto) {
      action   = ACT_GOTO;
      gotoTray = (uint8_t)value;
    } else {
      action = ACT_MOVE;
    }
    startMoveTo(target);
    snprintf(out, size, "OK T=%lu", ms);

  } else if (strcmp(cmd, "STOP") == 0) {
    bool wasMoving = mv.active;
    stopMotion();
    if (action == ACT_HOME) homed = false;
    if (wasMoving) trayPos = 0;
    action    = ACT_NONE;
    state     = STATE_IDLE;
    errorCode = NULL;
    snprintf(out, size, "OK");

  } else {
    snprintf(out, size, "ERR BAD_CMD");
  }
}

void processLine(char *line) {
  char *start = strrchr(line, '>');
  if (start == NULL) return;
  char *body = start + 1;
  char *star = strrchr(body, '*');
  if (star == NULL || !isxdigit(star[1]) || !isxdigit(star[2])) return;
  if (star[3] != '\0' && star[3] != '\r') return;
  uint8_t received = (uint8_t)strtoul(star + 1, NULL, 16);
  *star = '\0';
  if (xorChecksum(body) != received) return;

  char request[MAX_FRAME];
  strncpy(request, body, sizeof request - 1);
  request[sizeof request - 1] = '\0';

  char *addr    = strtok(body, " ");
  char *seqText = strtok(NULL, " ");
  char *cmd     = strtok(NULL, " ");
  char *arg     = strtok(NULL, " ");
  if (addr == NULL || seqText == NULL || cmd == NULL) return;
  if (strcmp(addr, MY_ADDRESS) != 0) return;
  int seq = atoi(seqText);

  bool readOnly = strcmp(cmd, "PING") == 0 || strcmp(cmd, "STATUS") == 0;
  if (!readOnly && haveLast && seq == lastSeq && strcmp(request, lastRequest) == 0
      && millis() - lastAt < DUPLICATE_WINDOW_MS) {
    sendFrame(lastReply);
    return;
  }
  char reply[MAX_FRAME];
  int n = snprintf(reply, sizeof reply, "%s %d ", MY_ADDRESS, seq);
  execute(cmd, arg, reply + n, sizeof reply - n);
  sendFrame(reply);
  if (!readOnly) {
    haveLast = true;
    lastSeq  = seq;
    strcpy(lastRequest, request);
    strcpy(lastReply, reply);
    lastAt   = millis();
  }
}

void pollBus() {
  while (bus.available() > 0) {
    char c = (char)bus.read();
    if (c == '>' || c == '<') { lineLen = 0; lineTooLong = false; }
    if (c == '\n') {
      if (!lineTooLong && lineLen > 0) {
        lineBuf[lineLen] = '\0';
        processLine(lineBuf);
      }
      lineLen = 0;
      lineTooLong = false;
    } else if (lineLen < MAX_FRAME - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineTooLong = true;
    }
  }
}

// ===================== Arduino entry points =====================

void setup() {
  pinMode(RS485_DE_PIN, OUTPUT);
  digitalWrite(RS485_DE_PIN, LOW);
  pinMode(HOME_IR_PIN, INPUT);
  for (uint8_t i = 0; i < 6; i++) {
    if (TRAY_IR_PINS[i] == NO_PIN) continue;
    pinMode(TRAY_IR_PINS[i], INPUT_PULLUP);
    trayFull[i] = digitalRead(TRAY_IR_PINS[i]) == TRAY_IR_ACTIVE;   // start from the real state
  }
  pinMode(DIR_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(SLEEP_PIN, OUTPUT);
  pinMode(RESET_PIN, OUTPUT);
  pinMode(M2_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(M2_PIN, HIGH);               // 1/16 step (M0, M1 disconnected)
  digitalWrite(RESET_PIN, HIGH);
  digitalWrite(SLEEP_PIN, HIGH);            // driver on: holds the base in place
  delay(5);

  for (uint8_t i = 0; i < 6; i++) {
    float s = TRAY_DEG[i] * STEPS_PER_BASE_TURN / 360.0;
    traySteps[i] = (long)(s < 0 ? s - 0.5 : s + 0.5);
  }
  if (FAST_PROFILE) {                       // constant acceleration from 200 to 1600 steps/s
    float v0 = 2000000.0 / START_T, vMax = 2000000.0 / FAST_T;
    long rampSteps = (long)((vMax * vMax - v0 * v0) / (2 * ACCEL));
    if (rampSteps > ((long)TABLE_SIZE << TABLE_SHIFT) - 1) rampSteps = ((long)TABLE_SIZE << TABLE_SHIFT) - 1;
    RAMP = (uint16_t)rampSteps;
    for (uint16_t k = 0; k < TABLE_SIZE; k++) {
      float v = sqrt(v0 * v0 + 2 * ACCEL * ((float)k * (1 << TABLE_SHIFT)));
      if (v > vMax) v = vMax;
      accelTable[k] = (uint16_t)(2000000.0 / v);
    }
  }
  timerSetup();
  bus.begin(RS485_BAUD);
}

void loop() {
  pollBus();
  updateAction();
  sampleTrays();
}
