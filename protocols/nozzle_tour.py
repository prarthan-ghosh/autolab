"""Move the nozzle to a few positions and log the reported position at each."""

NAME = "Nozzle tour"
DESCRIPTION = "Home, then visit four XY corners + center. No pipette use."

SAFE_Z = 20.0
POINTS = [
    (20.0,  20.0),
    (200.0, 20.0),
    (200.0, 200.0),
    (20.0,  200.0),
    (110.0, 110.0),
]


async def run(p):
    await p.home()
    pos = await p.pos()
    await p.log(f"after home: x={pos.x:.2f} y={pos.y:.2f} z={pos.z:.2f}")

    for i, (x, y) in enumerate(POINTS, 1):
        await p.move(x, y, z=SAFE_Z)
        pos = await p.pos()
        await p.log(
            f"point {i}: commanded=({x:.1f}, {y:.1f}, {SAFE_Z:.1f}) "
            f"actual=({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})"
        )
        await p.sleep(0.5)

    await p.move(0.0, 0.0, z=SAFE_Z)
    await p.log("done")
