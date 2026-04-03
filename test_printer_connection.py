#!/usr/bin/env python3
"""
Standalone script to test USB-C connection to Anycubic Kobra 2 Neo printer.

=============================================================================
DEVELOPMENT WORKFLOW & SETUP (Mac to Raspberry Pi)
=============================================================================

1. Mount the Remote Pi Directory:
   Mount the Raspberry Pi's autolab directory locally via Tailscale/SSH:
   sshfs -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 -o volname="Pi-Autolab" pi@100.72.164.72:/home/pi/autolab ~/pi-autolab

2. Sync Files (Optional):
   To copy the mounted files to your local Documents folder for backup/editing:
   rsync -av \\
       --exclude='.git' \\
       --exclude='__pycache__' \\
       --exclude='.DS_Store' \\
       --exclude='*.pyc' \\
       --exclude='myenv/' \\
       /Users/prarthanghosh/pi-autolab/ \\
       /Users/prarthanghosh/Documents/Neurotech/

3. Environment & Packages:
   Activate your conda environment and install the required serial package:
   conda activate auto_lab_env
   pip install pyserial

4. Discovering the Printer (Mac Terminal):
   If you need to manually find the printer's serial port, run:
   ls /dev/tty.* # or
   ls /dev/cu.*
   # Look for devices named like /dev/tty.usbserial-XXXX or /dev/tty.usbmodemXXXX

=============================================================================
WHAT TO EXPECT WHEN RUNNING
=============================================================================
- Initialization: The printer will likely reset (reboot) when the script opens the port. The script waits 3 seconds for this.
- Terminal Output: You will see firmware details (Marlin), temperature readings, current XYZ coordinates, and endstop states.
- Physical Action: At the end of the test (Test 6), the printer's part cooling fan will audibly spin up to 50% speed for 1.5 seconds and then turn completely off.

=============================================================================
SCRIPT DETAILS
=============================================================================
This script tests basic communication with the printer by:
1. Connecting to the serial port
2. Waiting for printer initialization
3. Sending test commands (M115, M105, M114, M119, M106/M107)
4. Verifying responses

Usage:
    python test_printer_connection.py [PORT]
    
    If PORT is not specified, it will try common ports:
    - Linux/Pi: /dev/ttyUSB0, /dev/ttyACM0
    - Windows: COM3, COM4
    - Mac: /dev/tty.usbserial-*, /dev/tty.usbmodem*
"""

import serial
import time
import sys
import os
from typing import Optional, List


# Configuration
BAUD_RATE = 115200  # Anycubic Kobra 2 Neo standard speed
INIT_WAIT_TIME = 3  # Seconds to wait after opening port (printer reboots)


def find_serial_ports() -> List[str]:
    """Find available serial ports on the system."""
    ports = []
    
    # Linux/Pi common ports
    linux_ports = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']
    
    # Windows common ports
    windows_ports = [f'COM{i}' for i in range(1, 21)]
    
    # Mac common ports (check common patterns)
    mac_patterns = ['/dev/tty.usbserial-', '/dev/tty.usbmodem', '/dev/cu.usbserial-', '/dev/cu.usbmodem']
    
    # Try to detect OS and suggest ports
    if sys.platform.startswith('linux'):
        ports.extend(linux_ports)
    elif sys.platform.startswith('win'):
        ports.extend(windows_ports)
    elif sys.platform.startswith('darwin'):  # Mac
        # Try to list actual ports
        try:
            import glob
            for pattern in mac_patterns:
                ports.extend(glob.glob(f'{pattern}*'))
        except:
            pass
        # Fallback to common patterns
        if not ports:
            ports.extend([f'/dev/tty.usbserial-{i}' for i in range(10)])
    
    return ports


def connect_printer(port: str) -> Optional[serial.Serial]:
    """Connect to printer and wait for initialization."""
    try:
        print(f"Connecting to {port} at {BAUD_RATE} baud...")
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        
        # CRITICAL: When you open the port, the printer usually reboots (DTR reset).
        # We must wait a few seconds for it to be ready.
        print(f"Waiting {INIT_WAIT_TIME} seconds for printer to initialize after connection...")
        time.sleep(INIT_WAIT_TIME)
        
        # Clear any startup text (like "Marlin x.x.x" or boot messages)
        ser.reset_input_buffer()
        print("✓ Printer connected and ready.\n")
        return ser
        
    except serial.SerialException as e:
        print(f"✗ Error connecting to {port}: {e}")
        return None
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return None


def send_gcode(ser: serial.Serial, command: str, timeout: float = 5.0) -> tuple[bool, str]:
    """
    Send a G-code command and read the response.
    
    Returns:
        (success: bool, response: str)
    """
    if not ser or not ser.is_open:
        return False, "Serial port not open"
    
    print(f"  Sending: {command}")
    
    # G-code must end with a newline character (\n)
    full_command = f"{command}\n"
    ser.write(full_command.encode('utf-8'))
    
    # Read the response lines
    start_time = time.time()
    responses = []
    
    while (time.time() - start_time) < timeout:
        try:
            line = ser.readline().decode('utf-8').strip()
            if line:
                responses.append(line)
                print(f"    Printer says: {line}")
                
                # Standard Marlin firmware replies with "ok" when done
                if line.lower().startswith('ok'):
                    return True, '\n'.join(responses)
                
                # Check for errors
                if 'error' in line.lower() or 'resend' in line.lower():
                    return False, '\n'.join(responses)
                    
        except Exception as e:
            # If readline times out or fails, continue waiting
            time.sleep(0.1)
            continue
    
    # Timeout
    if responses:
        return False, f"Timeout (no 'ok' received). Responses: {'; '.join(responses)}"
    else:
        return False, "Timeout (no response received)"


def test_connection(ser: serial.Serial) -> bool:
    """Run a series of tests to verify printer communication."""
    print("=" * 60)
    print("Running Connection Tests")
    print("=" * 60)
    
    all_passed = True
    
    # Test 1: Get Firmware Info (M115)
    print("\n[Test 1] Getting firmware information (M115)...")
    success, response = send_gcode(ser, "M115", timeout=3.0)
    if success:
        print("  ✓ Firmware info received")
        if 'Marlin' in response or 'firmware' in response.lower():
            print("  ✓ Detected Marlin firmware")
    else:
        print(f"  ✗ Failed: {response}")
        all_passed = False
    
    # Test 2: Get Temperature (M105)
    print("\n[Test 2] Getting temperature (M105)...")
    success, response = send_gcode(ser, "M105", timeout=3.0)
    if success:
        print("  ✓ Temperature query successful")
        import re
        temp_match = re.search(r'T:([\d.]+)', response)
        bed_match = re.search(r'B:([\d.]+)', response)
        if temp_match and bed_match:
            print(f"  ✓ Nozzle temp: {temp_match.group(1)}°C, Bed temp: {bed_match.group(1)}°C")
    else:
        print(f"  ✗ Failed: {response}")
        all_passed = False
    
    # Test 3: Set Safe Modes (G21, G90)
    print("\n[Test 3] Setting safe modes (G21: millimeters, G90: absolute)...")
    success1, response1 = send_gcode(ser, "G21", timeout=2.0)
    success2, response2 = send_gcode(ser, "G90", timeout=2.0)
    if success1 and success2:
        print("  ✓ Safe modes set successfully")
    else:
        print(f"  ✗ Failed: G21={success1}, G90={success2}")
        all_passed = False
    
    # Test 4: Get Current Position (M114)
    print("\n[Test 4] Getting current position (M114)...")
    success, response = send_gcode(ser, "M114", timeout=3.0)
    if success:
        print("  ✓ Position query successful")
        import re
        pos_match = re.search(r'X:([\d.-]+)\s+Y:([\d.-]+)\s+Z:([\d.-]+)', response)
        if pos_match:
            x, y, z = pos_match.groups()
            print(f"  ✓ Current position: X={x}, Y={y}, Z={z}")
    else:
        print(f"  ✗ Failed: {response}")
        all_passed = False

    # Test 5: Check Endstops and Inductive Probe (M119)
    print("\n[Test 5] Checking endstops and probe (M119)...")
    success, response = send_gcode(ser, "M119", timeout=3.0)
    if success:
        print("  ✓ Endstop query successful")
        if 'x_min' in response.lower() or 'z_min' in response.lower():
            print("  ✓ Endstop states received")
    else:
        print(f"  ✗ Failed: {response}")
        all_passed = False

    # Test 6: Test Part Cooling Fan (M106 / M107)
    print("\n[Test 6] Testing part cooling fan...")
    print("  Turning fan ON to 50% (M106 S128)")
    success1, response1 = send_gcode(ser, "M106 S128", timeout=2.0)
    if success1:
        time.sleep(1.5) # Wait 1.5 seconds to allow the fan to spin up audibly
        print("  Turning fan OFF (M107)")
        success2, response2 = send_gcode(ser, "M107", timeout=2.0)
        if success2:
            print("  ✓ Fan test successful")
        else:
            print(f"  ✗ Failed to turn fan off: {response2}")
            all_passed = False
    else:
        print(f"  ✗ Failed to turn fan on: {response1}")
        all_passed = False
        
    return all_passed


def main():
    """Main function."""
    print("=" * 60)
    print("Anycubic Kobra 2 Neo USB-C Connection Test")
    print("=" * 60)
    print()
    
    # Get port from command line or try to find it
    if len(sys.argv) > 1:
        port = sys.argv[1]
        ports_to_try = [port]
    else:
        print("No port specified. Trying to find available ports...")
        ports_to_try = find_serial_ports()
        
        if not ports_to_try:
            print("✗ Could not find any serial ports to try.")
            print("\nPlease specify the port manually:")
            print("  python test_printer_connection.py <PORT>")
            print("\nCommon ports:")
            print("  Linux/Pi: /dev/ttyUSB0, /dev/ttyACM0")
            print("  Windows: COM3, COM4")
            print("  Mac: /dev/tty.usbserial-*")
            sys.exit(1)
        
        print(f"Found {len(ports_to_try)} port(s) to try: {', '.join(ports_to_try)}")
        print()
    
    # Try to connect to each port
    ser = None
    connected_port = None
    
    for port in ports_to_try:
        ser = connect_printer(port)
        if ser:
            connected_port = port
            break
        print()
    
    if not ser:
        print("✗ Failed to connect to printer on any port.")
        print("\nTroubleshooting:")
        print("  1. Make sure the printer is powered on")
        print("  2. Check that the USB-C cable is connected")
        print("  3. Verify the port name (check Device Manager on Windows,")
        print("     or 'ls /dev/tty*' on Linux/Mac)")
        print("  4. On Linux/Pi, you may need to add your user to the 'dialout' group:")
        print("     sudo usermod -a -G dialout $USER")
        print("     (then logout and login again)")
        sys.exit(1)
    
    print(f"✓ Connected to printer on {connected_port}")
    
    # Run tests
    try:
        all_passed = test_connection(ser)
        
        print("\n" + "=" * 60)
        if all_passed:
            print("✓ ALL TESTS PASSED - Printer connection is working!")
        else:
            print("✗ SOME TESTS FAILED - Check the errors above")
        print("=" * 60)
        
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n✗ Unexpected error during testing: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if ser and ser.is_open:
            ser.close()
            print("\nConnection closed.")


if __name__ == "__main__":
    main()

