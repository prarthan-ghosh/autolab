# Autolab Printer Interface

A web-based interface for controlling a 3D printer gantry with live camera monitoring. Runs on a Raspberry Pi and exposes a browser UI for jogging, homing, and emergency-stopping the nozzle while streaming a live MJPEG feed from the HQ Camera.

---

## Hardware Requirements

- Raspberry Pi 4 (or later)
- Raspberry Pi HQ Camera (IMX477) with Arducam C-mount LN046 manual focus lens
- Anycubic Kobra 2 Neo (or any Marlin-based printer) connected via USB

---

## Setup

```bash
# 1. Create venv (use --system-site-packages so picamera2 is accessible)
python3 -m venv --system-site-packages venv
source venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install system packages (Raspberry Pi only)
sudo apt update
sudo apt install -y python3-picamera2 python3-libcamera
```

---

## Configuration

Edit the `.yml` file that matches your mode before starting:

| File | Used in |
|------|---------|
| `config_test.yml` | Test / simulation mode |
| `config_connected.yml` | Connected mode (real hardware) |

Key sections:

- **`printer`** — serial device path, baud rate, axis swap, safe movement limits, default feedrate
- **`camera`** — sharpness and JPEG quality (focus is set physically on the lens)
- **`stream`** — preview resolution and frame rate
- **`simulation`** — `movement_delay` (test mode only, controls interpolation speed)

---

## Running

**Test mode** (simulation, no hardware required):

```bash
python server.py --mode test
```

**Connected mode** (real hardware):

```bash
python server.py --mode connected
```

**Custom host / port:**

```bash
python server.py --mode test --host 0.0.0.0 --port 8080
```

Access the web interface at `http://<raspberry-pi-ip>:5000`.

**systemd service:** See `setup_pi.sh` for an automated setup that installs a systemd unit.

---

## Using the Interface

- **Jog buttons** — move the nozzle in X, Y, or Z by the configured step size
- **Home** — run G28; nozzle returns to (0, 0, 0)
- **Emergency Stop** — sends M112 (or simulates it); all movement halts immediately
- **Clear E-Stop** — sends M999 to restart the printer firmware, then resumes normal operation
- **3D view** — shows real-time nozzle position within the safe-limit bounding box
- **Camera feed** — live MJPEG stream from the HQ Camera

---

## Accessing the Pi from Mac

**SSH:**
```bash
tailscale ssh pi@raspberrypi
```

**Mount the project directory for editing (sshfs):**
```bash
sshfs -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 \
      -o volname="Pi-Autolab" \
      pi@100.72.164.72:/home/pi/autolab ~/pi-autolab
```

The project files will appear at `~/pi-autolab`. Unmount when done:
```bash
umount ~/pi-autolab
```

**Browser access:**

Since both the Mac and the Pi are Tailscale devices, open your browser and navigate to:

```
http://raspberrypi:5000
```

or using the Pi's Tailscale IP directly:

```
http://100.72.164.72:5000
```

---

## Troubleshooting

**Serial permission denied**
```bash
sudo usermod -a -G dialout $USER
# then log out and back in
```

**Camera not detected**
```bash
# Check libcamera sees the sensor
libcamera-hello --list-cameras
# Ensure the ribbon cable is seated firmly
```

**Server refuses to start with "Missing required config key"**
A required YAML key is absent. The error message names the exact path (e.g. `simulation.movement_delay`). Add it to the config file and restart.

---

## Next Steps

- **Network security:** Restrict SocketIO CORS to known IPs (set `ALLOWED_ORIGINS` env var), add bearer token auth to WebSocket handshake, put Nginx with HTTPS in front, or use Tailscale/WireGuard for remote access.
- **Rate limiting:** Throttle WebSocket command events per client to prevent command flooding.
- **User access control:** Add session-based or token-based authentication before exposing to a wider network.

For architecture details, FSM documentation, and the full API reference, see [TECHNICAL.md](TECHNICAL.md).
