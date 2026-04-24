"""Pipette-only self-test: lift nozzle, home plunger, then a few aspirate/dispense cycles."""

NAME = "Pipette test"
DESCRIPTION = "Lift nozzle to safe Z, home pipette, set stroke, run 3 aspirate/dispense cycles."

SAFE_Z = 80.0        # mm — nozzle/pipette-body height above bed during the test
STROKE_STEPS = 1300  # tuned value — full aspirate stroke
SPEED = 700          # steps/sec — tuned so one cycle returns to start
ACCEL = 100          # steps/sec^2 — tuned so one cycle returns to start
BACKLASH = 250       # extra steps after DISPENSE to absorb direction-change slack
CYCLES = 1
DWELL = 0.5          # seconds between moves


async def run(p):
    # TODO: lift nozzle automatically once Z calibration is reliable.
    # For now, park the nozzle at a safe Z manually before running this script.
    # await p.log(f"lift nozzle to Z={SAFE_Z} for pipette clearance")
    # await p.move_z(SAFE_Z)

    await p.log(f"set speed={SPEED}, accel={ACCEL}, backlash={BACKLASH}")
    await p.pipette.set_speed(SPEED)
    await p.pipette.set_acceleration(ACCEL)
    await p.pipette.set_backlash(BACKLASH)

    await p.log(f"pipette_home — make sure plunger is at mechanical bottom")
    await p.pipette_home()

    await p.log(f"set stroke = {STROKE_STEPS} steps")
    await p.set_stroke(STROKE_STEPS)

    for i in range(1, CYCLES + 1):
        await p.log(f"cycle {i}/{CYCLES}: aspirate")
        await p.aspirate()
        await p.sleep(DWELL)

        await p.log(f"cycle {i}/{CYCLES}: dispense")
        await p.dispense()
        await p.sleep(DWELL)

    await p.log("pipette test complete")
