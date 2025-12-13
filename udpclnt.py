import socket
import threading
import time
import os
import sys
import struct
import random
from protocol import *

SERVER_PORT = 12001
DEFAULT_INTERVAL_DURATION = 20
DEFAULT_INTERVALS = [1, 5, 30]

if len(sys.argv) < 2:
    print("Usage: python udpclnt.py <device_id> [interval_duration] [intervals_csv]")
    sys.exit(1)

try:
    MY_DEVICE_ID = int(sys.argv[1])
except ValueError:
    print("device_id must be an integer")
    sys.exit(1)

if len(sys.argv) > 2:
    try:
        Interval_Duration = int(sys.argv[2])
    except ValueError:
        Interval_Duration = DEFAULT_INTERVAL_DURATION
else:
    Interval_Duration = DEFAULT_INTERVAL_DURATION

if len(sys.argv) > 3:
    try:
        intervals = [int(x) for x in sys.argv[3].split(",")]
    except ValueError:
        intervals = DEFAULT_INTERVALS
else:
    intervals = DEFAULT_INTERVALS

SERVER_ADDR = ('localhost', SERVER_PORT)
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

running = True
sensors = []    
sent_history = {} 
CONFIG_FILE = "device_config.txt"

def load_device_config(path):
    config = {}
    if not os.path.exists(path):
        return config
    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3: continue
            try:
                did = int(parts[0])
            except ValueError: continue
            unit = parts[1]
            batch_file = parts[2]
            config[did] = (unit, batch_file)
    return config

device_config = load_device_config(CONFIG_FILE)

def compress_data(values):
    compressed_values = []
    flag_batches = []
    for i, value in enumerate(values, start=1):
        int_value = value * 10**6
        int_value = int(int_value)
        if (int_value >= -2147483648) and (int_value <= 2147483647):
            compressed_values.append(int_value)
        else:
            compressed_values.append(value)
            flag_batches.append(i)
    return compressed_values, flag_batches

def send_heartbeat():
    global running
    while running:
        time.sleep(10)
        for sensor in sensors:
            header = build_checksum_header(device_id=sensor['device_id'], batch_count=0, seq_num=0, msg_type=HEART_BEAT)
            client_socket.sendto(header, SERVER_ADDR)
            print(f"Sent HEARTBEAT for Device {sensor['device_id']}")

def receive_nacks():
    global running
    try: client_socket.settimeout(1.0)
    except: pass 
    while running:
        try:
            data, addr = client_socket.recvfrom(1200) 
            header = parse_header(data)
            payload_bytes = data[HEADER_SIZE:]
            received_checksum = header['checksum']
            BASE_HEADER_SIZE = 9
            base_header_bytes = data[:BASE_HEADER_SIZE]
            calculated_checksum = calculate_expected_checksum(base_header_bytes, payload_bytes)

            if received_checksum != calculated_checksum:
                continue
                        
            payload_str = payload_bytes.decode('utf-8', errors='ignore').strip()

            if header['msg_type'] == NACK_MSG:
                parts = payload_str.split(":") 
                if len(parts) == 2:
                    try:
                        nack_device_id = int(parts[0])
                        missing_seq = int(parts[1])
                    except ValueError: continue
                else: continue

                print(f"\n [!] Received NACK for Device {nack_device_id}, seq: {missing_seq}")
                
                history_key = (nack_device_id, missing_seq)
                if history_key in sent_history:
                    packet = sent_history[history_key]
                    client_socket.sendto(packet, SERVER_ADDR)
                    print(f" [>>] Retransmitting DATA seq={missing_seq}")
                else:
                    print(f" [x] Cannot retransmit seq={missing_seq}")

                if missing_seq == 1 and sensors and sensors[0]["device_id"] == nack_device_id:
                    print(f" [^] Server requested re-INIT for Device {nack_device_id}.")
                    sensor = sensors[0]
                    init_header = build_checksum_header(
                        device_id=sensor["device_id"],
                        batch_count=sensor["unit_code"],
                        seq_num=1,
                        msg_type=MSG_INIT
                    )
                    client_socket.sendto(init_header, SERVER_ADDR)
                    sensor["seq_num"] = 2
                    sensor["stream_index"] = 0 # RESET STREAM INDEX
                    sent_history.clear()
                    sent_history[(sensor["device_id"], 1)] = init_header
                    print(f" [>>] Sent re-INIT (seq=1)")

        except socket.timeout: continue
        except OSError: 
            if not running: break
            time.sleep(0.1)
        except Exception as e:
            if running: print(f"Error in receiver thread: {e}")
            time.sleep(0.1)

threading.Thread(target=receive_nacks, daemon=True).start()

# --- NEW: LOAD ALL DATA INTO ONE BIG LIST ---
def load_all_data(batch_file):
    if not os.path.exists(batch_file):
        return []

    all_data = []
    with open(batch_file, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"): continue
            tokens = [t.strip() for t in line.split(",") if t.strip()]
            for t in tokens:
                try:
                    all_data.append(float(t))
                except:
                    pass
    return all_data

if MY_DEVICE_ID not in device_config:
    print(f"this id is not configured: {MY_DEVICE_ID}")
    running = False
else:
    unit, batch_filename = device_config[MY_DEVICE_ID]
    unit_code = unit_to_code(unit)
    # Load everything into a flat list
    full_data_stream = load_all_data(batch_filename)

    if not full_data_stream:
        print(f"No valid data found in {batch_filename} for device {MY_DEVICE_ID}")
        running = False

if running:
    sensors.clear()
    sensor = {
        "device_id": MY_DEVICE_ID,
        "unit": unit,
        "unit_code": unit_code,
        "data": full_data_stream, # The flat list
        "stream_index": 0,         # Points to current position in list
        "seq_num": 1
    }
    sensors.append(sensor)

    # Send INIT 
    init_packet = build_checksum_header(
        device_id=sensor["device_id"],
        batch_count=sensor["unit_code"],
        seq_num=sensor["seq_num"],
        msg_type=MSG_INIT
    )
    client_socket.sendto(init_packet, SERVER_ADDR)
    sent_history[(sensor["device_id"], sensor["seq_num"])] = init_packet
    print(f"Sent INIT (Device={sensor['device_id']}, seq={sensor['seq_num']})")
    sensor["seq_num"] += 1

    threading.Thread(target=send_heartbeat, daemon=True).start()

    print(f"Starting test for Device {MY_DEVICE_ID} with intervals {intervals} ({Interval_Duration}s each)...")

    for interval in intervals:
        print(f"\n--- Device {MY_DEVICE_ID}: Running {interval}s interval for {Interval_Duration} seconds ---")
        start_interval = time.time()

        while time.time() - start_interval < Interval_Duration:
            loop_start = time.time()

            # --- GRAB NEXT 10 NUMBERS ---
            chunk_size = 10
            current_idx = sensor["stream_index"]
            data_len = len(sensor["data"])
            
            chunk_values = []
            
            # Smart wrapping: if we hit the end, wrap around immediately to fill the packet
            for i in range(chunk_size):
                val = sensor["data"][(current_idx + i) % data_len]
                # Optional: Add noise so it doesn't look identical every loop
                # val += random.uniform(-0.1, 0.1) 
                chunk_values.append(val)
            
            # Update index for next time
            sensor["stream_index"] = (current_idx + chunk_size) % data_len

            # --- PREPARE PACKET ---
            compressed_data, flag_batches = compress_data(chunk_values)
            raw_payload = encode_smart_payload(compressed_data, flag_batches)
            payload = encrypt_bytes(raw_payload, sensor["device_id"], sensor["seq_num"])
            
            batch_count = len(chunk_values) 

            header = build_checksum_header(
                device_id=sensor["device_id"],
                batch_count=batch_count,
                seq_num=sensor["seq_num"],
                msg_type=MSG_DATA,
                payload=payload
            )

            packet = header + payload
            sent_history[(sensor["device_id"], sensor["seq_num"])] = packet

            client_socket.sendto(packet, SERVER_ADDR)
            print(f"Sent DATA (ID={sensor['device_id']}, seq={sensor['seq_num']}, count={batch_count})")
            
            sensor["seq_num"] += 1

            # Sleep to maintain interval
            elapsed = time.time() - loop_start
            if elapsed < interval:
                time.sleep(interval - elapsed)

print("Test finished. Closing client...")
running = False
client_socket.close()
print("Client finished.")