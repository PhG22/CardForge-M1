# RS-485 protocol: Raspberry Pi ↔ Arduinos (A1, A2)

This is the contract the Arduino firmware must follow. The Python side is in
`sorter_api/protocol.py`, `bus.py` and `modules.py`. `sorter_api/sim.py` is a
software version of both Arduinos that follows this spec, so you can use it as a
reference for firmware behaviour.

## 1. Bus

| Item | Value |
|---|---|
| Topology | Half-duplex RS-485 (MAX485), one twisted pair. Use 120 Ω termination at both physical ends |
| Roles | **Pi = master**, A1 (top module) and A2 (bottom module) = slaves |
| Serial | **9600 8N1** by default (`MTG_BAUDRATE`). All three nodes must match |
| Direction | DE and RE tied together. HIGH = transmit, LOW = receive. Idle = LOW |

**Golden rule: a slave only transmits to reply to a valid request addressed to it.**
The Pi sends every request and waits for its reply before sending the next one,
so two nodes never drive the bus at the same time.

## 2. Frame format

Each frame is one ASCII line, at most **96 bytes** including the final `\n`:

```
>A1 53 FEED 4000*50\n         request  (Pi → Arduino)
<A1 53 OK*72\n                reply    (Arduino → Pi)
```

| Part | Request | Reply |
|---|---|---|
| Start char | `>` | `<` |
| Address | target: `A1` or `A2` | sender: own address |
| Seq | 0–255, set by the Pi | **echo the request's seq** |
| Tokens | `COMMAND [ARG ...]` | `OK [KEY=VALUE ...]` or `ERR CODE` |
| Checksum | `*` + 2 uppercase hex digits | same |

* Tokens are separated by **one space**. Tokens never contain spaces, `*`, `<`, `>`.
* **Checksum** = XOR of every byte between the start char and `*` (not
  including either). It works the same way as the NMEA checksum.
* Bytes before the start char are ignored. The Pi skips any noise that comes
  before the `<` when the bus turns around.

Checksum and parsing in Arduino C++:

```cpp
uint8_t xorChecksum(const char *s, size_t n) {   // s = text between '>' and '*'
  uint8_t c = 0;
  for (size_t i = 0; i < n; i++) c ^= (uint8_t)s[i];
  return c;
}
// Validate: line = ">A1 53 FEED 4000*50"
//   star = strrchr(line, '*');  body = line+1 .. star-1
//   strtoul(star+1, NULL, 16) == xorChecksum(line+1, star-(line+1))
// Reply:  snprintf(out, sizeof out, "A1 %u OK", seq);
//         Serial.print('<'); Serial.print(out); Serial.print('*');
//         if (cs < 0x10) Serial.print('0'); Serial.print(cs, HEX); Serial.print('\n');
```

## 3. Rules for the firmware

1. **Ignore** (send nothing) when the line has a bad checksum, is longer than
   96 bytes, starts with `<` (another module's reply), or is addressed to the
   other module. The Pi will time out and retry.
2. **Reply within 50 ms**, but **wait ≥ 3 ms after the request's `\n` before
   enabling DE**, so the Pi has time to release the line. Then: DE HIGH → print
   the frame → `Serial.flush()` (waits until the last byte has been sent) → DE LOW.
3. **Never block `loop()`.** Write FEED, HOME and GOTO as non-blocking state
   machines. STATUS must still get an answer while a motor is moving. Do not use
   `delay()` while an action is running. `a2_base` makes its step pulses from a
   Timer1 interrupt, so the motor keeps turning smoothly while SoftwareSerial sends a reply.
4. **Retries and duplicates.** The Pi resends a request with the **same seq**
   if the reply was lost (timeout 300 ms, up to **3 retransmissions**, RNF10; after the third the
   link is declared lost: Data-Link Loss). Keep the last seq,
   the last request text and the last reply. If a request arrives with the same
   seq **and** the same text within **2 s**, send the stored reply again and do
   **not** run the command a second time. PING and STATUS can always just run again.
5. IR sensors are reported as **1 = object/card detected**, whatever the sensor's
   electrical polarity. Handle inversion and debouncing in the firmware.

## 4. Module state

Every module has one state, reported in STATUS:

| STATE | Meaning |
|---|---|
| `IDLE` | Nothing running. After an action this means it **finished successfully** |
| `BUSY` | An action (FEED / HOME / GOTO) is running |
| `ERROR` | The last action failed. STATUS adds `ERR=<CODE>`. Cleared by the next action command or STOP |

Action commands reply `OK` **as soon as the action starts**. Before sending
that `OK`, set the state to `BUSY`. The Pi then polls `STATUS` every 100 ms
until the module reports `IDLE` or `ERROR`. If an action command arrives while
`BUSY`, reply `ERR BUSY`.

## 5. A1 — top module (feeder)

Hardware: 360° servo (feed roller), 180° servo (stopper flap), stack IR, platform IR.

| Command | Reply | What it does |
|---|---|---|
| `PING` | `OK [FW=x.y]` | Liveness check |
| `STATUS` | `OK STATE=.. STACK=0/1 PLAT=0/1 FLAP=<deg> ROLLER=0/1 ATTEMPT=<0-2> [ERR=..]` | Current state and sensors |
| `ROLLER STOP` | `OK` | Roller to neutral (no rotation). Cancels a running FEED |
| `FLAP <0-180>` | `OK` | Move flap servo to angle. Instant command, state unchanged. **80 = closed**, **5 = open (drop)** |
| `FEED <timeout_ms>` | `OK` | *Action* (RF05). Up to **2 pull attempts** of `timeout_ms / 2` each: run the roller until **PLAT=1**, then stop it at once → `IDLE`. No card at the end of the first attempt → roller stopped 0.5 s, then the second attempt. No card at the end of the second → **Feed Jam**: roller stopped at once, `ERROR` + `ERR=FEED_TIMEOUT`. If PLAT is already 1, finish immediately |
| `STOP` | `OK` | Stop roller, cancel any action, state `IDLE`. Flap keeps its angle |

| STATUS field | Meaning |
|---|---|
| `STACK` | Stack IR: 1 = there is at least one card on the stack |
| `PLAT` | Platform IR: 1 = a card is on the reading platform |
| `FLAP` | Last angle commanded to the flap servo |
| `ROLLER` | 1 = feed roller is turning |
| `ATTEMPT` | Pull attempt of the current or last FEED: 1 or 2 (0 = no FEED since power-up). Firmware 1.1+ |

Notes:
* A 360° servo stops at its neutral pulse (≈1500 µs, `writeMicroseconds`), and
  the exact value varies from servo to servo. Calibrate a trim constant so
  "stop" really stops it.
* FEED may move the roller in steps. `firmware/a1_top` runs 150 ms, pauses 150 ms,
  then checks the IR, like `feeder_flap_test.ino`. While the roller turns, it
  watches the raw platform IR and stops the roller the moment the card arrives.
  It does this even while it is sending a reply.
* The flap drop sequence is done by the Pi: `FLAP 5`, wait until `PLAT=0`, wait
  0.5 s, `FLAP 80`.

### 5.1 Optional A1 command: chamber light (not in `a1_top.ino` yet)

The Pi already sends it. While the firmware answers `ERR BAD_CMD`, the Pi turns
the feature off for the rest of the session and the machine works as before.

| Command | Reply | What it does |
|---|---|---|
| `LIGHT <0-100>` | `OK` | Chamber LED strips, percent. Instant command, state unchanged. The Pi sends the base level (60) at power-on and at every Start, then +20 before each identification retry (RNF09), then back to the base level |

The RGB **status LED** is not on the bus: it is wired to the Raspberry Pi GPIO (17/22/27) and driven by
`sorter_api/status_led.py`.

Wiring note: the circuit diagram (Rev E) puts the LED strip gate (`LED_CTRL`) on D3, but
`a1_top.ino` now uses D3/D4 for the RS-485 SoftwareSerial and D5/D6 for the IR sensors.
Pick a free PWM pin (D9/D10 are taken by Timer1 for the servos) before adding `LIGHT`.

## 6. A2 — bottom module (rotating base)

Hardware: stepper (base), homing IR (reflective strip), 6 tray IRs. Firmware: `firmware/a2_base`.

Tray IR sensors (FC-51, LOW = card in the tray): tray 1 D10, tray 2 D8, tray 3 D11, tray 4 D7, tray 5 D9,
tray 6 D12, with the internal pull-up (an unplugged sensor reads "empty"). The firmware debounces them (3 equal
readings 5 ms apart) and reports them in `TRAYS`. The Pi reads them before a run starts (RF07) and never uses
them during a run (RF13).

| Command | Reply | What it does |
|---|---|---|
| `PING` | `OK [FW=x.y]` | Liveness check |
| `STATUS` | `OK STATE=.. HOMED=0/1 POS=<0-6> ZERO=0/1 TRAYS=<6 digits> STEPS=<n> DRIFT=<n> [ERR=..]` | Current state and sensors |
| `HOME <timeout_ms> [steps]` | `OK` | *Action.* Find the reflective strip. Home is **tray 3**, so it ends with `HOMED=1 POS=3 STEPS=0`, `IDLE`. If the strip isn't found → `ERROR` + `ERR=HOME_FAILED`. If it takes longer than `timeout_ms` → `ERR=HOME_TIMEOUT`. RF08 (firmware 1.1): when the position is known (HOMED=1, or `steps` = where the Pi last saw the base), it goes fast to just before the strip and only does the slow final approach; if the strip isn't there it falls back to the full search. Older firmware ignores `steps` |
| `GOTO <1-6>` | `OK T=<ms>` / `ERR NOT_HOMED` | *Action.* Turn until tray N is under the platform → `POS=N`, `IDLE`. Positions are absolute from home, and the base never wraps through the open gap between trays 6 and 1 |
| `MOVE <steps>` | `OK T=<ms>` / `ERR NOT_HOMED` | *Action.* Turn to an absolute position, in steps from home (+ = clockwise, −20000..20000). Used for testing and calibration. `POS` = the tray at that exact position, or 0 |
| `STOP` | `OK` | Stop the stepper now, state `IDLE`. If stopped mid-move, `POS=0` and `STEPS` shows where it stopped. If stopped mid-homing, `HOMED=0` |

| STATUS field | Meaning |
|---|---|
| `HOMED` | 1 = position is known (HOME succeeded since power-up) |
| `POS` | Tray currently under the platform, 1–6. 0 = between trays / unknown |
| `ZERO` | Homing IR: 1 = it sees the strip |
| `STEPS` | Position in steps from home (+ = clockwise). 12800 steps = one base turn |
| `DRIFT` | How many steps the position was off at the last re-HOME. 0 to ±5 = fine. More means the motor lost steps since the previous HOME |
| `TRAYS` | 6 characters, tray 1 first: `1` = the tray has cards, `0` = empty. E.g. `010010` = trays 2 and 5 not empty |

**Keep the bus quiet while the base moves.** The base receives every byte on the bus
(SoftwareSerial), and each byte pauses its step interrupt for about 1 ms. Polling every
100 ms during a move made the motor stagger: 80–170 hiccups per move, and 5–11% slower.
So `GOTO`/`MOVE` return `T=<ms>`, the move's duration. The Pi waits that long (+2%)
without sending anything, then checks `STATUS` every 250 ms. While homing (duration
unknown), it polls once per second.

## 7. Error codes

| Code | Sent by | Meaning |
|---|---|---|
| `BAD_CMD` | both | Unknown command |
| `BAD_ARG` | both | Missing / non-numeric / out-of-range argument |
| `BUSY` | both | Action command received while an action is running |
| `NOT_HOMED` | A2 | GOTO before a successful HOME |
| `FEED_TIMEOUT` | A1 (in STATUS `ERR=`) | Feed Jam: no card reached the platform after 2 pull attempts (jam or empty stack, RF05) |
| `HOME_TIMEOUT` | A2 (in STATUS `ERR=`) | Homing took longer than the timeout |
| `HOME_FAILED` | A2 (in STATUS `ERR=`) | The strip was not found (sensor, or the strip is missing) |

## 8. Example: one card cycle

```
>A1 52 STATUS*63               <A1 52 OK STATE=IDLE STACK=1 PLAT=0 FLAP=80 ROLLER=0*52   card on stack
>A1 53 FEED 4000*50            <A1 53 OK*72                                              feed started
>A1 54 STATUS*65               <A1 54 OK STATE=BUSY STACK=1 PLAT=0 FLAP=80 ROLLER=1*4C   ...feeding
>A1 55 STATUS*64               <A1 55 OK STATE=IDLE STACK=1 PLAT=1 FLAP=80 ROLLER=0*54   card on platform
   (Pi: take picture / identify — placeholder waits 2 s)
>A2 56 GOTO 2*71               <A2 56 OK*74                                              base turning
>A2 57 STATUS*65               <A2 57 OK STATE=IDLE HOMED=1 POS=2 ZERO=0 TRAYS=100000*41 at tray 2
>A1 58 FLAP 5*73               <A1 58 OK*79                                              open flap
>A1 59 STATUS*68               <A1 59 OK STATE=IDLE STACK=1 PLAT=0 FLAP=5 ROLLER=0*64    card dropped
>A1 60 FLAP 80*45              <A1 60 OK*72                                              close flap
```

Errors look like this:

```
>A1 61 STATUS*63               <A1 61 OK STATE=ERROR STACK=1 PLAT=0 FLAP=80 ROLLER=0 ERR=FEED_TIMEOUT*50
>A2 62 GOTO 7*73               <A2 62 ERR BAD_ARG*5E
```

To see the real traffic, run the API with `MTG_LOG_LEVEL=DEBUG`. Every TX/RX frame is logged.
