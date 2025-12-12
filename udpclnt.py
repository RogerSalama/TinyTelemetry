
import socket
import threading
import time
import os
import sys
import struct
from protocol import *   # Make sure protocol.py defines HEADER_SIZE, build_checksum_header, parse_header, encrypt_bytes, calculate_expected_checksum, etc.

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
        intervals = [int(x) for x in sys.argv[3].split(",") if x.strip()]
        if not intervals:
            intervals = DEFAULT_INTERVALS
    except Exception:
        intervals = DEFAULT_INTERVALS
else:
    intervals = DEFAULT_INTERVALS
# -----------------------------
SERVER_ADDR = ('localhost', 12002)
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

running = True

# HISTORY STORAGE (Key is now (device_id, seq_num) tuple)
sent_history = {}

# IMPORTANT FIX: define sensors BEFORE any use
sensors = []    # <<--- added to prevent NameError when using sensors.clear() or iterating

def send_heartbeat():
    """Send heartbeat messages every heartbeat_period seconds for all sensors."""
    global running
    heartbeat_period = 10  # seconds (adjust if you want 1s)
    while running:
        time.sleep(heartbeat_period)
        # sending heartbeat message for all sensors in text file
        for sensor in list(sensors):
            try:
                header = build_checksum_header(device_id=sensor['device_id'], batch_count=0, seq_num=0, msg_type=HEART_BEAT)
                client_socket.sendto(header, SERVER_ADDR)
                print(f"Sent HEARTBEAT for Device {sensor['device_id']} (seq={sensor['seq_num']})")
            except Exception as e:
                print(f"Heartbeat send error for Device {sensor.get('device_id')}: {e}")

def receive_nacks():
    """Thread to listen for NACK messages from server."""
    global running
    try:
        client_socket.settimeout(1.0)
    except Exception:
        pass
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
                print(f" Checksum mismatch: received={received_checksum}, calculated={calculated_checksum}")
                continue

            payload_str = payload_bytes.decode('utf-8', errors='ignore').strip()

            if header['msg_type'] == NACK_MSG:
                parts = payload_str.split(":")
                if len(parts) == 2:
                    try:
                        nack_device_id = int(parts[0])
                        missing_seq = int(parts[1])
                    except ValueError:
                        print(f" [x] Failed to parse NACK payload into integers: {payload_str}")
                        continue
                else:
                    print(f" [x] Received malformed NACK payload format: {payload_str}")
                    continue

                print(f"\n [!] Received NACK for Device {nack_device_id}, seq: {missing_seq}")

                history_key = (nack_device_id, missing_seq)
                if history_key in sent_history:
                    packet = sent_history[history_key]
                    client_socket.sendto(packet, SERVER_ADDR)
                    print(f" [>>] Retransmitting DATA seq={missing_seq}")
                else:
                    print(f" [x] Cannot retransmit seq={missing_seq} (not in history or INIT/HEARTBEAT)")

                # Handle re-INIT NACK (seq=1)
                if missing_seq == 1:
                    for sensor in sensors:
                        if sensor["device_id"] == nack_device_id:
                            print(f" [^] Server requested re-INIT for Device {nack_device_id}.")
                            init_header = build_checksum_header(
                                device_id=sensor["device_id"],
                                batch_count=sensor["unit_code"],
                                seq_num=1,
                                msg_type=MSG_INIT
                            )
                            client_socket.sendto(init_header, SERVER_ADDR)
                            # reset local state for that sensor
                            sensor["seq_num"] = 2
                            sensor["batch_idx"] = 0
                            # only clear history for this device (safe for multi-device)
                            keys_to_delete = [k for k in sent_history.keys() if k[0] == sensor["device_id"]]
                            for k in keys_to_delete:
                                del sent_history[k]
                            sent_history[(sensor["device_id"], 1)] = init_header
                            print(f" [>>] Sent re-INIT (seq=1, unit={sensor['unit']})")
                            break

        except socket.timeout:
            continue
        except OSError as e:
            if not running:
                break
            print(f"Receiver thread OSError: {e}")
            time.sleep(0.1)
            continue
        except Exception as e:
            if running:
                print(f"Error in receiver thread: {e}")
            time.sleep(0.1)

# Start NACK receiver thread early (it doesn't rely on sensors list being populated)
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
                tokens = tokens[:10]
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
    # safe: sensors was defined earlier, so .clear() won't raise
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

    # Now start heartbeat thread (sensors list exists)
    threading.Thread(target=send_heartbeat, daemon=True).start()

    print(f"Starting test for Device {MY_DEVICE_ID} with intervals {intervals} ({Interval_Duration}s each)...")

    try:
        for interval in intervals:
            print(f"\n--- Device {MY_DEVICE_ID}: Running {interval}s interval for {Interval_Duration} seconds ---")
            start_interval = time.time()

            while time.time() - start_interval < Interval_Duration and running:
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
    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        print("Test finished. Closing client...")
        running = False
        client_socket.close()
        print("Client finished.")
