--#include <AccelStepper.h>

// --- PIN DEFINITIONS ---
const int dirPin = 3;  // Connects to the DIR pin on the A4988
const int stepPin = 2; // Connects to the STEP pin on the A4988

// --- MOTOR PARAMETERS ---
const int STEPS_PER_REV = 200; 

// Default/Initial Speed and Acceleration
const float DEFAULT_MAX_SPEED = 800.0; 
const float DEFAULT_ACCELERATION = 400.0; 

// AccelStepper setup: (Type, Step Pin, Dir Pin)
AccelStepper stepper(AccelStepper::DRIVER, stepPin, dirPin);

// --- GLOBAL STATE VARIABLES ---

// Tracks the current operating state (Moving or Stopped)
bool isMoving = false; 

// Default limits (5 revolutions safe travel - used only until user sets limits)
const long DEFAULT_SAFE_LIMIT = STEPS_PER_REV * 5; 

// Target positions for the oscillatory movement (NOW SET BY SLU/SLD)
// New Coordinate System: Lower Limit (DOWN) is COORDINATE 0. Upper Limit (UP) is a negative coordinate.
long targetPositionDown = 0; // Default Lower Limit is now 0 (Coordinate Origin)
long targetPositionUp = DEFAULT_SAFE_LIMIT * -1; // Default Upper Limit is -1000 steps
long currentTarget = 0; // The current destination the motor is heading toward

// Step size for jogging commands (Up/Down)
long jogStepSize = 100;

// Function prototypes to fix potential compilation warnings
void printHelp();
void printPositionInfo();
void handleSerialCommands();


void setup() {
  Serial.begin(9900); 
  Serial.println("--- AccelStepper CLI Ready ---");
  // Removed Calibration Phase messages for cleaner startup.
  printHelp();

  // Apply default speed and acceleration
  stepper.setMaxSpeed(DEFAULT_MAX_SPEED); 
  stepper.setAcceleration(DEFAULT_ACCELERATION); 
  
  // Set initial position to 0 (default starting position)
  stepper.setCurrentPosition(0); 
  currentTarget = targetPositionUp; 
}

// Helper function to print available commands
void printHelp() {
  Serial.println("Commands:");
  Serial.println("  G   : Go (Starts oscillatory loop between saved limits)"); 
  Serial.println("  X   : Stop (Halt the motor smoothly)");
  Serial.println("  P   : Print Current Position and Direction");
  Serial.println("  S<num> : Set Max Speed (e.g., S800)");
  Serial.println("  SLU : Set Upper Limit (Saves current coordinate to the UP limit)"); 
  Serial.println("  SLD : Set Lower Limit (Sets current position to COORDINATE 0)"); // UPDATED DESCRIPTION
  Serial.println("  U   : Jog Upward (move -JOG_STEP_SIZE steps)"); 
  Serial.println("  D   : Jog Downward (move +JOG_STEP_SIZE steps)"); 
  Serial.println("  J<num> : Set Jog Step Size (e.g., J50)"); 
  Serial.println("------------------------------------");
}

// Function to display current position and movement status
void printPositionInfo() {
  long currentPos = stepper.currentPosition();
  String direction = "Stationary";
  
  // Determine direction based on whether it's moving and where the target is
  if (stepper.distanceToGo() != 0) {
    if (stepper.targetPosition() > currentPos) {
      // Moving to a more POSITIVE position (New DOWNWARD)
      direction = "Positive/Downward"; 
    } else {
      // Moving to a more NEGATIVE position (New UPWARD)
      direction = "Negative/Upward"; 
    }
  }

  Serial.println("--- Current Stepper State ---");
  // COORDINATE SYSTEM: 0 is always the Lower Limit
  Serial.print("COORDINATE: "); 
  Serial.print(currentPos);
  Serial.println(" steps (0 is Lower Limit)"); // Context added
  
  Serial.print("MOVEMENT: ");
  Serial.println(stepper.distanceToGo() != 0 ? "Active" : "Stopped"); 
  Serial.print("DIRECTION: ");
  Serial.println(direction);
  Serial.print("UPPER LIMIT (UP Target): ");
  Serial.print(targetPositionUp);
  Serial.print(" | LOWER LIMIT (DOWN Target, COORD 0): ");
  Serial.println(targetPositionDown);
  Serial.println("-----------------------------");
}


// Function to handle incoming Serial commands
void handleSerialCommands() {
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    
    // Log the received command 
    Serial.println("------------------------------------");
    Serial.print("RECEIVED COMMAND: ");
    Serial.println(input);
    Serial.println("------------------------------------");
    
    // Status message specific to the executed command
    String statusMessage = "";
    bool commandHandled = false;
    
    // --- Check for three-character commands (SLU and SLD) ---
    String commandPrefix = input.length() >= 3 ? input.substring(0, 3) : "";
    commandPrefix.toUpperCase();

    if (commandPrefix.equals("SLU")) {
        // SLU: Set Limit Up (Saves current position to the UP limit)
        long currentPos = stepper.currentPosition();
        if (currentPos < targetPositionDown) { // Check: Must be above/less than the LL (0)
            targetPositionUp = currentPos;
            statusMessage = "Upper Limit (UP) saved at coordinate: " + String(currentPos) + " steps.";
        } else {
            statusMessage = "Error: Upper Limit must be a coordinate less than the Lower Limit (0).";
        }
        commandHandled = true;
    } else if (commandPrefix.equals("SLD")) {
        // SLD: Set Lower Limit (Sets current position to COORDINATE 0)
        
        // 1. Reset AccelStepper's internal position to 0
        stepper.setCurrentPosition(0); 
        
        // 2. Set the Lower Limit target to 0
        targetPositionDown = 0;
        
        // 3. Status update
        statusMessage = "Lower Limit (DOWN) set, current position is now COORDINATE 0.";
        commandHandled = true;
    }
    
    // --- Check for single-character commands (G, X, P, S, U, D, J, H) ---
    if (!commandHandled) {
        char command = toupper(input.charAt(0)); 

        switch (command) {
          case 'G': // Go/Start - ALWAYS START DOWNWARD
            // Check for valid limits before starting oscillation
            if (targetPositionUp < targetPositionDown) { // UP (-ve) must be less than DOWN (0)
                isMoving = true;
                
                // Force the initial target to DOWNWARD (Lower Limit, which is 0)
                currentTarget = targetPositionDown; 
                
                // Start the move from the motor's current position towards the new target.
                stepper.moveTo(currentTarget);
                
                statusMessage = "Motor started/resumed, oscillating between limits.";
            } else {
                statusMessage = "Error: Limits are invalid. SLU coordinate must be less than SLD (0).";
            }
            break;

          case 'X': // Stop
            isMoving = false;
            stepper.stop(); // Stops with controlled deceleration
            statusMessage = "Motor stopping gracefully...";
            break;

          case 'P': // Print Position
            statusMessage = "Position requested.";
            break;
            
          // Removed case 'Z' here

          case 'S': { // Set Speed
            if (input.length() > 1) {
              float newSpeed = input.substring(1).toFloat();
              if (newSpeed > 0) {
                stepper.setMaxSpeed(newSpeed);
                statusMessage = "Max Speed set to: " + String(newSpeed);
              } else {
                statusMessage = "Error: Invalid speed. Please use a positive number.";
              }
            }
            break;
          }

          case 'U': // Jog Upward (relative move) - MOVES NEGATIVE
            stepper.move(-jogStepSize);
            statusMessage = "Jogging UP (Negative) by " + String(jogStepSize) + " steps.";
            isMoving = false; // Pause oscillation loop
            break;

          case 'D': // Jog Downward (relative move) - MOVES POSITIVE
            stepper.move(jogStepSize);
            statusMessage = "Jogging DOWN (Positive) by " + String(jogStepSize) + " steps.";
            isMoving = false; // Pause oscillation loop
            break;

          case 'J': { // Set Jog Step Size
            if (input.length() > 1) {
              long newSize = input.substring(1).toInt();
              if (newSize > 0) {
                jogStepSize = newSize;
                statusMessage = "Jog Step Size set to: " + String(jogStepSize) + " steps.";
              } else {
                statusMessage = "Error: Invalid step size. Please use a positive integer.";
              }
            }
            break;
          }
          
          case 'H': // Help
            printHelp();
            statusMessage = "Help displayed.";
            break;

          default:
            statusMessage = "Error: Unknown command. Type 'H' for help.";
            break;
        }
    }
    
    // Print the consolidated status message and current state
    Serial.println("STATUS: " + statusMessage);
    printPositionInfo();
  }
}


void loop() {
  // 1. Handle Commands: Always check for serial input.
  handleSerialCommands();

  // 2. Motor Movement: Always call run() to execute any pending move (oscillation or jog).
  stepper.run(); 

  // 3. Oscillation Logic: Only check and set new targets if the loop is active ('G' command).
  if (isMoving) {
    
    // Check if the current move is complete (motor has reached its target)
    if (stepper.distanceToGo() == 0) {
      // Toggle the target position for continuous oscillation
      if (currentTarget == targetPositionDown) { // Currently at the Lower Limit (0)
        currentTarget = targetPositionUp; // Move to the Upper Limit (negative value)
        Serial.println("Reached DOWN limit (0). Moving UP.");
      } else { // Currently at the Upper Limit (negative value)
        currentTarget = targetPositionDown; // Move to the Lower Limit (0)
        Serial.println("Reached UP limit. Moving DOWN (to 0).");
      }
      
      // Set the new target
      stepper.moveTo(currentTarget);
    }
  }
}
