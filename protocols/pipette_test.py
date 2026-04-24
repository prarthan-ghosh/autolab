"""Pipette-only self-test. Starts and ends at mechanical bottom (ready to aspirate)."""

NAME = "Pipette test"
DESCRIPTION = "Assumes plunger at bottom. Runs 1 aspirate→dispense cycle."

# Precondition: before running, plunger must be HOMEd at mechanical bottom
# and limit set.

STROKE_STEPS = 1300  # full aspirate stroke
SPEED        = 700   # steps/sec
ACCEL        = 100   # steps/sec^2
BACKLASH     = 250   # extra steps after dispense to absorb slack
CYCLES       = 1
DWELL        = 0.5   # seconds between moves


async def run(p):
    # TODO: lift nozzle automatically once Z calibration is reliable.
    # For now, park the nozzle at a safe Z manually before running this script.

    await p.log(f"set speed={SPEED}, accel={ACCEL}, backlash={BACKLASH}")
    await p.pipette.set_speed(SPEED)
    await p.pipette.set_acceleration(ACCEL)
    await p.pipette.set_backlash(BACKLASH)
    await p.set_stroke(STROKE_STEPS)

    for i in range(1, CYCLES + 1):
        await p.log(f"cycle {i}/{CYCLES}: aspirate")
        await p.aspirate()
        await p.sleep(DWELL)

        await p.log(f"cycle {i}/{CYCLES}: dispense")
        await p.dispense()
        await p.sleep(DWELL)

    await p.log("pipette test complete — plunger back at bottom")
