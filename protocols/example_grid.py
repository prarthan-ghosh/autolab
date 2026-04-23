"""Dispense onto a 3x3 grid."""

NAME = "3x3 grid"
DESCRIPTION = "Sweep a 3x3 XY grid, dispensing one stroke per spot."

ORIGIN = (60.0, 60.0)
SPACING = 20.0
ROWS, COLS = 3, 3

SAFE_Z     = 30.0
DISPENSE_Z = 5.0
STROKE     = 1000


async def run(p):
    await p.home()
    await p.pipette_home()
    await p.set_stroke(STROKE)

    x0, y0 = ORIGIN
    for r in range(ROWS):
        for c in range(COLS):
            x = x0 + c * SPACING
            y = y0 + r * SPACING
            await p.log(f"spot r={r} c={c} -> ({x}, {y})")
            await p.move(x, y, z=SAFE_Z)
            await p.aspirate()
            await p.move_z(DISPENSE_Z)
            await p.dispense()
            await p.move_z(SAFE_Z)
