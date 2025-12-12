import socket
import threading
import time
import os
import sys
import struct
from protocol import *
# cmd parsing one device per cmd run ---------------
DEFAULT_INTERVAL_DURATION = 20
DEFAULT_INTERVALS = [1, 5, 30]

if len(sys.argv) < 2:
    print("Usage: python udpclnt.py <device_id> [interval_duration] [intervals_csv]")
    print("Example: python udpclnt.py 3 60 1,30")
    sys.exit(1)

try:
    MY_DEVICE_ID = int(sys.argv[1])
except ValueError:
    print("device_id must be an integer")
    sys.exit(1)

# DeviceID is 4 bits -> valid 0..15
if MY_DEVICE_ID < 0 or MY_DEVICE_ID > 15:
    print("device_id must be between 0 and 15")
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
# -----------------------------
SERVER_ADDR = ('localhost', 12002)
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

running = True
sensors = []   # list of active sensors 
# HISTORY STORAGE (Key is now (device_id, seq_num) tuple)
sent_history = {} 

# Send INIT message once
# header = build_header(device_id=MY_DEVICE_ID, batch_count=0, seq_num=seq_num, msg_type=MSG_INIT)
# client_socket.sendto(header, SERVER_ADDR)
# sent_history[(MY_DEVICE_ID, seq_num)] = header # Store with device ID
# print(f"Sent INIT (Device={MY_DEVICE_ID}, seq={seq_num})")
# seq_num += 1

def send_heartbeat():
    """Send heartbeat messages every 1 second from all devices."""
    global running
    while running:
        time.sleep(10)
        # time.sleep(1)
        #sending heartbeat message for all sensors in text file
        for sensor in sensors:
            header = build_checksum_header(device_id=sensor['device_id'], batch_count=0, seq_num=0, msg_type=HEART_BEAT)
            client_socket.sendto(header, SERVER_ADDR)
            print(f"Sent HEARTBEAT for Device {sensor['device_id']} (seq={sensor['seq_num']})")
            # sensor['seq_num'] += 1
        # header = build_header(device_id=MY_DEVICE_ID, batch_count=0, seq_num=seq_num, msg_type=HEART_BEAT)
        # client_socket.sendto(header, SERVER_ADDR)
        # sent_history[(MY_DEVICE_ID, seq_num)] = header # Store with device ID
        # print(f"Sent HEARTBEAT (Device={MY_DEVICE_ID}, seq={seq_num})")
        # seq_num += 1

def receive_nacks():
    """Thread to listen for NACK messages from server."""
    global running
    # Optional: set a short timeout so recvfrom can exit periodically
    try:
        client_socket.settimeout(1.0)
    except Exception:
        pass  # ignore if not supported
    # client_socket.settimeout(1.0) # non-blocking with timeout
    while running:
        try:
            # Data received is a bytes object
            data, addr = client_socket.recvfrom(1200) 
            
            # Parse the header (which requires bytes)
            header = parse_header(data)
            
            # The payload is the part of the bytes object after the header
            payload_bytes = data[HEADER_SIZE:]

            received_checksum = header['checksum']


            BASE_HEADER_SIZE = 9
            base_header_bytes = data[:BASE_HEADER_SIZE]
            calculated_checksum = calculate_expected_checksum(base_header_bytes,payload_bytes)

            checksum_valid = (received_checksum == calculated_checksum)

            if not checksum_valid:
                print(f" Checksum mismatch: received={received_checksum}, calculated={calculated_checksum}")
                continue
                        
            payload_str = payload_bytes.decode('utf-8', errors='ignore').strip()

            if header['msg_type'] == NACK_MSG:
                # Server sends one NACK packet per missing sequence in the format: "DEVICE_ID:MISSING_SEQ"
                parts = payload_str.split(":") 
                
                if len(parts) == 2:
                    try:
                        # 1. FIX: Convert both parts to integers.
                        nack_device_id = int(parts[0])
                        missing_seq = int(parts[1]) # Server sends a single sequence number
                    except ValueError:
                        print(f" [x] Failed to parse NACK payload into integers: {payload_str}")
                        continue
                else:
                    # The original check here was too broad. Now we know exactly why it's malformed.
                    print(f" [x] Received malformed NACK payload format: {payload_str}")
                    continue

                print(f"\n [!] Received NACK for Device {nack_device_id}, seq: {missing_seq}")
                
                # 2. FIX: Use the single integer sequence number to look up the packet
                history_key = (nack_device_id, missing_seq)
                if history_key in sent_history:
                    packet = sent_history[history_key]
                    client_socket.sendto(packet, SERVER_ADDR)
                    print(f" [>>] Retransmitting DATA seq={missing_seq}")
                else:
                    print(f" [x] Cannot retransmit seq={missing_seq} (not in history or INIT/HEARTBEAT)")

                # 3. FIX: Handle re-INIT NACK (seq=1) - logic cleaned up
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
                    # reset local state
                    sensor["seq_num"] = 2
                    sensor["batch_idx"] = 0
                    sent_history.clear()
                    sent_history[(sensor["device_id"], 1)] = init_header
                    print(f" [>>] Sent re-INIT (seq=1, unit={sensor['unit']})")


                        
        except socket.timeout:
            continue
        except OSError as e:
            # Handle Windows-specific socket closure errors (WinError 10022)
            if not running:
                break
            print(f"Receiver thread OSError: {e}")
            time.sleep(0.1)
            continue
        except Exception as e:
            if running:
                print(f"Error in receiver thread: {e}")
            time.sleep(0.1)

# Start background threads
# threading.Thread(target=send_heartbeat, daemon=True).start()
threading.Thread(target=receive_nacks, daemon=True).start()
#------------------------------------------------------
# BATCH-FILE LOADING ----------------
CONFIG_FILE = "device_config.txt"

def load_device_config(path):
    """
    Format (CSV):
      device_id,unit,batch_file
    Example:
      3,kelvin,device_3.txt
    """
    config = {}
    if not os.path.exists(path):
        return config

    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                did = int(parts[0])
            except ValueError:
                continue
            unit = parts[1]
            batch_file = parts[2]
            config[did] = (unit, batch_file)
    return config

def load_batches(batch_file):
    """
    Each line is ONE batch.
    Each line must have 1..10 numbers separated by commas.
    """
    if not os.path.exists(batch_file):
        return []

    batches = []
    with open(batch_file, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tokens = [t.strip() for t in line.split(",") if t.strip()]
            if len(tokens) == 0:
                continue
            if len(tokens) > 10:
                # keep protocol safe: max 10 readings
                tokens = tokens[:10]
            # keep as strings for printing; we convert to float on send
            batches.append(tokens)
    return batches

device_config = load_device_config(CONFIG_FILE)

if MY_DEVICE_ID not in device_config:
    print(f"this id is not configured: {MY_DEVICE_ID}")
    running = False
else:
    unit, batch_filename = device_config[MY_DEVICE_ID]
    unit_code = unit_to_code(unit)
    batches = load_batches(batch_filename)

    if not batches:
        print(f"No batches found in {batch_filename} for device {MY_DEVICE_ID}")
        running = False


if running:
    sensors.clear()
    sensor = {
        "device_id": MY_DEVICE_ID,
        "unit": unit,
        "unit_code": unit_code,
        "batches": batches,
        "batch_idx": 0,
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
    print(f"Sent INIT (Device={sensor['device_id']}, seq={sensor['seq_num']}, unit={sensor['unit']})")
    sensor["seq_num"] += 1

    # Start heartbeat thread 
    threading.Thread(target=send_heartbeat, daemon=True).start()

    print(f"Starting test for Device {MY_DEVICE_ID} with intervals {intervals} ({Interval_Duration}s each)...")

    for interval in intervals:
        print(f"\n--- Device {MY_DEVICE_ID}: Running {interval}s interval for {Interval_Duration} seconds ---")
        start_interval = time.time()

        while time.time() - start_interval < Interval_Duration:
            loop_start = time.time()

            # Choose ONE line (ONE batch) per DATA send
            batch = sensor["batches"][sensor["batch_idx"]]
            sensor["batch_idx"] = (sensor["batch_idx"] + 1) % len(sensor["batches"])

            # Convert to floats
            values = []
            for token in batch:
                try:
                    values.append(float(token))
                except Exception:
                    values.append(0.0)

            batch_count = len(values)  # 1..10
            fmt = "!" + ("d" * batch_count)
            payload = struct.pack(fmt, *values)

            # Encrypt the binary payload (XOR stream) so packed
            # doubles remain 8-byte aligned. Uses device_id & seq
            payload = encrypt_bytes(payload, sensor["device_id"], sensor["seq_num"])

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
            print(f"Sent DATA (Device={sensor['device_id']}, seq={sensor['seq_num']}, interval={interval}s, batch={batch})")
            sensor["seq_num"] += 1

            # Sleep to maintain interval
            elapsed = time.time() - loop_start
            if elapsed < interval:
                time.sleep(interval - elapsed)

print("Test finished. Closing client...")
running = False
client_socket.close()
print("Client finished.")