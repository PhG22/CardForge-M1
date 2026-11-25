/*
  a2_motor_test.ino - MTG card sorter: low-level stepper bench test (BOTTOM module, A2)

  Diagnostic firmware. It speaks the same RS-485 frames as a2_base.ino (address A2,
  9600 8N1, docs/PROTOCOL.md), so you can drive it from the laptop through the
  USB-RS485 adapter with tools/a2_motor_test.py and nothing has to be rewired.

  Unlike a2_base.ino there is no homing, no tray table and no Timer1 interrupt:
  every move runs to completion inside the command, and the reply is sent AFTER the
  move, with how long it took and where it ended. The PC waits for it.
  That makes the step timing trivial to reason about, which is the point: this sketch
  is here to answer "does the driver obey DIR, STEP, SLEEP, RESET and M2 at all?".

  Commands (reply always carries the request's seq, like the real protocol)
    PING                      -> OK FW=MT1.0
    STATUS                    -> OK DIR=.. DIRRD=.. SLP=.. RST=.. M2=.. ZERO=0/1
                                    POS=<steps> PER=<us> RAMP=0/1
    DIR <0|1>                 -> OK DIR=<set> DIRRD=<read back from the pin>
    PER <625-20000>           -> OK PER=<us>        step period at full speed
    RAMP <0|1>                -> OK RAMP=<0|1>      1 = ramp up from 200 steps/s
    SLP <0|1>                 -> OK SLP=..          0 = driver asleep, motor limp
    RST <0|1>                 -> OK RST=..          0 = driver in reset (ignores STEP)
    M2  <0|1>                 -> OK M2=..           microstep select pin (D2)
    ZERO                      -> OK POS=0           reset the step counter
    STEP <+-steps>            -> move, DIR taken from the sign (+ = CW_DIR)
    RAW  <0|1> <steps>        -> move, DIR level FORCED to the given level
    AB   <steps> <cycles>     -> <steps> one way, <steps> back, <cycles> times
    SEEK <dir 0|1> <seen 0|1> <limit>
                              -> step until the homing IR reads <seen>, or give up
                                 after <limit> steps
    STOP                      -> OK (see below)

    Move replies: OK N=<steps> DIR=<lvl> T=<ms> POS=<steps> ZERO=0/1 [HIT=0/1]
                     [OVER=<braking steps>] [ABORT=1]
      N     steps actually done (for SEEK: steps up to the moment the IR triggered)
      POS   step counter, + = CW_DIR, same sign convention as a2_base's STEPS
      ABORT the move was cut short (see STOP)

  STOP / panic brake: any byte arriving on the bus stops a running move at once (the
  PC is the only talker on the bus during a test, so this is safe here - it would not
  be in a2_base, where the Pi polls STATUS). So sending STOP, or ^C in the Python
  tool, stops the motor. A duplicate request (same seq + same text within 1 s) is
  answered from the stored reply and never moves the motor twice.

  LIMITS kept from the real machine: never faster than 0.5 motor turn/s (625 us/step
  at 1/16 step) and at most 26000 steps (~2 base turns) per command.

  Wiring (identical to a2_base.ino / a2_homing_test.ino)
    A5  homing IR (HIGH = strip)
    D6  DRV8825 DIR      D5  STEP      D4  SLEEP     D3  RESET     D2  M2 (HIGH = 1/16)
    A0  <- MAX485 RO     A1  -> MAX485 DI            D13 -> MAX485 DE + RE
    Tray IRs (D7-D12) are left alone: this sketch does not read them.

  NOTE ON THE DRIVER MODULE: the pin order of an A4988 board and a DRV8825 board is
  the same, but the microstep table is not. On a DRV8825, M2 HIGH with M0/M1 low =
  1/16 step. On an A4988 the same pin is MS3, and MS3 HIGH with MS1/MS2 low = FULL
  step - 16x more movement per step than the rest of the code assumes. The Python
  tool's "turn" command measures steps per base turn, which tells you which one you
  have (12800 = 1/16, ~800 = full step).
*/

#include <SoftwareSerial.h>

// Declared here, before anything else: the Arduino builder inserts the generated
// function prototypes right after the #includes, and doMove() returns a MoveResult.
const int8_t U_NONE = -1, U_CLEAR = 0, U_SEEN = 1;

struct MoveResult {
  long          steps;     // steps done (for an "until" move: up to the IR trigger)
  long          over;      // extra steps spent braking after the IR triggered
  bool          hit;
  bool          aborted;
  unsigned long ms;
};


// ===================== RS-485 (same as a2_base) =====================
const uint8_t       RS485_RX_PIN        = A0;    // MAX485 RO
const uint8_t       RS485_TX_PIN        = A1;    // MAX485 DI
const uint8_t       RS485_DE_PIN        = 13;    // MAX485 DE + RE
const long          RS485_BAUD          = 9600;
const char          MY_ADDRESS[]        = "A2";
const unsigned long REPLY_DELAY_US      = 3000;  // let the PC release the line first
const unsigned long DUPLICATE_WINDOW_MS = 1000;

SoftwareSerial bus(RS485_RX_PIN, RS485_TX_PIN);

// ===================== Driver / sensor pins =====================
const uint8_t DIR_PIN     = 6;
const uint8_t STEP_PIN    = 5;
const uint8_t SLEEP_PIN   = 4;
const uint8_t RESET_PIN   = 3;
const uint8_t M2_PIN      = 2;
const uint8_t HOME_IR_PIN = A5;
const int     IR_ACTIVE   = HIGH;   // this module reads HIGH over the reflective strip
const uint8_t IR_CONFIRM  = 3;      // equal readings needed before the IR counts

// ===================== Motion limits =====================
const int   CW_DIR            = HIGH;    // DIR level that turns the base clockwise
const long  STEPS_PER_BASE_TURN = 12800; // expected at 1/16 step (76/19 gearing)
const long  STEP_LIMIT        = 26000;   // max steps per command (~2 base turns)
const int   CYCLE_LIMIT       = 50;      // max cycles for AB
const float FAST_PERIOD_US    = 625.0;   // 1600 steps/s = 0.5 motor turn/s (mechanical max)
const float START_PERIOD_US   = 5000.0;  // ramp start: 200 steps/s
const long  RAMP_STEPS        = 1600;    // ramp length, as validated on the bench
const long  PERIOD_MIN_US     = 625;
const long  PERIOD_MAX_US     = 20000;

// ===================== Live settings =====================
long periodUs = (long)FAST_PERIOD_US;    // step period at full speed
bool useRamp  = true;
int  dirCommanded = CW_DIR;              // last level written to DIR_PIN
long position = 0;                       // steps from power-up, + = CW_DIR

// ===================== Motion =====================
bool stripRaw() {
  return digitalRead(HOME_IR_PIN) == IR_ACTIVE;
}

void stepOnce(int8_t sign, float periodHalfUs) {
  digitalWrite(STEP_PIN, HIGH);
  delayMicroseconds((unsigned int)periodHalfUs);
  digitalWrite(STEP_PIN, LOW);
  delayMicroseconds((unsigned int)periodHalfUs);
  position += sign;
}

// Period for a point of the ramp: START_PERIOD_US at r = 0, periodUs at r = RAMP_STEPS
float rampPeriod(long r) {
  if (r >= RAMP_STEPS) return (float)periodUs;
  float f = (float)r / (float)RAMP_STEPS;
  return START_PERIOD_US - (START_PERIOD_US - (float)periodUs) * f;
}

// One move. untilMode U_NONE = exactly `limit` steps; U_SEEN / U_CLEAR = step until
// the homing IR reads that, giving up after `limit` steps.
MoveResult doMove(int dirLevel, long limit, int8_t untilMode) {
  MoveResult res = {0, 0, false, false, 0};
  if (limit <= 0) return res;

  const int8_t sign  = (dirLevel == CW_DIR) ? 1 : -1;
  const bool   ramped = useRamp && (float)periodUs < START_PERIOD_US;
  const long   rMax   = !ramped ? 0
                      : untilMode == U_NONE ? min(RAMP_STEPS, limit / 2) : RAMP_STEPS;

  dirCommanded = dirLevel;
  digitalWrite(DIR_PIN, dirLevel);
  delayMicroseconds(10);                 // DIR setup time (DRV8825 needs 650 ns)

  long    ramp    = 0;
  uint8_t confirm = 0;
  bool    braking = false;
  unsigned long t0 = millis();

  for (long n = 1; n <= limit; n++) {
    stepOnce(sign, (ramped ? rampPeriod(ramp) : (float)periodUs) / 2.0);

    if (untilMode == U_NONE) {
      res.steps = n;
      long remaining = limit - n;
      if (remaining <= ramp)   ramp--;   // braking
      else if (ramp < rMax)    ramp++;   // accelerating
    } else if (!braking) {
      res.steps = n;
      if (stripRaw() == (untilMode == U_SEEN)) {
        if (++confirm >= IR_CONFIRM) {
          res.hit = true;
          if (ramp == 0) break;          // slow enough to stop on the spot
          braking = true;                // fast: brake over as many steps as we accelerated
        }
      } else {
        confirm = 0;
      }
      if (!braking && ramp < rMax) ramp++;
    } else {
      res.over++;
      if (ramp == 0) break;
      ramp--;
    }

    if (bus.available() > 0) { res.aborted = true; break; }   // panic brake, see header
  }

  res.ms = millis() - t0;
  return res;
}

// ===================== Protocol =====================
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

// "OK N=.. DIR=.. T=.. POS=.. ZERO=.." (+ HIT / OVER for SEEK, + ABORT if cut short)
void moveReply(const MoveResult &res, int dirLevel, bool withHit, char *out, size_t size) {
  int n = snprintf(out, size, "OK N=%ld DIR=%d T=%lu POS=%ld ZERO=%d",
                   res.steps, dirLevel, res.ms, position, stripRaw() ? 1 : 0);
  if (withHit && n > 0 && (size_t)n < size) {
    n += snprintf(out + n, size - n, " HIT=%d OVER=%ld", res.hit ? 1 : 0, res.over);
  }
  if (res.aborted && n > 0 && (size_t)n < size) snprintf(out + n, size - n, " ABORT=1");
}

// Driver control pin, read back from the pin itself (an output shorted to GND or 5 V
// reads the forced level, which is exactly what we want to know).
void pinReply(const char *key, uint8_t pin, char *out, size_t size) {
  snprintf(out, size, "OK %s=%d", key, digitalRead(pin));
}

bool setPin(const char *key, uint8_t pin, const char *arg, char *out, size_t size) {
  long value;
  if (!parseArg(arg, 0, 1, value)) { snprintf(out, size, "ERR BAD_ARG"); return false; }
  digitalWrite(pin, value ? HIGH : LOW);
  pinReply(key, pin, out, size);
  return true;
}

void execute(const char *cmd, const char *a1, const char *a2, const char *a3,
             char *out, size_t size) {
  long value, value2, value3;

  if (strcmp(cmd, "PING") == 0) {
    snprintf(out, size, "OK FW=MT1.0");

  } else if (strcmp(cmd, "STATUS") == 0) {
    snprintf(out, size,
             "OK DIR=%d DIRRD=%d SLP=%d RST=%d M2=%d ZERO=%d POS=%ld PER=%ld RAMP=%d",
             dirCommanded, digitalRead(DIR_PIN), digitalRead(SLEEP_PIN),
             digitalRead(RESET_PIN), digitalRead(M2_PIN), stripRaw() ? 1 : 0,
             position, periodUs, useRamp ? 1 : 0);

  } else if (strcmp(cmd, "DIR") == 0) {
    if (!parseArg(a1, 0, 1, value)) { snprintf(out, size, "ERR BAD_ARG"); return; }
    dirCommanded = value ? HIGH : LOW;
    digitalWrite(DIR_PIN, dirCommanded);
    delayMicroseconds(10);
    snprintf(out, size, "OK DIR=%ld DIRRD=%d", value, digitalRead(DIR_PIN));

  } else if (strcmp(cmd, "PER") == 0) {
    if (!parseArg(a1, PERIOD_MIN_US, PERIOD_MAX_US, value)) { snprintf(out, size, "ERR BAD_ARG"); return; }
    periodUs = value;
    snprintf(out, size, "OK PER=%ld", periodUs);

  } else if (strcmp(cmd, "RAMP") == 0) {
    if (!parseArg(a1, 0, 1, value)) { snprintf(out, size, "ERR BAD_ARG"); return; }
    useRamp = value != 0;
    snprintf(out, size, "OK RAMP=%d", useRamp ? 1 : 0);

  } else if (strcmp(cmd, "SLP") == 0) {
    setPin("SLP", SLEEP_PIN, a1, out, size);

  } else if (strcmp(cmd, "RST") == 0) {
    setPin("RST", RESET_PIN, a1, out, size);

  } else if (strcmp(cmd, "M2") == 0) {
    setPin("M2", M2_PIN, a1, out, size);

  } else if (strcmp(cmd, "ZERO") == 0) {
    position = 0;
    snprintf(out, size, "OK POS=0");

  } else if (strcmp(cmd, "STOP") == 0) {
    snprintf(out, size, "OK");                 // the move, if any, already stopped

  } else if (strcmp(cmd, "STEP") == 0) {
    if (!parseArg(a1, -STEP_LIMIT, STEP_LIMIT, value)) { snprintf(out, size, "ERR BAD_ARG"); return; }
    int dirLevel = value >= 0 ? CW_DIR : !CW_DIR;
    MoveResult res = doMove(dirLevel, value >= 0 ? value : -value, U_NONE);
    moveReply(res, dirLevel, false, out, size);

  } else if (strcmp(cmd, "RAW") == 0) {
    if (!parseArg(a1, 0, 1, value) || !parseArg(a2, 0, STEP_LIMIT, value2)) {
      snprintf(out, size, "ERR BAD_ARG");
      return;
    }
    int dirLevel = value ? HIGH : LOW;
    MoveResult res = doMove(dirLevel, value2, U_NONE);
    moveReply(res, dirLevel, false, out, size);

  } else if (strcmp(cmd, "AB") == 0) {
    if (!parseArg(a1, 1, STEP_LIMIT, value) || !parseArg(a2, 1, CYCLE_LIMIT, value2)) {
      snprintf(out, size, "ERR BAD_ARG");
      return;
    }
    unsigned long t0 = millis();
    long done = 0;
    bool aborted = false;
    for (long c = 0; c < value2; c++) {
      MoveResult fwd = doMove(CW_DIR, value, U_NONE);
      if (fwd.aborted) { aborted = true; break; }
      MoveResult back = doMove(!CW_DIR, value, U_NONE);
      if (back.aborted) { aborted = true; break; }
      done = c + 1;
    }
    int n = snprintf(out, size, "OK CYCLES=%ld POS=%ld T=%lu ZERO=%d",
                     done, position, millis() - t0, stripRaw() ? 1 : 0);
    if (aborted && n > 0 && (size_t)n < size) snprintf(out + n, size - n, " ABORT=1");

  } else if (strcmp(cmd, "SEEK") == 0) {
    if (!parseArg(a1, 0, 1, value) || !parseArg(a2, 0, 1, value2)
        || !parseArg(a3, 1, STEP_LIMIT, value3)) {
      snprintf(out, size, "ERR BAD_ARG");
      return;
    }
    int dirLevel = value ? HIGH : LOW;
    MoveResult res = doMove(dirLevel, value3, value2 ? U_SEEN : U_CLEAR);
    moveReply(res, dirLevel, true, out, size);

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
  char *a1      = strtok(NULL, " ");
  char *a2      = strtok(NULL, " ");
  char *a3      = strtok(NULL, " ");
  if (addr == NULL || seqText == NULL || cmd == NULL) return;
  if (strcmp(addr, MY_ADDRESS) != 0) return;
  int seq = atoi(seqText);

  // A retry of a move must not move the motor a second time (the retry is also what
  // stopped the move, see the panic brake): answer from the stored reply.
  bool readOnly = strcmp(cmd, "PING") == 0 || strcmp(cmd, "STATUS") == 0;
  if (!readOnly && haveLast && seq == lastSeq && strcmp(request, lastRequest) == 0
      && millis() - lastAt < DUPLICATE_WINDOW_MS) {
    sendFrame(lastReply);
    return;
  }
  char reply[MAX_FRAME];
  int n = snprintf(reply, sizeof reply, "%s %d ", MY_ADDRESS, seq);
  execute(cmd, a1, a2, a3, reply + n, sizeof reply - n);
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
  digitalWrite(RS485_DE_PIN, LOW);         // listen
  pinMode(HOME_IR_PIN, INPUT);

  pinMode(DIR_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(SLEEP_PIN, OUTPUT);
  pinMode(RESET_PIN, OUTPUT);
  pinMode(M2_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, CW_DIR);
  digitalWrite(M2_PIN, HIGH);              // DRV8825: 1/16 step (M0, M1 disconnected)
  digitalWrite(RESET_PIN, HIGH);           // out of reset
  digitalWrite(SLEEP_PIN, HIGH);           // driver on: holds the base in place
  delay(5);

  bus.begin(RS485_BAUD);
}

void loop() {
  pollBus();
}
