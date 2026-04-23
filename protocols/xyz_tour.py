"""
A basic tour of the workspace demonstrating simultaneous XYZ movement.
"""

NAME = "Basic XYZ Tour"
DESCRIPTION = "Moves to 4 corners with varying Z heights using hardcoded values."

async def run(p):
    await p.log("Starting basic XYZ tour...")

    # 1. Home everything
    await p.home()

    # 2. Lift to a safe height
    await p.log("Lifting to Z=30")
    await p.move_z(30)

    # 3. Move to the four corners with different Z heights to show XYZ movement
    await p.log("Moving to front-left (0, 0, 10)")
    await p.move(0, 0, z=10)
    await p.sleep(0.5)
    
    await p.log("Moving to front-right (200, 0, 40)")
    await p.move(200, 0, z=40)
    await p.sleep(0.5)
    
    await p.log("Moving to back-right (200, 200, 10)")
    await p.move(200, 200, z=10)
    await p.sleep(0.5)
    
    await p.log("Moving to back-left (0, 200, 40)")
    await p.move(0, 200, z=40)
    await p.sleep(0.5)

    # 4. Move to center and go down
    await p.log("Moving to center (100, 100, 5)")
    await p.move(100, 100, z=5)
    await p.sleep(1)
    
    await p.log("Raising back to Z=30")
    await p.move_z(30)

    # 5. Finished
    await p.home()
    await p.log("Tour complete!")
