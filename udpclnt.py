import socket
import threading
import time
import os
import sys
from protocol import MAX_BYTES, build_header, MSG_INIT, MSG_DATA, HEART_BEAT

# --- CLIENT CONFIGURATION ---
MY_DEVICE_ID = 1  # The ID this client uses when sending packets
# --- END CONFIGURATION ---

# Configurable defaults
DEFAULT_INTERVAL_DURATION = 60
DEFAULT_INTERVALS = [1, 5, 30]

# Parse command-line arguments
if len(sys.argv) > 1:
    try:
        Interval_Duration = int(sys.argv[1])
    except ValueError:
        Interval_Duration = DEFAULT_INTERVAL_DURATION
else:
    Interval_Duration = DEFAULT_INTERVAL_DURATION

if len(sys.argv) > 2:
    try:
        intervals = [int(x) for x in sys.argv[2].split(",")]
    except ValueError:
        intervals = DEFAULT_INTERVALS
else:
    intervals = DEFAULT_INTERVALS

SERVER_ADDR = ('localhost', 12000)
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

seq_num = 1
running = True

# HISTORY STORAGE (Key is now (device_id, seq_num) tuple)
sent_history = {} 

# Send INIT message once
header = build_header(device_id=MY_DEVICE_ID, batch_count=0, seq_num=seq_num, msg_type=MSG_INIT)
client_socket.sendto(header, SERVER_ADDR)
sent_history[(MY_DEVICE_ID, seq_num)] = header # Store with device ID
print(f"Sent INIT (Device={MY_DEVICE_ID}, seq={seq_num})")
seq_num += 1

def send_heartbeat():
    """Send heartbeat messages every 10 seconds."""
    global seq_num, running
    while running:
        time.sleep(10)
        header = build_header(device_id=MY_DEVICE_ID, batch_count=0, seq_num=seq_num, msg_type=HEART_BEAT)
        client_socket.sendto(header, SERVER_ADDR)
        sent_history[(MY_DEVICE_ID, seq_num)] = header # Store with device ID
        print(f"Sent HEARTBEAT (Device={MY_DEVICE_ID}, seq={seq_num})")
        seq_num += 1

def receive_nacks():
    """Thread to listen for NACK messages from server."""
    global running
    client_socket.settimeout(1.0) # non-blocking with timeout
    while running:
        try:
            data, addr = client_socket.recvfrom(1024)
            msg = data.decode('utf-8')
            
            # Expected format: "NACK:<device_id>:<seq1>,<seq2>,..."
            if msg.startswith("NACK:"):
                parts = msg.split(":")
                if len(parts) == 3:
                    try:
                        nack_device_id = int(parts[1])
                        missing_str = parts[2]
                        missing_seqs = [int(s) for s in missing_str.split(",") if s.strip()]
                    except ValueError:
                        print(f" [x] Failed to parse NACK message: {msg}")
                        continue
                else:
                    print(f" [x] Received malformed NACK message: {msg}")
                    continue

                if nack_device_id != MY_DEVICE_ID:
                    print(f" [x] Received NACK for device ID {nack_device_id}, ignoring (My ID is {MY_DEVICE_ID}).")
                    continue
                
                print(f"\n [!] Received NACK for Device {nack_device_id}, seqs: {missing_seqs}")
                
                for miss_seq in missing_seqs:
                    history_key = (nack_device_id, miss_seq)
                    if history_key in sent_history:
                        packet = sent_history[history_key]
                        client_socket.sendto(packet, SERVER_ADDR)
                        print(f" [>>] Retransmitting seq={miss_seq}")
                    else:
                        print(f" [x] Cannot retransmit seq={miss_seq} (not in history)")
                        
        except socket.timeout:
            continue
        except Exception as e:
            if running:
                print(f"Error in receiver thread: {e}")

# Start background threads
threading.Thread(target=send_heartbeat, daemon=True).start()
threading.Thread(target=receive_nacks, daemon=True).start()

# Load sensor data
if not os.path.exists("sensor_values.txt"):
    print("sensor_values.txt not found. Please create it with comma-separated values.")
    running = False
else:
    with open("sensor_values.txt") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        print("sensor_values.txt is empty.")
        running = False
    else:
        print(f"Starting test for intervals {intervals} ({Interval_Duration}s each)...")

        for interval in intervals:
            print(f"\n--- Running {interval}s interval for {Interval_Duration} seconds ---")
            start_interval = time.time()
            while time.time() - start_interval < Interval_Duration:
                line = lines[(seq_num - 1) % len(lines)]
                values = [v.strip() for v in line.split(",") if v.strip()]
                batch_count = len(values)
                payload = ",".join(values).encode("utf-8")

                header = build_header(device_id=MY_DEVICE_ID, batch_count=batch_count,
                                      seq_num=seq_num, msg_type=MSG_DATA)
                packet = header + payload

                # STORE IN HISTORY using (device_id, seq_num) key
                sent_history[(MY_DEVICE_ID, seq_num)] = packet
                
                client_socket.sendto(packet, SERVER_ADDR)
                print(f"Sent DATA seq={seq_num}, interval={interval}s, readings={values}")
                seq_num += 1
                time.sleep(interval)

print("Test finished. Closing client...")
running = False
client_socket.close()
print("Client finished.")