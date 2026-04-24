"""Pick up water, deposit into each cell, return home.

Precondition: plunger is at mechanical bottom (ready to aspirate). HOME +
LIMIT must have been set up in a prior UI session. Script starts by going
to water and aspirating. Ends at the mechanical bottom (ready to aspirate)
for re-entrance."""

NAME = "Water → cells"
DESCRIPTION = "Assumes plunger is at bottom. Water → aspirate → cell → dispense, per cell. Ends at bottom."

# --- Positions (mm) -----------------------------------------------------
START = (100.0, 100.0)           # XY park position at end of run
WATER = (142.0, 38.0)          # XY of the water reservoir
CELLS = [                      # XY of each cell to fill
    (0.0,  209),
    (0.0,  171.0),
    (0.0,  133.0),
    (38.0,  209.0),
    (38.0,  171.0),
    (38.0,  133.0),
]

# --- Z heights (mm) -----------------------------------------------------
Z_TRAVEL = 200                # safe cruising height between XY moves
Z_WATER  = 150.0                # dip depth in the water reservoir
Z_CELL   = 150.0

# --- Pipette tuning (steps) ---------------------------------------------
STROKE_STEPS = 1300
SPEED        = 700
ACCEL        = 100
BACKLASH     = 250


async def run(p):
    await p.pipette.set_speed(SPEED)
    await p.pipette.set_acceleration(ACCEL)
    await p.pipette.set_backlash(BACKLASH)
    await p.set_stroke(STROKE_STEPS)

    # Lift to travel height FIRST, then move to start XY. Guarantees
    # clearance regardless of where the nozzle is parked.
    await p.move_z(Z_TRAVEL)
    await p.move(*START)

    for i, (cx, cy) in enumerate(CELLS, 1):
        await p.log(f"cell {i}/{len(CELLS)} at ({cx}, {cy})")

        # Fill at water reservoir (plunger at bottom → aspirate up).
        await p.move(*WATER, z=Z_TRAVEL)
        await p.move_z(Z_WATER)
        await p.aspirate()
        await p.move_z(Z_TRAVEL)

        # Deposit into cell (plunger returns to bottom).
        await p.move(cx, cy, z=Z_TRAVEL)
        await p.move_z(Z_CELL)
        await p.dispense()
        await p.move_z(Z_TRAVEL)

    # Park.
    await p.move(*START, z=Z_TRAVEL)
    await p.log("done — plunger back at bottom (ready to aspirate)")
