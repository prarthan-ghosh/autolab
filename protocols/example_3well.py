"""Dispense into three wells in a row."""

NAME = "3-well row"
DESCRIPTION = "Home, then visit three XY points and dispense at each."

WELLS = [(60, 60), (90, 60), (120, 60)]
SAFE_Z    = 30.0
DISPENSE_Z = 5.0
STROKE     = 1000  # steps — adjust to your pipette


async def run(p):
    await p.home()
    await p.pipette_home()
    await p.set_stroke(STROKE)

    for i, (x, y) in enumerate(WELLS, 1):
        await p.log(f"well {i}/{len(WELLS)} at ({x}, {y})")
        await p.move(x, y, z=SAFE_Z)
        await p.aspirate()
        await p.move_z(DISPENSE_Z)
        await p.dispense()
        await p.move_z(SAFE_Z)
