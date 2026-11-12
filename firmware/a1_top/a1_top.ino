/*
  a1_top.ino - MTG card sorter, TOP module (RS-485 address A1)

  Same feeder/flap logic as feeder_flap_test.ino, but the Raspberry Pi drives
  the cycle: this sketch only executes commands and reports its sensors
  (docs/PROTOCOL.md, section 5).

    PING                -> OK FW=1.1
    STATUS              -> OK STATE=IDLE|BUSY|ERROR STACK=0/1 PLAT=0/1 FLAP=<deg> ROLLER=0/1
                              ATTEMPT=<0-2> [ERR=..]
    ROLLER STOP         -> stop the feed roller (also cancels a FEED)
    FLAP <0-180>        -> move the flap servo (80 = closed, 0 = open)
    FEED <timeout_ms>   -> feed ONE card onto the platform (RF05): up to 2 pull attempts of
                           timeout_ms / 2 each, in small steps (run 150 ms, pause 150 ms,
                           check the IR). No card after the first attempt: the roller stops
                           for 0.5 s, then the second attempt. No card after the second:
                           Feed Jam = ERR=FEED_TIMEOUT, roller stopped at once.
                           ATTEMPT in STATUS = the attempt running (or the last one).
    STOP                -> stop the roller, cancel FEED, clear the error (flap keeps its angle)

  Power-up: flap closed (80), roller stopped, nothing moves until the Pi asks.
  Built-in LED: off = idle, on = feeding, fast blink = error (until the next FEED or STOP).
  If DE/RE is on D13 (= the built-in LED pin), the LED is NOT used for status: it then
  just lights up briefly whenever this module transmits. Use the Serial Monitor instead.

  Default wiring (change the constants below to match yours)
    D9  feed roller servo (360)     D10 flap servo (180)
    D6  platform IR                 D5  stack IR
    D4  <- MAX485 RO                D3  -> MAX485 DI          D13 -> MAX485 DE + RE
*/

// No Servo library: the servos are driven by Timer1 hardware PWM (see Timer1Servo below),
// so SoftwareSerial blocking interrupts cannot stretch the pulses.

// ===================== RS-485 link (must match your wiring and the Pi) =====================
// 1 = SoftwareSerial on RS485_RX_PIN / RS485_TX_PIN (D4 / D3). Uploading over USB works
//     with everything connected. Safe for the servos because they use hardware PWM.
// 0 = hardware Serial: D0 <- RO, D1 -> DI. No debug prints; unplug RO from D0 to upload.
#define RS485_USE_SOFTSERIAL 1

const uint8_t       RS485_RX_PIN        = 4;     // SoftwareSerial only: MAX485 RO
const uint8_t       RS485_TX_PIN        = 3;     // SoftwareSerial only: MAX485 DI
const uint8_t       RS485_DE_PIN        = 13;    // MAX485 DE + RE tied together
                                                 // (NO_DE_PIN if the module switches by itself)
const long          RS485_BAUD          = 9600;  // = MTG_BAUDRATE on the Pi
const char          MY_ADDRESS[]        = "A1";
const unsigned long REPLY_DELAY_US      = 3000;  // give the Pi time to release the bus
const unsigned long DUPLICATE_WINDOW_MS = 2000;  // PROTOCOL.md 3.4 (covers the Pi's 3 resends)

// ===================== Pins =====================
// The servos MUST stay on D9 and D10: those are the Timer1 hardware PWM pins (OC1A / OC1B).
const uint8_t FEEDER_PIN   = 9;
const uint8_t FLAP_PIN     = 10;
const uint8_t PLAT_IR_PIN  = 6;
const uint8_t STACK_IR_PIN = 5;      // stack IR

// ===================== Flap =====================
const int FLAP_CLOSED = 80;          // power-up position; the Pi sends FLAP 0 / FLAP 80

// ===================== Feeder (same values as feeder_flap_test.ino) =====================
const int           FEEDER_STOP_US = 1500;  // trim (e.g. 1490 / 1510) if it creeps when stopped
const int           FEEDER_RUN_US  = 1300;  // use 1700 if it spins the wrong way
const unsigned long FEED_STEP_MS   = 150;   // how long one feeder step runs
const unsigned long FEED_PAUSE_MS  = 150;   // pause after each step before checking the IR
// RF05: pull attempts per feed cycle before Feed Jam, and the roller stop between them
const uint8_t       FEED_ATTEMPTS       = 2;
const unsigned long FEED_RETRY_PAUSE_MS = 500;
// true = cut the servo signal whenever the roller is stopped. Most continuous servos
// then stop dead (no creep). Keep false if yours keeps turning without a signal.
const bool FEEDER_DETACH_WHEN_STOPPED = false;

// ===================== IR sensors =====================
const int           PLAT_IR_ACTIVE  = LOW;  // level when the sensor sees a card
const int           STACK_IR_ACTIVE = LOW;  // (most IR obstacle modules pull LOW)
const unsigned long IR_SAMPLE_MS    = 5;    // 3 equal samples 5 ms apart -> new stable value
const uint8_t       IR_SAMPLES      = 3;    // (same filter as cardOnPlate() in the old test)

// ===================== Serial port selection =====================
#if RS485_USE_SOFTSERIAL
  #include <SoftwareSerial.h>
  SoftwareSerial rs485(RS485_RX_PIN, RS485_TX_PIN);
  #define DEBUG_LOG(msg) Serial.println(msg)
#else
  #define rs485 Serial
  #define DEBUG_LOG(msg)
#endif

// ===================== State =====================
const uint8_t MAX_FRAME = 96;
const uint8_t NO_DE_PIN = 255;
const bool    STATUS_LED = (RS485_DE_PIN != LED_BUILTIN);   // D13 is shared with the LED

enum ModuleState { STATE_IDLE, STATE_BUSY, STATE_ERROR };
enum FeedPhase   { FEED_OFF, FEED_RUN, FEED_PAUSE, FEED_RETRY };

struct IrInput {
  uint8_t       pin;
  int           activeLevel;
  bool          stable;       // debounced value: true = card detected
  uint8_t       changes;      // consecutive samples that disagree with `stable`
  unsigned long lastSample;
};

// Servo pulses generated by Timer1 in hardware (Uno/Nano, ATmega328P): 20 ms period,
// 0.5 us resolution. Same angle -> pulse mapping as the Servo library (544-2400 us).
class Timer1Servo {
 public:
  explicit Timer1Servo(uint8_t pin) : pin_(pin) {}

  static void begin() {
#if defined(__AVR_ATmega328P__)
    TCCR1A = (1 << WGM11);                                // fast PWM, TOP = ICR1 (mode 14)
    TCCR1B = (1 << WGM13) | (1 << WGM12) | (1 << CS11);   // prescaler 8 -> 0.5 us per tick
    ICR1   = 39999;                                       // 40000 ticks = 20 ms
#endif
  }
  void attach() {
    apply();
#if defined(__AVR_ATmega328P__)
    pinMode(pin_, OUTPUT);
    TCCR1A |= (pin_ == 9) ? (1 << COM1A1) : (1 << COM1B1);   // connect the pin to the timer
#endif
    attached_ = true;
  }
  void detach() {
#if defined(__AVR_ATmega328P__)
    TCCR1A &= (pin_ == 9) ? ~(1 << COM1A1) : ~(1 << COM1B1);
    digitalWrite(pin_, LOW);                              // no pulses at all
#endif
    attached_ = false;
  }
  bool attached() const { return attached_; }
  void writeMicroseconds(int value) {
    us = value < 500 ? 500 : (value > 2500 ? 2500 : value);
    apply();
  }
  void write(int degrees) {
    angle = degrees < 0 ? 0 : (degrees > 180 ? 180 : degrees);
    writeMicroseconds(544 + (long)(2400 - 544) * angle / 180);
  }

  int us    = 1500;
  int angle = 90;

 private:
  void apply() {
#if defined(__AVR_ATmega328P__)
    if (pin_ == 9) OCR1A = (uint16_t)us * 2;              // double-buffered: no glitch
    else           OCR1B = (uint16_t)us * 2;
#endif
  }
  uint8_t pin_;
  bool    attached_ = false;
};

Timer1Servo feeder(FEEDER_PIN);
Timer1Servo flap(FLAP_PIN);
IrInput platIr  = {PLAT_IR_PIN,  PLAT_IR_ACTIVE,  false, 0, 0};
IrInput stackIr = {STACK_IR_PIN, STACK_IR_ACTIVE, false, 0, 0};

ModuleState   state         = STATE_IDLE;
const char   *errorCode     = NULL;
FeedPhase     feedPhase     = FEED_OFF;
unsigned long attemptStartedAt = 0;
unsigned long phaseStartedAt = 0;
unsigned long attemptMs     = 0;           // length of one pull attempt
uint8_t       feedAttempt   = 0;           // 1 or 2 while feeding; 0 = never fed
bool          rollerRunning = false;
int           flapAngle     = FLAP_CLOSED;

char    lineBuf[MAX_FRAME];
uint8_t lineLen     = 0;
bool    lineTooLong = false;

// Last executed action, to answer a retry without running it twice
bool          haveLast = false;
int           lastSeq  = -1;
char          lastRequest[MAX_FRAME];
char          lastReply[MAX_FRAME];
unsigned long lastAt   = 0;

// ===================== Sensors =====================

bool irRaw(const IrInput &in) {
  return digitalRead(in.pin) == in.activeLevel;
}

void updateIr(IrInput &in, unsigned long now) {
  if (now - in.lastSample < IR_SAMPLE_MS) return;
  in.lastSample = now;
  if (irRaw(in) == in.stable) {
    in.changes = 0;
  } else if (++in.changes >= IR_SAMPLES) {
    in.stable = !in.stable;
    in.changes = 0;
  }
}

// ===================== Feeder =====================

void feederRun() {
  feeder.writeMicroseconds(FEEDER_RUN_US);
  if (!feeder.attached()) feeder.attach();
  rollerRunning = true;
}

void feederStop() {
  feeder.writeMicroseconds(FEEDER_STOP_US);
  if (FEEDER_DETACH_WHEN_STOPPED && feeder.attached()) feeder.detach();
  rollerRunning = false;
}

void endFeed() {
  feederStop();
  feedPhase = FEED_OFF;
}

void startFeed(unsigned long timeoutMs, unsigned long now) {
  attemptMs = timeoutMs / FEED_ATTEMPTS;
  if (attemptMs < FEED_STEP_MS + FEED_PAUSE_MS) attemptMs = FEED_STEP_MS + FEED_PAUSE_MS;
  feedAttempt      = 1;
  attemptStartedAt = now;
  phaseStartedAt   = now;
  feedPhase        = FEED_RUN;
  state            = STATE_BUSY;
  errorCode        = NULL;
  feederRun();
  DEBUG_LOG(F("FEED started: pull attempt 1"));
}

// Non-blocking version of feedStep() + the IR check of the old loop()
void updateFeed(unsigned long now) {
  if (feedPhase == FEED_OFF) return;

  if (feedPhase == FEED_RETRY) {               // roller stopped between the two attempts
    if (now - phaseStartedAt >= FEED_RETRY_PAUSE_MS) {
      feedAttempt++;
      attemptStartedAt = now;
      phaseStartedAt   = now;
      feedPhase        = FEED_RUN;
      feederRun();
      DEBUG_LOG(F("FEED: pull attempt 2"));
    }
    return;
  }

  if (now - attemptStartedAt >= attemptMs) {   // this pull attempt is over: check the IR (RF09)
    feederStop();
    if (platIr.stable) {
      endFeed();
      state = STATE_IDLE;
      DEBUG_LOG(F("FEED done: card on the platform"));
    } else if (feedAttempt < FEED_ATTEMPTS) {
      feedPhase      = FEED_RETRY;
      phaseStartedAt = now;
      DEBUG_LOG(F("FEED: no card after pull attempt 1"));
    } else {
      endFeed();                               // RF05: Feed Jam, roller already stopped
      state     = STATE_ERROR;
      errorCode = "FEED_TIMEOUT";
      DEBUG_LOG(F("FEED_TIMEOUT: no card after 2 pull attempts (Feed Jam)"));
    }
    return;
  }

  if (feedPhase == FEED_RUN) {
    // raw IR (not debounced) so the roller stops the moment the card arrives
    if (irRaw(platIr) || now - phaseStartedAt >= FEED_STEP_MS) {
      feederStop();
      feedPhase      = FEED_PAUSE;
      phaseStartedAt = now;
    }
  } else if (now - phaseStartedAt >= FEED_PAUSE_MS) {      // FEED_PAUSE: card has settled
    if (platIr.stable) {
      endFeed();
      state = STATE_IDLE;
      DEBUG_LOG(F("FEED done: card on the platform"));
    } else {
      feederRun();
      feedPhase      = FEED_RUN;
      phaseStartedAt = now;
    }
  }
}

// Called while we wait or transmit: a card arriving mid-reply still stops the roller at once
void serviceCritical() {
  if (feedPhase == FEED_RUN && rollerRunning && irRaw(platIr)) feederStop();
}

// ===================== LED =====================

void updateLed(unsigned long now) {
  if (!STATUS_LED) return;                // the pin drives the MAX485 instead
  bool on = (state == STATE_BUSY) || (state == STATE_ERROR && (now / 100) % 2 == 0);
  digitalWrite(LED_BUILTIN, on ? HIGH : LOW);
}

// ===================== Protocol =====================

uint8_t xorChecksum(const char *text) {
  uint8_t value = 0;
  while (*text) value ^= (uint8_t)*text++;
  return value;
}

void setDriver(uint8_t level) {
  if (RS485_DE_PIN != NO_DE_PIN) digitalWrite(RS485_DE_PIN, level);
}

void waitTxDone() {
#if !RS485_USE_SOFTSERIAL && defined(SERIAL_TX_BUFFER_SIZE)
  while (rs485.availableForWrite() < SERIAL_TX_BUFFER_SIZE - 1) serviceCritical();
#endif
  rs485.flush();                          // returns once the last stop bit has been sent
}

// body = "A1 17 OK ..." -> sends "<A1 17 OK ...*CS\n"
void sendFrame(const char *body) {
  unsigned long t0 = micros();
  while (micros() - t0 < REPLY_DELAY_US) serviceCritical();

  char tail[6];
  snprintf(tail, sizeof tail, "*%02X\n", xorChecksum(body));

  setDriver(HIGH);                        // drive the bus
  rs485.write('<');
  for (const char *p = body; *p; p++) {
    rs485.write(*p);
    serviceCritical();
  }
  for (const char *p = tail; *p; p++) rs485.write(*p);
  waitTxDone();
  setDriver(LOW);                         // back to listening
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

// Runs one command and writes the reply tokens ("OK ..." / "ERR CODE") into out
void execute(const char *cmd, const char *arg, char *out, size_t size) {
  unsigned long now = millis();
  long value;

  if (strcmp(cmd, "PING") == 0) {
    snprintf(out, size, "OK FW=1.1");

  } else if (strcmp(cmd, "STATUS") == 0) {
    int n = snprintf(out, size, "OK STATE=%s STACK=%d PLAT=%d FLAP=%d ROLLER=%d ATTEMPT=%d",
                     stateName(), stackIr.stable ? 1 : 0, platIr.stable ? 1 : 0,
                     flapAngle, rollerRunning ? 1 : 0, feedAttempt);
    if (state == STATE_ERROR && errorCode != NULL && n > 0 && (size_t)n < size) {
      snprintf(out + n, size - n, " ERR=%s", errorCode);
    }

  } else if (strcmp(cmd, "ROLLER") == 0) {
    if (arg == NULL || strcmp(arg, "STOP") != 0) {
      snprintf(out, size, "ERR BAD_ARG");
      return;
    }
    if (feedPhase != FEED_OFF) {
      endFeed();
      state = STATE_IDLE;
    }
    feederStop();
    snprintf(out, size, "OK");

  } else if (strcmp(cmd, "FLAP") == 0) {
    if (!parseArg(arg, 0, 180, value)) {
      snprintf(out, size, "ERR BAD_ARG");
      return;
    }
    flapAngle = (int)value;
    flap.write(flapAngle);
    snprintf(out, size, "OK");

  } else if (strcmp(cmd, "FEED") == 0) {
    if (!parseArg(arg, 100, 60000, value)) {
      snprintf(out, size, "ERR BAD_ARG");
      return;
    }
    if (state == STATE_BUSY) {
      snprintf(out, size, "ERR BUSY");
      return;
    }
    if (platIr.stable) {                  // a card is already on the platform: done
      state     = STATE_IDLE;
      errorCode = NULL;
    } else {
      startFeed((unsigned long)value, now);
    }
    snprintf(out, size, "OK");

  } else if (strcmp(cmd, "STOP") == 0) {
    endFeed();
    state     = STATE_IDLE;
    errorCode = NULL;
    DEBUG_LOG(F("STOP"));
    snprintf(out, size, "OK");

  } else {
    snprintf(out, size, "ERR BAD_CMD");
  }
}

// line = ">A1 17 FEED 4000*50" (without '\n')
void processLine(char *line) {
  char *start = strrchr(line, '>');
  if (start == NULL) return;                    // a reply from the other module, or noise
  char *body = start + 1;

  char *star = strrchr(body, '*');
  if (star == NULL || !isxdigit(star[1]) || !isxdigit(star[2])) return;
  if (star[3] != '\0' && star[3] != '\r') return;
  uint8_t received = (uint8_t)strtoul(star + 1, NULL, 16);
  *star = '\0';
  if (xorChecksum(body) != received) return;   // corrupted: stay silent, the Pi retries

  char request[MAX_FRAME];
  strncpy(request, body, sizeof request - 1);
  request[sizeof request - 1] = '\0';

  char *addr    = strtok(body, " ");
  char *seqText = strtok(NULL, " ");
  char *cmd     = strtok(NULL, " ");
  char *arg     = strtok(NULL, " ");
  if (addr == NULL || seqText == NULL || cmd == NULL) return;
  if (strcmp(addr, MY_ADDRESS) != 0) return;   // for the other module
  int seq = atoi(seqText);

  bool readOnly = strcmp(cmd, "PING") == 0 || strcmp(cmd, "STATUS") == 0;
  if (!readOnly && haveLast && seq == lastSeq && strcmp(request, lastRequest) == 0
      && millis() - lastAt < DUPLICATE_WINDOW_MS) {
    sendFrame(lastReply);                      // retry of a request we already ran
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
  while (rs485.available() > 0) {
    char c = (char)rs485.read();
    if (c == '>' || c == '<') {                // start of a frame: drop any noise before it
      lineLen     = 0;
      lineTooLong = false;
    }
    if (c == '\n') {
      if (!lineTooLong && lineLen > 0) {
        lineBuf[lineLen] = '\0';
        processLine(lineBuf);
      }
      lineLen     = 0;
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
#if RS485_USE_SOFTSERIAL
  Serial.begin(115200);                   // debug prints over USB
#endif
  pinMode(PLAT_IR_PIN, INPUT);
  pinMode(STACK_IR_PIN, INPUT);
  if (STATUS_LED) pinMode(LED_BUILTIN, OUTPUT);
  if (RS485_DE_PIN != NO_DE_PIN) pinMode(RS485_DE_PIN, OUTPUT);
  setDriver(LOW);                         // listen

  // Targets BEFORE attach(), so the first pulses are already "closed" / "stopped"
  Timer1Servo::begin();
  flap.write(FLAP_CLOSED);
  feeder.writeMicroseconds(FEEDER_STOP_US);
  flap.attach();
  if (!FEEDER_DETACH_WHEN_STOPPED) feeder.attach();

  rs485.begin(RS485_BAUD);
#if !RS485_USE_SOFTSERIAL
  digitalWrite(0, HIGH);                  // Uno/Nano: pull-up on RX so it stays quiet while we transmit
#endif

  platIr.stable  = irRaw(platIr);
  stackIr.stable = irRaw(stackIr);
  DEBUG_LOG(F("A1 top module ready"));
}

void loop() {
  unsigned long now = millis();
  updateIr(platIr, now);
  updateIr(stackIr, now);
  updateFeed(now);
  pollBus();
  updateLed(now);
}
