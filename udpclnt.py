import socket
import threading
import time
import os
import sys
from protocol import MAX_BYTES, build_header, MSG_INIT, MSG_DATA, HEART_BEAT

# --- CLIENT CONFIGURATION ---
DEFAULT_INTERVAL_DURATION = 60
DEFAULT_INTERVALS = [1, 5, 30]
MAX_BATCH = 10  # max readings per DATA packet
SERVER_ADDR = ('localhost', 12000)

client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
running = True

# --- HISTORY STORAGE ---
sent_history = {}  # key = (device_id, seq_num)

# --- DEVICE SEQUENCE TRACKING ---
device_seq = {}  # device_id -> next seq_num

# --- Load sensor file ---
if not os.path.exists("sensor_values.txt"):
    print("sensor_values.txt not found. Please create it like: device_id,sensor_type,value1,value2,...")
    sys.exit(1)

with open("sensor_values.txt") as f:
    lines = [line.strip() for line in f if line.strip()]

# Build active devices and initial sequence numbers
active_devices = set()
device_data = {}  # device_id -> list of (sensor_type, readings)
for line in lines:
    parts = [v.strip() for v in line.split(",") if v.strip()]
    if len(parts) < 3:
        continue
    device_id = int(parts[0])
    sensor_type = parts[1]
    readings = parts[2:]
    active_devices.add(device_id)
    device_seq.setdefault(device_id, 1)
    device_data.setdefault(device_id, []).append((sensor_type, readings))

print(f"Active devices: {active_devices}")

# --- Send INIT for each device/sensor ---
for device_id, sensors in device_data.items():
    for sensor_type, _ in sensors:
        seq = device_seq[device_id]
        init_header = build_header(device_id=device_id, batch_count=0, seq_num=seq, msg_type=MSG_INIT)
        payload = sensor_type.encode("utf-8")
        packet = init_header + payload
        client_socket.sendto(packet, SERVER_ADDR)
        sent_history[(device_id, seq)] = packet
        print(f"Sent INIT (Device={device_id}, Sensor={sensor_type}, seq={seq})")
        device_seq[device_id] += 1

# --- Heartbeat thread ---
def send_heartbeat():
    global running
    while running:
        time.sleep(10)
        for device_id in active_devices:
            seq = device_seq[device_id]
            header = build_header(device_id=device_id, batch_count=0, seq_num=seq, msg_type=HEART_BEAT)
            client_socket.sendto(header, SERVER_ADDR)
            sent_history[(device_id, seq)] = header
            print(f"Sent HEARTBEAT (Device={device_id}, seq={seq})")
            device_seq[device_id] += 1

# --- NACK receiver thread ---
def receive_nacks():
    global running
    client_socket.settimeout(1.0)
    while running:
        try:
            data, addr = client_socket.recvfrom(1024)
            msg = data.decode('utf-8')
            if msg.startswith("NACK:"):
                parts = msg.split(":")
                if len(parts) != 3:
                    continue
                nack_device_id = int(parts[1])
                missing_seqs = [int(s) for s in parts[2].split(",") if s.strip()]
                if nack_device_id not in active_devices:
                    continue
                print(f"\n[!] Received NACK for Device {nack_device_id}, seqs: {missing_seqs}")
                for miss_seq in missing_seqs:
                    key = (nack_device_id, miss_seq)
                    if key in sent_history:
                        client_socket.sendto(sent_history[key], SERVER_ADDR)
                        print(f"[>>] Retransmitting seq={miss_seq}")
                    else:
                        print(f"[x] Cannot retransmit seq={miss_seq} (not in history)")
        except socket.timeout:
            continue
        except Exception as e:
            if running:
                print(f"Error in NACK thread: {e}")

# Start threads
threading.Thread(target=send_heartbeat, daemon=True).start()
threading.Thread(target=receive_nacks, daemon=True).start()

# --- Parse command-line arguments ---
if len(sys.argv) > 1:
    try:
        interval_duration = int(sys.argv[1])
    except:
        interval_duration = DEFAULT_INTERVAL_DURATION
else:
    interval_duration = DEFAULT_INTERVAL_DURATION

if len(sys.argv) > 2:
    try:
        intervals = [int(x) for x in sys.argv[2].split(",")]
    except:
        intervals = DEFAULT_INTERVALS
else:
    intervals = DEFAULT_INTERVALS

# --- Send DATA packets ---
try:
    for interval in intervals:
        print(f"\n--- Running interval {interval}s for {interval_duration}s ---")
        start_time = time.time()
        while time.time() - start_time < interval_duration:
            for device_id, sensors in device_data.items():
                for sensor_type, readings in sensors:
                    batch = readings[:MAX_BATCH]
                    payload = ",".join(batch).encode("utf-8")
                    seq = device_seq[device_id]
                    header = build_header(device_id=device_id, batch_count=len(batch), seq_num=seq, msg_type=MSG_DATA)
                    packet = header + payload
                    client_socket.sendto(packet, SERVER_ADDR)
                    sent_history[(device_id, seq)] = packet
                    print(f"Sent DATA seq={seq}, Device={device_id}, Sensor={sensor_type}, readings={batch}")
                    device_seq[device_id] += 1
            time.sleep(interval)
except KeyboardInterrupt:
    print("Client interrupted.")

running = False
client_socket.close()
print("Client finished.")
