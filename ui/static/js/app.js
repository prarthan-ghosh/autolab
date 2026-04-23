/**
 * Printer Interface Frontend JavaScript
 * Handles SocketIO communication and UI updates
 */

class PrinterInterface {
    constructor() {
        this.socket = null;
        this.mode = this.detectMode();
        this.connected = false;
        this.systemStatus = 'idle';
        this.lastCommand = null;
        this.currentPosition = { x: 0, y: 0, z: 0 };
        this.config = null;
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.nozzleMarker = null;
        this.workspaceBox = null;
        this.moving = false; // Track if a movement is in progress
        this.animationFrameId = null; // Track animation frame for cleanup
        this.needsRender = false; // Flag to indicate if render is needed
        this._waitingForThree = false; // Prevent duplicate waitForThreeAndInit calls
        this._visualizationInitialized = false; // Prevent duplicate initialization
        
        // Load config and initialize
        console.log('Starting config load...');
        this.loadConfig().then(() => {
            console.log('Config loaded successfully:', this.config);
            this.initializeSocket();
            // Wait for DOM to be ready before binding events
            const initUI = () => {
                console.log('Initializing UI components...');
                this.bindEvents();
                this.updateUI();
                // Ensure THREE.js is loaded before initializing visualization
                console.log('Calling waitForThreeAndInit...');
                this.waitForThreeAndInit();
            };
            
            console.log('Document ready state:', document.readyState);
            if (document.readyState === 'loading') {
                console.log('DOM still loading, waiting for DOMContentLoaded...');
                document.addEventListener('DOMContentLoaded', initUI);
            } else {
                // DOM already ready
                console.log('DOM already ready, initializing UI now...');
                initUI();
            }
        }).catch((error) => {
            console.error('Failed to load config:', error);
            // Still try to initialize with defaults
            this.initializeSocket();
            const initUI = () => {
                console.log('Initializing UI with default config...');
                this.bindEvents();
                this.updateUI();
                console.log('Calling waitForThreeAndInit (with defaults)...');
                this.waitForThreeAndInit();
            };
            
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', initUI);
            } else {
                initUI();
            }
        });
    }
    
    async loadConfig() {
        try {
            console.log('Fetching config from /config endpoint...');
            // Use cache to avoid re-fetching on every page load, but add timeout
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout (increased)
            
            const response = await fetch('/config', { 
                cache: 'default',
                signal: controller.signal
            });
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                throw new Error(`Config endpoint returned ${response.status}: ${response.statusText}`);
            }
            
            this.config = await response.json();
            console.log('Config fetched successfully');
        } catch (error) {
            // Don't treat AbortError as a critical failure if it's just a timeout
            if (error.name === 'AbortError') {
                console.warn('Config fetch timed out, using defaults');
            } else {
                console.error('Failed to load config:', error);
            }
            console.log('Using default config values');
            // Default values
            this.config = {
                x_min: 0, x_max: 220,
                y_min: 0, y_max: 220,
                z_min: 0, z_max: 250,
                move_feedrate_default: 1500
            };
        }
    }

    async loadProtocols() {
        try {
            const res = await fetch('/protocols');
            const list = await res.json();
            const sel = document.getElementById('protocol-select');
            if (!sel) return;
            sel.innerHTML = '<option value="">— select a protocol —</option>';
            for (const p of list) {
                const opt = document.createElement('option');
                opt.value = p.path;
                opt.textContent = p.name;
                opt.dataset.description = p.description || '';
                sel.appendChild(opt);
            }
        } catch (e) {
            console.warn('Failed to load protocols:', e);
        }
    }

    runSelectedProtocol() {
        const sel = document.getElementById('protocol-select');
        if (!sel || !sel.value) return;
        const log = document.getElementById('protocol-log');
        if (log) log.textContent = '';
        this.socket.emit('protocol.run', { path: sel.value });
    }

    handleProtocolEvent(event) {
        const log = document.getElementById('protocol-log');
        if (!log) return;
        const parts = [`[${event.kind}]`];
        for (const [k, v] of Object.entries(event)) {
            if (k === 'kind' || k === 'timestamp') continue;
            parts.push(`${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`);
        }
        log.textContent += parts.join(' ') + '\n';
        log.scrollTop = log.scrollHeight;
    }

    detectMode() {
        // Check for mode in page title or data attribute
        const modeElement = document.querySelector('.mode-badge');
        if (modeElement) {
            const modeText = modeElement.textContent.toLowerCase();
            if (modeText.includes('test')) return 'test';
            if (modeText.includes('connected')) return 'connected';
        }
        
        // Check URL port
        if (window.location.port === '5000') return 'test';
        if (window.location.port === '5001') return 'connected';
        
        return 'test'; // Default
    }
    
    initializeSocket() {
        // Use current host and port
        console.log('Initializing socket connection...');
        console.log('Current URL:', window.location.href);
        console.log('SocketIO URL will be:', window.location.origin);
        
        // Try with explicit transport options and longer timeout
        this.socket = io(window.location.origin, {
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 5000,
            reconnectionAttempts: 10,
            timeout: 20000,  // 20 second timeout
            transports: ['websocket', 'polling'],  // Try websocket first, fallback to polling
            upgrade: true,
            rememberUpgrade: true
        });
        
        this.socket.on('connect', () => {
            console.log('✅ Connected to server via SocketIO');
            this.connected = true;
            this.updateConnectionStatus();
        });
        
        this.socket.on('connect_error', (error) => {
            console.error('❌ Socket connection error:', error);
            this.connected = false;
            this.updateConnectionStatus();
        });
        
        this.socket.on('disconnect', (reason) => {
            console.log('⚠️ Disconnected from server. Reason:', reason);
            this.connected = false;
            this.updateConnectionStatus();
        });
        
        this.socket.on('reconnect', (attemptNumber) => {
            console.log('✅ Reconnected to server after', attemptNumber, 'attempts');
            this.connected = true;
            this.updateConnectionStatus();
        });
        
        this.socket.on('reconnect_error', (error) => {
            console.error('❌ Reconnection error:', error);
        });
        
        this.socket.on('reconnect_failed', () => {
            console.error('❌ Failed to reconnect after all attempts');
            alert('Failed to connect to server. Please refresh the page.');
        });
        
        this.socket.on('status', (data) => {
            console.log('Status update:', data);
            this.mode = data.mode;
        });
        
        this.socket.on('telemetry.position', (data) => {
            this.updateTelemetry(data);
        });
        
        this.socket.on('telemetry.command_ack', (data) => {
            this.handleCommandAck(data);
        });

        this.socket.on('protocol.event', (event) => {
            this.handleProtocolEvent(event);
        });
        
        // Check connection status after a short delay
        setTimeout(() => {
            if (!this.connected) {
                console.warn('⚠️ Socket not connected after initialization. Current state:', this.socket.connected);
            }
        }, 2000);
    }
    
    bindEvents() {
        // Helper function to safely bind event
        const safeBind = (id, event, handler) => {
            const element = document.getElementById(id);
            if (element) {
                element.addEventListener(event, handler);
            } else {
                console.warn(`Element with id '${id}' not found, cannot bind event`);
            }
        };
        
        // Homing buttons
        safeBind('home-nozzle-btn', 'click', () => {
            this.homeNozzle();
        });
        safeBind('firmware-home-btn', 'click', () => {
            this.sendCommand('cmd.firmware_home_xy', {});
        });
        safeBind('set-z-ref-btn', 'click', () => {
            const input = document.getElementById('set-z-ref-input');
            const raw = input && input.value;
            const z = parseFloat(raw);
            if (isNaN(z)) {
                alert('Enter a numeric Z value.');
                return;
            }
            this.sendCommand('cmd.set_z_reference', { z });
        });
        
        // Incremental movement buttons (±1mm)
        safeBind('x-plus-btn', 'click', () => {
            this.moveIncremental('x', 1.0);
        });
        safeBind('x-minus-btn', 'click', () => {
            this.moveIncremental('x', -1.0);
        });
        // Incremental movement buttons (±1mm)
        safeBind('y-plus-btn', 'click', () => {
            console.log('Y+ button clicked');
            this.moveIncremental('y', 1.0);
        });
        safeBind('y-minus-btn', 'click', () => {
            console.log('Y- button clicked');
            this.moveIncremental('y', -1.0);
        });
        safeBind('z-plus-btn', 'click', () => {
            console.log('Z+ button clicked');
            this.moveIncremental('z', 1.0);
        });
        safeBind('z-minus-btn', 'click', () => {
            console.log('Z- button clicked');
            this.moveIncremental('z', -1.0);
        });
        
        // Nozzle movement controls - individual axis buttons
        safeBind('move-x-btn', 'click', () => {
            this.moveNozzleX();
        });
        
        safeBind('move-y-btn', 'click', () => {
            console.log('Move Y button clicked');
            this.moveNozzleY();
        });
        
        safeBind('move-z-btn', 'click', () => {
            console.log('Move Z button clicked');
            this.moveNozzleZ();
        });
        
        // Emergency stop
        safeBind('emergency-stop-btn', 'click', () => {
            this.emergencyStop();
        });
        
        // Clear error
        safeBind('clear-error-btn', 'click', () => {
            this.sendCommand('cmd.clear_error', {});
        });

        // Protocols
        safeBind('protocol-run-btn', 'click', () => this.runSelectedProtocol());
        safeBind('protocol-stop-btn', 'click', () => {
            if (this.socket) this.socket.emit('protocol.stop', {});
        });
        const sel = document.getElementById('protocol-select');
        if (sel) {
            sel.addEventListener('change', () => {
                const opt = sel.options[sel.selectedIndex];
                const desc = document.getElementById('protocol-description');
                if (desc) desc.textContent = opt ? (opt.dataset.description || '') : '';
            });
        }
        this.loadProtocols();

        // Camera capture
        safeBind('capture-btn', 'click', () => {
            this.captureImage();
        });
        
        // Zoom controls for 3D visualization
        safeBind('zoom-in-btn', 'click', () => {
            if (this.controls) {
                // Zoom in by reducing distance to target
                const currentDistance = this.camera.position.distanceTo(this.controls.target);
                const newDistance = Math.max(currentDistance * 0.8, this.controls.minDistance);
                const direction = new THREE.Vector3().subVectors(this.camera.position, this.controls.target).normalize();
                this.camera.position.copy(this.controls.target).add(direction.multiplyScalar(newDistance));
                this.controls.update();
                this.requestRender();
            }
        });
        
        safeBind('zoom-out-btn', 'click', () => {
            if (this.controls) {
                // Zoom out by increasing distance to target
                const currentDistance = this.camera.position.distanceTo(this.controls.target);
                const newDistance = Math.min(currentDistance * 1.2, this.controls.maxDistance);
                const direction = new THREE.Vector3().subVectors(this.camera.position, this.controls.target).normalize();
                this.camera.position.copy(this.controls.target).add(direction.multiplyScalar(newDistance));
                this.controls.update();
                this.requestRender();
            }
        });
        
        // Enter key support for inputs
        document.querySelectorAll('input[type="number"]').forEach(input => {
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.handleInputEnter(e.target);
                }
            });
        });
    }
    
    handleInputEnter(input) {
        // Find the associated button and click it
        const axisControl = input.closest('.axis-control');
        if (axisControl) {
            const button = axisControl.querySelector('button');
            if (button) {
                button.click();
            }
        } else {
            // Fallback for old control-row structure
            const controlRow = input.closest('.control-row');
            const button = controlRow?.querySelector('button');
            if (button) {
                button.click();
            }
        }
    }
    
    moveNozzleX() {
        const xInput = document.getElementById('nozzle-x-input');
        const x = parseFloat(xInput.value);
        if (isNaN(x)) {
            alert('Please enter a valid X position');
            return;
        }
        // Keep current Y and Z positions
        const feedrate = this.config.move_feedrate_default || 1500;
        this.sendCommand('cmd.move_nozzle', { 
            x, 
            y: this.currentPosition.y, 
            z: this.currentPosition.z, 
            feedrate 
        });
        // Clear input and update placeholder after move
        xInput.value = '';
        xInput.placeholder = `Current: ${x.toFixed(1)} mm`;
    }
    
    moveNozzleY() {
        const yInput = document.getElementById('nozzle-y-input');
        const y = parseFloat(yInput.value);
        if (isNaN(y)) {
            alert('Please enter a valid Y position');
            return;
        }
        console.log('Move Y button clicked - moving to Y:', y, 'current position:', this.currentPosition);
        // Keep current X and Z positions
        const feedrate = this.config.move_feedrate_default || 1500;
        this.sendCommand('cmd.move_nozzle', { 
            x: this.currentPosition.x, 
            y: y,
            z: this.currentPosition.z, 
            feedrate 
        });
        // Clear input and update placeholder after move
        yInput.value = '';
        yInput.placeholder = `Current: ${y.toFixed(1)} mm`;
    }
    
    moveNozzleZ() {
        const zInput = document.getElementById('nozzle-z-input');
        const z = parseFloat(zInput.value);
        if (isNaN(z)) {
            alert('Please enter a valid Z position');
            return;
        }
        console.log('Move Z button clicked - moving to Z:', z, 'current position:', this.currentPosition);
        // Keep current X and Y positions
        const feedrate = this.config.move_feedrate_default || 1500;
        this.sendCommand('cmd.move_nozzle', { 
            x: this.currentPosition.x, 
            y: this.currentPosition.y, 
            z: z,
            feedrate 
        });
        // Clear input and update placeholder after move
        zInput.value = '';
        zInput.placeholder = `Current: ${z.toFixed(1)} mm`;
    }
    
    homeNozzle() {
        this.sendCommand('cmd.home_nozzle', {});
    }
    
    moveIncremental(axis, delta) {
        console.log(`moveIncremental called: axis=${axis}, delta=${delta}, currentPos=`, this.currentPosition);
        const newPos = { ...this.currentPosition };
        newPos[axis] = newPos[axis] + delta;
        
        // Clamp to safe limits
        const limits = {
            x: { min: this.config.x_min, max: this.config.x_max },
            y: { min: this.config.y_min, max: this.config.y_max },
            z: { min: this.config.z_min, max: this.config.z_max }
        };
        
        newPos[axis] = Math.max(limits[axis].min, Math.min(limits[axis].max, newPos[axis]));
        
        console.log(`Sending move command: axis=${axis}, from ${this.currentPosition[axis]} to ${newPos[axis]}, full pos:`, newPos);
        
        const feedrate = this.config.move_feedrate_default || 1500;
        this.sendCommand('cmd.move_nozzle', {
            x: newPos.x,
            y: newPos.y,
            z: newPos.z,
            feedrate: feedrate
        });
    }
    
    emergencyStop() {
        if (confirm('Are you sure you want to activate emergency stop?')) {
            this.sendCommand('cmd.emergency_stop', {});
        }
    }
    
    async captureImage() {
        try {
            const response = await fetch('/capture', { method: 'POST' });
            const data = await response.json();
            
            if (data.success) {
                alert(`Image captured: ${data.filename}`);
            } else {
                alert(`Capture failed: ${data.error}`);
            }
        } catch (error) {
            alert(`Capture error: ${error.message}`);
        }
    }
    
    sendCommand(command, data) {
        if (!this.socket || !this.socket.connected) {
            alert('Not connected to server. Please wait for connection...');
            return;
        }
        
        // Check if this is a movement command
        const isMovementCommand = command.startsWith('cmd.move_') || command === 'cmd.home_nozzle' || command === 'cmd.firmware_home_xy';
        if (isMovementCommand && this.moving) {
            console.log('Movement in progress — command dropped:', command);
            return;
        }
        
        console.log('Sending command:', command, data);
        this.socket.emit(command, data);
        this.lastCommand = { command, data, timestamp: Date.now() };
        this.updateLastCommand();
        
        // Set moving state for movement commands
        if (isMovementCommand) {
            this.moving = true;
            this.updateMovementButtons();
        }
    }
    
    updateTelemetry(data) {
        // Update current position - hardware should now report positions correctly
        // after the swap is handled in the hardware layer
        this.currentPosition = {
            x: data.nozzle.x,
            y: data.nozzle.y,
            z: data.nozzle.z
        };
        
        // Update nozzle position display
        document.getElementById('nozzle-x').textContent = data.nozzle.x.toFixed(1);
        document.getElementById('nozzle-y').textContent = data.nozzle.y.toFixed(1);
        document.getElementById('nozzle-z').textContent = data.nozzle.z.toFixed(1);
        
        // Update input field placeholders with current position (if empty)
        const xInput = document.getElementById('nozzle-x-input');
        const yInput = document.getElementById('nozzle-y-input');
        const zInput = document.getElementById('nozzle-z-input');
        
        // Always update placeholders to show current position when input is empty
        if (xInput && !xInput.value) {
            xInput.placeholder = `Current: ${data.nozzle.x.toFixed(1)} mm`;
        }
        if (yInput && !yInput.value) {
            yInput.placeholder = `Current: ${data.nozzle.y.toFixed(1)} mm`;
        }
        if (zInput && !zInput.value) {
            zInput.placeholder = `Current: ${data.nozzle.z.toFixed(1)} mm`;
        }
        
        // Update 3D visualization
        this.updateVisualization();
        
        // Update system status
        const previousStatus = this.systemStatus;
        this.systemStatus = data.status;
        this.updateSystemStatus();
        
        // Update movement state based on system status
        // This is the primary source of truth for movement state
        if (this.systemStatus === 'moving') {
            if (!this.moving) {
                console.log('System status changed to moving - disabling buttons');
                this.moving = true;
                this.updateMovementButtons();
            }
        } else if (this.systemStatus === 'idle') {
            if (this.moving) {
                console.log('System status changed to idle - enabling buttons');
                this.moving = false;
                this.updateMovementButtons();
            }
        }
    }
    
    waitForThreeAndInit(maxAttempts = 50) {
        // Prevent multiple calls
        if (this._waitingForThree) {
            console.log('Already waiting for THREE.js, skipping duplicate call');
            return;
        }
        
        console.log(`waitForThreeAndInit called, THREE defined: ${typeof THREE !== 'undefined'}, attempts: ${maxAttempts}`);
        
        if (typeof THREE !== 'undefined') {
            this._waitingForThree = false;
            console.log('THREE.js is available, version:', THREE.REVISION);
            console.log('Scheduling visualization initialization in 100ms...');
            // Use a small delay to ensure container is rendered
            setTimeout(() => {
                console.log('Timeout fired, calling initializeVisualization...');
                this.initializeVisualization();
            }, 100);
        } else if (maxAttempts > 0) {
            this._waitingForThree = true;
            console.log(`Waiting for THREE.js... (${maxAttempts} attempts remaining)`);
            setTimeout(() => {
                this._waitingForThree = false;
                this.waitForThreeAndInit(maxAttempts - 1);
            }, 100);
        } else {
            this._waitingForThree = false;
            console.error('THREE.js failed to load after 5 seconds! Visualization will not work.');
            // Try anyway - maybe it loaded but check failed
            console.log('Attempting to initialize anyway...');
            setTimeout(() => {
                this.initializeVisualization();
            }, 100);
        }
    }
    
    initializeVisualization() {
        // Prevent duplicate initialization
        if (this._visualizationInitialized) {
            console.log('Visualization already initialized, skipping...');
            return;
        }
        
        console.log('=== Starting visualization initialization ===');
        const container = document.getElementById('visualization-container');
        if (!container) {
            console.error('Cannot find visualization-container element!');
            const errorDiv = document.createElement('div');
            errorDiv.style.cssText = 'padding: 20px; color: red; background: #ffe0e0; border: 2px solid red;';
            errorDiv.textContent = 'ERROR: visualization-container element not found!';
            document.body.appendChild(errorDiv);
            return;
        }
        console.log('Container found:', container);
        
        if (!this.config) {
            console.error('Config not loaded yet!');
            container.innerHTML = '<div style="padding: 20px; color: red;">Config not loaded yet!</div>';
            return;
        }
        console.log('Config loaded:', this.config);
        
        // Check if THREE is loaded
        if (typeof THREE === 'undefined') {
            console.error('THREE.js is not loaded! Check if the script tag is correct.');
            container.innerHTML = '<div style="padding: 20px; color: red;">THREE.js not loaded! Check browser console.</div>';
            return;
        }
        console.log('THREE.js version:', THREE.REVISION);
        
        try {
        
        console.log('Initializing visualization with config:', this.config);
        console.log('Container size:', container.clientWidth, 'x', container.clientHeight);
        console.log('Container computed style:', window.getComputedStyle(container).width, 'x', window.getComputedStyle(container).height);
        
        // Make sure container has a size - force it if needed
        let width = container.clientWidth;
        let height = container.clientHeight;
        
        if (width === 0 || height === 0) {
            console.warn('Container has zero size! Setting default size.');
            // Try to get size from computed style
            const computedStyle = window.getComputedStyle(container);
            const computedWidth = parseInt(computedStyle.width);
            const computedHeight = parseInt(computedStyle.height);
            
            if (computedWidth > 0 && computedHeight > 0) {
                width = computedWidth;
                height = computedHeight;
                console.log('Using computed size:', width, 'x', height);
            } else {
                // Force a size
                container.style.width = '400px';
                container.style.height = '400px';
                width = 400;
                height = 400;
                console.log('Forced container size to:', width, 'x', height);
            }
        }
        
        // Scene setup
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0xf0f0f0);
        
        // Calculate workspace dimensions once
        // Coordinate mapping: Printer (X, Y, Z) -> Three.js (x, y, -z)
        // Printer X -> Three.js +x (Right)
        // Printer Y -> Three.js -z (Back)
        // Printer Z -> Z=0 is at Bed Surface (Three.js low Y), positive Z is UP (Three.js high Y)
        const workspaceWidth = this.config.x_max - this.config.x_min;
        const workspaceHeight = this.config.z_max - this.config.z_min;
        const workspaceDepth = this.config.y_max - this.config.y_min;
        
        // Use physical dimensions for centering
        const bedW = this.config.bed_width || 230;
        const bedD = this.config.bed_depth || 230;
        const frameH = this.config.frame_height || 400;
        const frameW = this.config.frame_width || 300;
        
        const centerX = bedW / 2;
        const centerY = frameH / 2;
        const centerZ = -bedD / 2;
        
        // Camera setup
        this.camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 2000);
        const maxDim = Math.max(frameW, frameH, bedD);
        this.camera.position.set(centerX + maxDim * 0.8, centerY + maxDim * 0.4, maxDim * 0.8);
        this.camera.lookAt(centerX, centerY, centerZ);
        
        // Renderer setup
        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        this.renderer.setSize(width, height);
        this.renderer.setClearColor(0xf0f0f0, 1);
        container.innerHTML = '';
        container.appendChild(this.renderer.domElement);
        
        if (typeof THREE.OrbitControls !== 'undefined') {
            this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
            this.controls.target.set(centerX, centerY, centerZ);
            this.controls.enableDamping = true;
            this.controls.update();
        }

        this.scene.add(new THREE.AmbientLight(0xffffff, 0.6));
        const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
        dirLight.position.set(100, 200, 100);
        this.scene.add(dirLight);

        const makeBox = (w, h, d, color) => {
            return new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshPhongMaterial({ color }));
        };

        const extrusion = 20;
        const frameMat = 0x333333;
        const railMat = 0x555555;
        const bedSurfaceY = extrusion + 5;

        // --- Static Base Frame ---
        const baseGroup = new THREE.Group();
        // Centered rails relative to centerX
        const baseL = makeBox(extrusion, extrusion, bedD * 1.5, frameMat);
        baseL.position.set(centerX - frameW/2 + extrusion/2, extrusion/2, -bedD/2);
        baseGroup.add(baseL);
        const baseR = makeBox(extrusion, extrusion, bedD * 1.5, frameMat);
        baseR.position.set(centerX + frameW/2 - extrusion/2, extrusion/2, -bedD/2);
        baseGroup.add(baseR);
        const baseCenter = makeBox(extrusion * 2, extrusion, bedD * 1.2, railMat);
        baseCenter.position.set(centerX, extrusion/2, -bedD/2);
        baseGroup.add(baseCenter);

        // --- Static Uprights ---
        // Position gantry so the nozzle tip is at Three.js Z=0 when Printer Y=0
        // Nozzle is offset forward from the rail by 'extrusion'
        const uprightZ = -extrusion; 
        
        const uprightL = makeBox(extrusion, frameH, extrusion, frameMat);
        uprightL.position.set(centerX - frameW/2 + extrusion/2, frameH/2, uprightZ);
        baseGroup.add(uprightL);
        const uprightR = makeBox(extrusion, frameH, extrusion, frameMat);
        uprightR.position.set(centerX + frameW/2 - extrusion/2, frameH/2, uprightZ);
        baseGroup.add(uprightR);
        const topBar = makeBox(frameW, extrusion, extrusion, frameMat);
        topBar.position.set(centerX, frameH - extrusion/2, uprightZ);
        baseGroup.add(topBar);
        this.scene.add(baseGroup);

        // --- Bed ---
        this.bedGroup = new THREE.Group();
        // Position bed so its front edge (Printer Y=0) is at Three.js Z=0
        const bedPlate = makeBox(bedW, 4, bedD, 0x222222);
        bedPlate.position.set(centerX, bedSurfaceY - 2, -bedD/2);
        this.bedGroup.add(bedPlate);
        const gridHelper = new THREE.GridHelper(bedW, 10, 0x888888, 0x444444);
        gridHelper.position.set(centerX, bedSurfaceY - 1.9, -bedD/2);
        this.bedGroup.add(gridHelper);
        this.scene.add(this.bedGroup);

        // --- Origin marker (at Printer 0,0,0 -> Three.js 0, _zZeroY, 0) ---
        const originMarker = new THREE.Mesh(new THREE.SphereGeometry(4, 16, 16), new THREE.MeshBasicMaterial({ color: 0x00ff00 }));
        this._zZeroY = bedSurfaceY; 
        originMarker.position.set(0, this._zZeroY, 0);
        this.scene.add(originMarker);

        // --- X-Gantry ---
        this.gantryGroup = new THREE.Group();
        const xRail = makeBox(frameW - extrusion*2, extrusion, extrusion, railMat);
        xRail.position.set(centerX, 0, uprightZ);
        this.gantryGroup.add(xRail);

        // --- Printhead ---
        this.printheadGroup = new THREE.Group();
        const carriage = makeBox(40, 45, 40, 0x111111);
        carriage.position.set(0, 0, extrusion); // Offset forward from rail so nozzle is at Z=0
        this.printheadGroup.add(carriage);
        this.nozzleMarker = new THREE.Mesh(new THREE.ConeGeometry(5, 10, 8), new THREE.MeshPhongMaterial({ color: 0xccaa00 }));
        this.nozzleMarker.rotation.x = Math.PI;
        this.nozzleMarker.position.set(0, -25, extrusion);
        this.printheadGroup.add(this.nozzleMarker);
        this.nozzleTip = new THREE.Mesh(new THREE.SphereGeometry(2, 8, 8), new THREE.MeshBasicMaterial({ color: 0xff0000 }));
        const nozzleTipOffset = -30;
        this.nozzleTip.position.set(0, nozzleTipOffset, extrusion);
        this.printheadGroup.add(this.nozzleTip);
        this.gantryGroup.add(this.printheadGroup);
        this.scene.add(this.gantryGroup);
        
        // Calibration: At logical Z=0, nozzle tip should be at _zZeroY
        // gantryGroup.y + nozzleTipOffset = _zZeroY  => baseY = _zZeroY - nozzleTipOffset
        this._gantryBaseY = this._zZeroY - nozzleTipOffset;

        // --- Workspace Volume ---
        const volGeo = new THREE.BoxGeometry(workspaceWidth, workspaceHeight, workspaceDepth);
        const volEdges = new THREE.EdgesGeometry(volGeo);
        const volMat = new THREE.LineBasicMaterial({ color: 0x0088ff, transparent: true, opacity: 0.3 });
        this.workspaceBox = new THREE.LineSegments(volEdges, volMat);
        this.workspaceBox.position.set(
            this.config.x_min + workspaceWidth / 2,
            this._zZeroY + workspaceHeight / 2,
            -(this.config.y_min + workspaceDepth / 2)
        );
        this.scene.add(this.workspaceBox);

        const axesHelper = new THREE.AxesHelper(50);
        axesHelper.position.set(0, this._zZeroY, 0);
        this.scene.add(axesHelper);

        this.addDimensionLabels(workspaceWidth, workspaceHeight, workspaceDepth, this._zZeroY);
        
        // Handle window resize
        window.addEventListener('resize', () => {
            const newWidth = container.clientWidth;
            const newHeight = container.clientHeight;
            const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
            this.camera.aspect = newWidth / newHeight;
            this.camera.updateProjectionMatrix();
            this.renderer.setPixelRatio(pixelRatio);
            this.renderer.setSize(newWidth, newHeight);
            this.requestRender();
        });
        
        // Initial render - do it immediately
        console.log('Performing initial render...');
        console.log('Scene children count:', this.scene.children.length);
        console.log('Scene children:', this.scene.children.map(c => c.type || c.constructor.name));
        
        if (this.renderer && this.scene && this.camera) {
            // Render multiple times to ensure visibility
            for (let i = 0; i < 3; i++) {
                this.renderer.render(this.scene, this.camera);
                console.log(`Render ${i + 1} complete`);
            }
            
            // Verify canvas is actually in DOM and visible
            const canvas = this.renderer.domElement;
            const rect = canvas.getBoundingClientRect();
            console.log('Canvas bounding rect:', rect);
            console.log('Canvas visible:', rect.width > 0 && rect.height > 0);
            
            // Force a render after a brief delay
            setTimeout(() => {
                if (this.renderer && this.scene && this.camera) {
                    this.renderer.render(this.scene, this.camera);
                    console.log('Delayed render complete');
                }
            }, 200);
        } else {
            console.error('Cannot render - missing components:', {
                renderer: !!this.renderer,
                scene: !!this.scene,
                camera: !!this.camera
            });
            container.innerHTML = '<div style="padding: 20px; color: red;">Failed to create renderer/scene/camera. Check console.</div>';
        }
        
        // Set up on-demand render loop (only renders when needed)
        // But always render on the first frame to ensure visibility
        let firstFrame = true;
        const animate = () => {
            this.animationFrameId = requestAnimationFrame(animate);
            
            // Update OrbitControls if available (for smooth interaction)
            if (this.controls) {
                this.controls.update();
                // Always render when controls are active
                if (this.renderer && this.scene && this.camera) {
                    this.renderer.render(this.scene, this.camera);
                    this.needsRender = false;
                    if (firstFrame) {
                        firstFrame = false;
                        console.log('First animation frame rendered');
                    }
                }
            } else if ((this.needsRender || firstFrame) && this.renderer && this.scene && this.camera) {
                // Only render when needed if no controls
                this.renderer.render(this.scene, this.camera);
                this.needsRender = false;
                if (firstFrame) {
                    firstFrame = false;
                    console.log('First animation frame rendered');
                }
            }
        };
        animate();
        console.log('Started on-demand animation loop');
        
        // Also update visualization if nozzle marker exists
        if (this.nozzleMarker) {
            this.updateVisualization();
        }
        
        this._visualizationInitialized = true;
        console.log('Visualization initialization complete!');
        } catch (error) {
            console.error('Error initializing visualization:', error);
            console.error(error.stack);
            // Show user-friendly error message
            const container = document.getElementById('visualization-container');
            if (container) {
                container.innerHTML = '<div style="padding: 20px; color: red;">Error loading 3D visualization. Please check the browser console for details.</div>';
            }
            this._visualizationInitialized = true; // Mark as attempted even on error
        }
    }
    
    addDimensionLabels(width, height, depth, bedSurfaceY) {
        // Create text sprites for dimension labels
        // We'll use canvas-based text since TextGeometry requires a font loader
        
        const createTextSprite = (text, position, color = 0x000000, backgroundColor = 0xffffff, vertical = false, isMaxLabel = false) => {
            // Use high resolution canvas for crisp text
            const scale = 4; // 4x resolution for very sharp text
            const padding = isMaxLabel ? 12 : 16;
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');
            
            // Measure text first to size canvas appropriately
            const fontSize = isMaxLabel ? 16 : 20;
            context.font = `${isMaxLabel ? '500' : '600'} ${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`;
            const metrics = context.measureText(text);
            const textWidth = metrics.width;
            const textHeight = fontSize;
            
            // For vertical text, swap dimensions
            const canvasWidth = vertical ? (textHeight + padding * 2) : (textWidth + padding * 2);
            const canvasHeight = vertical ? (textWidth + padding * 2) : (textHeight + padding * 2);
            
            canvas.width = canvasWidth * scale;
            canvas.height = canvasHeight * scale;
            
            // Scale up the context for high DPI rendering
            context.scale(scale, scale);
            
            // Rotate canvas if vertical (rotate clockwise)
            if (vertical) {
                context.translate(canvasWidth / 2, canvasHeight / 2);
                context.rotate(-Math.PI / 2);
                context.translate(-canvasHeight / 2, -canvasWidth / 2);
            }
            
            // Draw background with subtle shadow and modern styling
            const bgR = (backgroundColor >> 16) & 0xFF;
            const bgG = (backgroundColor >> 8) & 0xFF;
            const bgB = backgroundColor & 0xFF;
            
            // Create subtle gradient background
            const gradient = context.createLinearGradient(0, 0, 0, canvasHeight);
            gradient.addColorStop(0, `rgba(${bgR}, ${bgG}, ${bgB}, 0.95)`);
            gradient.addColorStop(1, `rgba(${bgR}, ${bgG}, ${bgB}, 0.9)`);
            
            const cornerRadius = 8;
            const x = padding / 2;
            const y = padding / 2;
            const w = textWidth + padding;
            const h = textHeight + padding;
            
            // Helper function to draw rounded rectangle
            const drawRoundedRect = (ctx, x, y, w, h, radius) => {
                ctx.beginPath();
                ctx.moveTo(x + radius, y);
                ctx.lineTo(x + w - radius, y);
                ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
                ctx.lineTo(x + w, y + h - radius);
                ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
                ctx.lineTo(x + radius, y + h);
                ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
                ctx.lineTo(x, y + radius);
                ctx.quadraticCurveTo(x, y, x + radius, y);
                ctx.closePath();
            };
            
            // Draw subtle shadow first
            context.fillStyle = 'rgba(0, 0, 0, 0.15)';
            drawRoundedRect(context, x + 1, y + 2, w, h, cornerRadius);
            context.fill();
            
            // Draw background with gradient
            context.fillStyle = gradient;
            drawRoundedRect(context, x, y, w, h, cornerRadius);
            context.fill();
            
            // Draw subtle border (lighter for max labels)
            const borderColor = isMaxLabel 
                ? `rgba(${(color >> 16) & 0xFF}, ${(color >> 8) & 0xFF}, ${color & 0xFF}, 0.2)`
                : `rgba(${(color >> 16) & 0xFF}, ${(color >> 8) & 0xFF}, ${color & 0xFF}, 0.3)`;
            context.strokeStyle = borderColor;
            context.lineWidth = 1.5;
            drawRoundedRect(context, x, y, w, h, cornerRadius);
            context.stroke();
            
            // Draw text with better color and no shadow for cleaner look
            const textR = (color >> 16) & 0xFF;
            const textG = (color >> 8) & 0xFF;
            const textB = color & 0xFF;
            context.fillStyle = `rgba(${textR}, ${textG}, ${textB}, 0.9)`;
            context.font = `${isMaxLabel ? '500' : '600'} ${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`;
            context.textAlign = 'center';
            context.textBaseline = 'middle';
            context.fillText(text, (textWidth + padding) / 2 + padding / 2, (textHeight + padding) / 2 + padding / 2);
            
            const texture = new THREE.CanvasTexture(canvas);
            texture.minFilter = THREE.LinearFilter;
            texture.magFilter = THREE.LinearFilter;
            texture.generateMipmaps = false;
            
            const spriteMaterial = new THREE.SpriteMaterial({ 
                map: texture,
                transparent: true,
                alphaTest: 0.01,
                depthTest: false,
                depthWrite: false
            });
            const sprite = new THREE.Sprite(spriteMaterial);
            
            // Adjust scale based on orientation
            if (vertical) {
                sprite.scale.set(35, 140, 1);
            } else {
                sprite.scale.set(140, 35, 1);
            }
            
            sprite.position.copy(position);
            return sprite;
        };
        
        // Add professional dimension labels at key positions
        // Strategically positioned to avoid overlaps with rotations where needed
        // Use white background with colored borders for professional look
        
        // Origin label at front-left corner
        const originLabel = createTextSprite(
            `Origin (0,0,0)`,
            new THREE.Vector3(
                this.config.x_min - 20,
                bedSurfaceY,
                -this.config.y_min + 20
            ),
            0x2d8659,  // Softer green
            0xffffff,  // White background
            false,
            false
        );
        this.scene.add(originLabel);
        
        // Dimensions tuple label in corner - shows (X, Y, Z) dimensions
        const dimensionsLabel = createTextSprite(
            `Max (${width.toFixed(0)}, ${depth.toFixed(0)}, ${height.toFixed(0)}) mm`,
            new THREE.Vector3(
                this.config.x_max + 20,
                bedSurfaceY + height,
                -this.config.y_max - 20
            ),
            0x4a5568,  // Dark gray
            0xffffff,  // White background
            false,
            false
        );
        this.scene.add(dimensionsLabel);
    }
    
    requestRender() {
        // Mark that a render is needed
        this.needsRender = true;
    }
    
    updateVisualization() {
        if (!this.renderer || !this.scene || !this.camera) {
            return;
        }

        // Coordinate mapping: Printer (X, Y, Z) -> Three.js (x, y, -z)
        const px = this.currentPosition.x;
        const py = this.currentPosition.y;
        const pz = this.currentPosition.z;

        // Bed moves in printer Y → Three.js -Z
        if (this.bedGroup) {
            this.bedGroup.position.z = -py;
        }

        // Gantry moves in printer Z
        // Z=0 is at Bed Surface (at _gantryBaseY), positive Z moves UP (increases Y)
        if (this.gantryGroup) {
            this.gantryGroup.position.y = (this._gantryBaseY || 50) + pz;
        }

        // Printhead moves in printer X → Three.js X
        if (this.printheadGroup) {
            this.printheadGroup.position.x = px;
        }

        // Force immediate render
        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }
        this.requestRender();
    }
    
    handleCommandAck(data) {
        console.log('Command ACK:', data);
        
        // Check if this was a movement command
        const isMovementCommand = this.lastCommand && (
            this.lastCommand.command.startsWith('cmd.move_') ||
            this.lastCommand.command === 'cmd.home_nozzle' ||
            this.lastCommand.command === 'cmd.firmware_home_xy'
        );
        
        // Update UI based on command status
        if (data.status === 'ok') {
            console.log('Command completed successfully');
            // Clear moving state when movement command completes
            // But don't clear if system status is still 'moving' (wait for telemetry)
            if (isMovementCommand && this.systemStatus !== 'moving') {
                console.log('Clearing moving state - command completed and system idle');
                this.moving = false;
                this.updateMovementButtons();
            } else if (isMovementCommand) {
                console.log('Command completed but system still moving - waiting for telemetry');
            }
        } else if (data.status === 'err') {
            console.error('Command failed:', data.message);
            alert(`Command failed: ${data.message}`);
            // Always clear moving state on error
            if (isMovementCommand) {
                console.log('Clearing moving state due to error');
                this.moving = false;
                this.updateMovementButtons();
            }
        }
        
        this.lastCommand = { ...this.lastCommand, ack: data };
        this.updateLastCommand();
    }
    
    updateMovementButtons() {
        // Get all movement-related buttons
        const movementButtons = [
            'home-nozzle-btn',
            'move-x-btn',
            'move-y-btn',
            'move-z-btn',
            'x-plus-btn', 'x-minus-btn',
            'y-plus-btn', 'y-minus-btn',
            'z-plus-btn', 'z-minus-btn'
        ];
        
        movementButtons.forEach(btnId => {
            const btn = document.getElementById(btnId);
            if (btn) {
                btn.disabled = this.moving;
            }
        });
    }
    
    updateConnectionStatus() {
        const statusElement = document.getElementById('connection-status');
        if (this.connected) {
            statusElement.textContent = 'Connected';
            statusElement.className = 'status-connected';
        } else {
            statusElement.textContent = 'Disconnected';
            statusElement.className = 'status-disconnected';
        }
    }
    
    updateSystemStatus() {
        const statusElement = document.getElementById('system-status');
        statusElement.textContent = this.systemStatus.charAt(0).toUpperCase() + this.systemStatus.slice(1);
        statusElement.className = `status-${this.systemStatus}`;

        // Show/hide clear error button
        const errorGroup = document.getElementById('error-recovery-group');
        if (errorGroup) {
            errorGroup.style.display = this.systemStatus === 'error' ? 'block' : 'none';
        }
    }
    
    updateLastCommand() {
        const lastCommandElement = document.getElementById('last-command');
        if (this.lastCommand) {
            const time = new Date(this.lastCommand.timestamp).toLocaleTimeString();
            lastCommandElement.textContent = `${this.lastCommand.command} at ${time}`;
        } else {
            lastCommandElement.textContent = 'None';
        }
    }
    
    updateUI() {
        this.updateConnectionStatus();
        this.updateSystemStatus();
        this.updateLastCommand();
        this.updateMovementButtons();
    }
}

// Wait for Three.js to load before initializing
function waitForThreeJS(callback, maxAttempts = 20) {
    console.log(`waitForThreeJS: THREE defined = ${typeof THREE !== 'undefined'}, attempts = ${maxAttempts}`);
    if (typeof THREE !== 'undefined') {
        console.log('Three.js is available, version:', THREE.REVISION);
        callback();
    } else if (maxAttempts > 0) {
        setTimeout(() => waitForThreeJS(callback, maxAttempts - 1), 100);
    } else {
        console.error('Three.js failed to load after 2 seconds!');
        console.log('Attempting to initialize anyway - THREE might load later...');
        // Still try to initialize, but visualization will fail gracefully
        callback();
    }
}

// Initialize the application when the page loads
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, waiting for Three.js...');
    console.log('THREE at DOMContentLoaded:', typeof THREE);
    waitForThreeJS(() => {
        console.log('Initializing PrinterInterface...');
        console.log('THREE at initialization:', typeof THREE);
        try {
            window.printerInterface = new PrinterInterface();
            console.log('PrinterInterface created successfully');
        } catch (error) {
            console.error('Failed to create PrinterInterface:', error);
            console.error(error.stack);
        }
    });
});
