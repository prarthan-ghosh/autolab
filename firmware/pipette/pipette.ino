// Pipette controller — line-protocol firmware for pi-autolab.
//
// Protocol: every command ends with exactly one terminator line:
//   OK                 — success
//   ERR <message>      — failure
// Optional info lines before the terminator are prefixed with "# ".
//
// Coordinate system: 0 = lower (dispensed / home). Upper limit is NEGATIVE steps.
//
// Commands (case-insensitive, one per line, \n-terminated):
//   HOME                 Set current position as 0.
//   LIMIT <steps>        Set upper limit to -<steps>. <steps> must be > 0.
//   ASPIRATE             Move to upper limit, block until done.
//   DISPENSE             Move to 0, block until done.
//   MOVE <coord>         Absolute move to <coord> steps (negative = up).
//   JOG <delta>          Relative move by <delta> steps (negative = up).
//   POS                  Report current position.
//   SPEED <v>            Set max speed (steps/sec).
//   ACCEL <v>            Set acceleration (steps/sec^2).
//   STOP                 Stop immediately (no deceleration).
//   HELP                 List commands.

#include <AccelStepper.h>

const int DIR_PIN  = 3;
const int STEP_PIN = 2;

const float DEFAULT_MAX_SPEED    = 800.0;
const float DEFAULT_ACCELERATION = 400.0;
const long  DEFAULT_UPPER_LIMIT  = -1000;

AccelStepper stepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

long upperLimit = DEFAULT_UPPER_LIMIT;  // negative
bool limitSet   = false;

static void ok()                       { Serial.println("OK"); }
static void err(const String& msg)     { Serial.print("ERR "); Serial.println(msg); }
static void info(const String& msg)    { Serial.print("# ");  Serial.println(msg); }

// Blocking move to absolute coord. Returns true when target reached.
static bool runTo(long target) {
  stepper.moveTo(target);
  while (stepper.distanceToGo() != 0) {
    stepper.run();
    // Allow STOP during motion.
    if (Serial.available() > 0) {
      int peek = Serial.peek();
      if (peek == 'S' || peek == 's') {
        String line = Serial.readStringUntil('\n');
        line.trim();
        if (line.equalsIgnoreCase("STOP")) {
          stepper.setCurrentPosition(stepper.currentPosition());
          info("motion aborted by STOP");
          return false;
        }
        // Not STOP — ignore, consumed anyway. (Safer than re-buffering.)
        info("ignored during motion: " + line);
      }
    }
  }
  return true;
}

static void printHelp() {
  info("HOME | LIMIT <steps> | ASPIRATE | DISPENSE | MOVE <c> | JOG <d>");
  info("POS | SPEED <v> | ACCEL <v> | STOP | HELP");
}

static void handleCommand(String line) {
  line.trim();
  if (line.length() == 0) { ok(); return; }

  // Split verb / arg.
  int sp = line.indexOf(' ');
  String verb = (sp < 0) ? line : line.substring(0, sp);
  String arg  = (sp < 0) ? ""   : line.substring(sp + 1);
  verb.toUpperCase();
  arg.trim();

  if (verb == "HOME") {
    stepper.setCurrentPosition(0);
    ok();
  } else if (verb == "LIMIT") {
    long v = arg.toInt();
    if (v <= 0) { err("LIMIT requires positive step count"); return; }
    upperLimit = -v;
    limitSet = true;
    info("upper limit = " + String(upperLimit));
    ok();
  } else if (verb == "ASPIRATE") {
    if (!limitSet) { err("upper limit not set (use LIMIT)"); return; }
    if (runTo(upperLimit)) ok(); else err("aborted");
  } else if (verb == "DISPENSE") {
    if (runTo(0)) ok(); else err("aborted");
  } else if (verb == "MOVE") {
    long target = arg.toInt();
    if (limitSet && target < upperLimit) { err("target above upper limit"); return; }
    if (target > 0)                      { err("target below 0 (lower limit)"); return; }
    if (runTo(target)) ok(); else err("aborted");
  } else if (verb == "JOG") {
    long delta = arg.toInt();
    long target = stepper.currentPosition() + delta;
    if (limitSet && target < upperLimit) { err("jog would exceed upper limit"); return; }
    if (target > 0)                      { err("jog would exceed lower limit (0)"); return; }
    if (runTo(target)) ok(); else err("aborted");
  } else if (verb == "POS") {
    info("POS " + String(stepper.currentPosition()));
    ok();
  } else if (verb == "SPEED") {
    float v = arg.toFloat();
    if (v <= 0) { err("SPEED must be positive"); return; }
    stepper.setMaxSpeed(v);
    ok();
  } else if (verb == "ACCEL") {
    float v = arg.toFloat();
    if (v <= 0) { err("ACCEL must be positive"); return; }
    stepper.setAcceleration(v);
    ok();
  } else if (verb == "STOP") {
    // Idle STOP: no-op (nothing moving). Report OK.
    ok();
  } else if (verb == "HELP") {
    printHelp();
    ok();
  } else {
    err("unknown command: " + verb);
  }
}

void setup() {
  Serial.begin(115200);
  stepper.setMaxSpeed(DEFAULT_MAX_SPEED);
  stepper.setAcceleration(DEFAULT_ACCELERATION);
  stepper.setCurrentPosition(0);
  // Boot banner terminated with OK so driver can sync.
  info("pipette firmware ready");
  ok();
}

void loop() {
  if (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    handleCommand(line);
  }
}
