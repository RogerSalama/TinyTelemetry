import socket
import threading
import time
import os
import sys
<<<<<<< HEAD
from protocol import MAX_BYTES, HEADER_SIZE, build_header, MSG_INIT, MSG_DATA, HEART_BEAT

# --- CONFIG ---
DEFAULT_INTERVAL_DURATION = 60
DEFAULT_INTERVALS = [1, 5, 30]
MAX_BATCH = 10
SERVER_ADDR = ('localhost', 12000)
REPEAT_READINGS = True

MAX_PAYLOAD_BYTES = MAX_BYTES - HEADER_SIZE
if MAX_PAYLOAD_BYTES <= 0:
    raise RuntimeError("MAX_BYTES too small for header")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
running = True

sent_history = {}
sent_history_lock = threading.Lock()
device_seq = {}
device_seq_lock = threading.Lock()

sensor_entries = []

# --- Load sensor_values.txt ---
if not os.path.exists("sensor_values.txt"):
    print("sensor_values.txt not found. Create lines like: device_id,sensor_type,val1,val2,...")
    sys.exit(1)

with open("sensor_values.txt") as f:
    for raw in f:
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",") if p.strip() != ""]
        if len(parts) < 3:
            print("Skipping invalid line:", line)
            continue
        try:
            dev = int(parts[0])
        except ValueError:
            print("Skipping invalid device id line:", line)
            continue
        sensor_entries.append({
            "device_id": dev,
            "sensor_type": parts[1],
            "readings": parts[2:]
        })
        with device_seq_lock:
            device_seq.setdefault(dev, 1)

if not sensor_entries:
    print("No valid sensor entries loaded.")
    sys.exit(1)

print(f"Loaded {len(sensor_entries)} sensor entries (preserved order).")

# --- Send INIT messages ---
for entry in sensor_entries:
    dev = entry["device_id"]
    stype = entry["sensor_type"]
    with device_seq_lock:
        seq = device_seq[dev]
        device_seq[dev] += 1
    header = build_header(dev, batch_count=0, seq_num=seq, msg_type=MSG_INIT)
    sock.sendto(header + stype.encode("utf-8"), SERVER_ADDR)
    with sent_history_lock:
        sent_history[(dev, seq)] = header + stype.encode("utf-8")
    print(f"Sent INIT Device={dev} Sensor={stype} seq={seq}")

# --- Heartbeat thread ---
def heartbeat_thread():
    global running
    while running:
        time.sleep(10)
        seen = set()
        for entry in sensor_entries:
            dev = entry["device_id"]
            if dev in seen:
                continue
            seen.add(dev)
            with device_seq_lock:
                seq = device_seq[dev]
                device_seq[dev] += 1
            header = build_header(dev, batch_count=0, seq_num=seq, msg_type=HEART_BEAT)
            sock.sendto(header, SERVER_ADDR)
            with sent_history_lock:
                sent_history[(dev, seq)] = header
            print(f"Sent HEARTBEAT Device={dev} seq={seq}")

# --- NACK thread ---
def nack_thread():
    global running
    sock.settimeout(1.0)
    while running:
        try:
            data, addr = sock.recvfrom(4096)
=======
import struct

from protocol import (
    MAX_BYTES, HEADER_SIZE,
    build_checksum_header, parse_header,
    encrypt_bytes, unit_to_code,
    MSG_INIT, MSG_DATA, HEART_BEAT, NACK_MSG
)

# --- CONFIG ---
SERVER_ADDR = ('localhost', 12002)   # change host if server runs on another machine
DEFAULT_INTERVAL_DURATION = 20
DEFAULT_INTERVALS = [1, 5, 30]
# -------------

# Parse CLI args: udpclnt.py [duration] [comma_intervals]
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

client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
running = True

# (device_id, seq) -> full packet bytes (header+payload)
sent_history: dict[tuple[int, int], bytes] = {}

# Each sensor: {device_id, unit, unit_code, readings, current_index, seq_num}
sensors = []

def send_heartbeat():
    """Send heartbeat per device every 10 seconds, incrementing seq."""
    global running
    while running:
        time.sleep(10)
        for sensor in sensors:
            hb_hdr = build_checksum_header(
                device_id=sensor['device_id'],
                batch_count=0,
                seq_num=sensor['seq_num'],
                msg_type=HEART_BEAT,
                payload=b'',
            )
            client_socket.sendto(hb_hdr, SERVER_ADDR)
            print(f"Sent HEARTBEAT for Device {sensor['device_id']} (seq={sensor['seq_num']})")
            sensor['seq_num'] += 1

def receive_nacks():
    """Listen for plain-text NACKs from server and retransmit."""
    global running
    try:
        client_socket.settimeout(1.0)
    except Exception:
        pass

    while running:
        try:
            data, addr = client_socket.recvfrom(1200)

            # Server sends NACK as text: "NACK:<device_id>:<seq1>,<seq2>,..."
            if data.startswith(b"NACK:"):
                msg = data.decode('utf-8', 'ignore')
                try:
                    _, dev_str, seqs_str = msg.split(":", 2)
                    nack_device_id = int(dev_str)
                    missing_seqs = [int(s) for s in seqs_str.split(",") if s.strip()]
                except Exception:
                    print(f" [x] Malformed NACK: {msg}")
                    continue

                print(f"\n [!] Received NACK for Device {nack_device_id}, seqs: {missing_seqs}")
                for miss_seq in missing_seqs:
                    pkt = sent_history.get((nack_device_id, miss_seq))
                    if pkt:
                        client_socket.sendto(pkt, SERVER_ADDR)
                        print(f" [>>] Retransmitting seq={miss_seq}")
                    else:
                        # If INIT (seq=1) missing, rebuild + resend
                        if miss_seq == 1:
                            for sensor in sensors:
                                if sensor['device_id'] == nack_device_id:
                                    init_hdr = build_checksum_header(
                                        device_id=sensor['device_id'],
                                        batch_count=sensor['unit_code'],
                                        seq_num=1,
                                        msg_type=MSG_INIT,
                                        payload=b'',
                                    )
                                    client_socket.sendto(init_hdr, SERVER_ADDR)
                                    print(f" [^] Sent re-INIT for device {nack_device_id}")
                                    break
                        else:
                            print(f" [x] No packet in history for seq={miss_seq}")
                continue

            # (Optional) Handle other server messages with headers, if any
            # header = parse_header(data)
            # ...

>>>>>>> e8a682a (fixing errors)
        except socket.timeout:
            continue
        except OSError:
            if not running:
                break
        except Exception as e:
            if running:
<<<<<<< HEAD
                print("Recv error:", e)
            continue

        try:
            text = data.decode('utf-8')
        except:
            continue
        if not text.startswith("NACK:"):
            continue
        parts = text.split(":")
        if len(parts) != 3:
            print("Malformed NACK:", text)
            continue
        try:
            nack_dev = int(parts[1])
            missing = [int(s) for s in parts[2].split(",") if s.strip()]
        except ValueError:
            print("Malformed NACK numbers:", text)
            continue

        print(f"[!] Received NACK for device {nack_dev}, seqs={missing}")
        for seq in missing:
            key = (nack_dev, seq)
            with sent_history_lock:
                packet = sent_history.get(key)
            if packet:
                sock.sendto(packet, SERVER_ADDR)
                print(f"[>>] Retransmitted (dev={nack_dev}, seq={seq})")
            else:
                print(f"[x] No history for (dev={nack_dev}, seq={seq})")

# --- Batch helper ---
def make_batch_bytes(readings):
    batch = []
    total_bytes = 0
    for r in readings:
        predicted = (len(r) if not batch else 1 + len(r))
        if len(batch) >= MAX_BATCH:
            break
        if total_bytes + predicted > MAX_PAYLOAD_BYTES:
            break
        batch.append(r)
        total_bytes += predicted
    return batch, len(batch)

# --- Start threads ---
threading.Thread(target=heartbeat_thread, daemon=True).start()
threading.Thread(target=nack_thread, daemon=True).start()

# --- Parse CLI args ---
interval_duration = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INTERVAL_DURATION
intervals = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else DEFAULT_INTERVALS

# --- Send DATA ---
try:
    finished_early = False
    for interval in intervals:
        print(f"\n--- Running interval={interval}s for {interval_duration}s ---")
        start = time.time()
        while time.time() - start < interval_duration:
            any_left = False
            for entry in sensor_entries:
                dev = entry["device_id"]
                stype = entry["sensor_type"]
                readings = entry["readings"]
                if not readings:
                    continue
                any_left = True

                batch, cnt = make_batch_bytes(readings)
                if cnt == 0:
                    print(f"[!] single reading too large, dropping: {readings[0]}")
                    if REPEAT_READINGS:
                        entry["readings"] = readings[1:] + [readings[0]]
                    else:
                        entry["readings"] = readings[1:]
                    continue

                payload = ",".join(batch).encode("utf-8")
                with device_seq_lock:
                    seq = device_seq[dev]
                    device_seq[dev] += 1
                header = build_header(dev, cnt, seq, MSG_DATA)
                sock.sendto(header + payload, SERVER_ADDR)
                with sent_history_lock:
                    sent_history[(dev, seq)] = header + payload

                print(f"Sent DATA seq={seq} dev={dev} sensor={stype} count={cnt} readings={batch}")

                if REPEAT_READINGS:
                    entry["readings"] = readings[cnt:] + batch
                else:
                    entry["readings"] = readings[cnt:]

            if not any_left:
                print("All readings exhausted — finishing early.")
                finished_early = True
                break

            time.sleep(interval)

        if finished_early:
            break

except KeyboardInterrupt:
    print("Interrupted by user.")

# --- Send optional FIN ---
try:
    fin_payload = b"__FIN__"
    fin_header = build_header(0, 0, 0, MSG_INIT)
    sock.sendto(fin_header + fin_payload, SERVER_ADDR)
    print("Sent FIN marker.")
except Exception as e:
    print("Failed to send FIN:", e)
=======
                print(f"Error in receiver thread: {e}")
            time.sleep(0.1)

# ---- Load sensors from file (robust parsing) ----
if not os.path.exists("sensor_values.txt"):
    print("sensor_values.txt not found. Please create it with comma-separated values.")
    running = False
else:
    with open("sensor_values.txt", "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        print("sensor_values.txt is empty.")
        running = False
    else:
        for line in lines:
            parts = [v.strip() for v in line.split(",") if v.strip()]

            # defaults
            device_id = 1
            unit = "unknown"
            readings = []

            try:
                device_id = int(parts[0])           # first token = device_id
                if len(parts) >= 2:
                    unit = parts[1]                 # optional unit string
                if len(parts) >= 3:
                    readings = parts[2:]
                else:
                    readings = parts[1:]            # if no unit, everything after id is reading
                    unit = "unknown"
            except (ValueError, IndexError):
                # If the first token isn't int, treat whole line as readings for default device
                readings = parts

            device_id = max(0, min(15, device_id))  # 4-bit device_id

            sensors.append({
                'device_id': device_id,
                'unit': unit,
                'unit_code': unit_to_code(unit),
                'readings': readings,
                'current_index': 0,
                'seq_num': 1
            })

# Start background NACK thread
threading.Thread(target=receive_nacks, daemon=True).start()

if running:
    # Send INIT for all sensors
    for sensor in sensors:
        init_hdr = build_checksum_header(
            device_id=sensor['device_id'],
            batch_count=sensor['unit_code'],
            seq_num=sensor['seq_num'],
            msg_type=MSG_INIT,
            payload=b'',
        )
        client_socket.sendto(init_hdr, SERVER_ADDR)
        sent_history[(sensor['device_id'], sensor['seq_num'])] = init_hdr  # store for possible NACK
        print(f"Sent INIT (device={sensor['device_id']}, seq={sensor['seq_num']}, unit={sensor['unit']})")
        sensor['seq_num'] += 1
        time.sleep(0.5)

    # Start heartbeat thread
    threading.Thread(target=send_heartbeat, daemon=True).start()

    print(f"Starting test for intervals {intervals} ({Interval_Duration}s each)...")
    for interval in intervals:
        print(f"\n--- Running {interval}s interval for {Interval_Duration} seconds ---")

        # Reset per-interval read index
        for sensor in sensors:
            sensor['current_index'] = 0

        start_interval = time.time()
        while time.time() - start_interval < Interval_Duration:
            loop_start = time.time()

            for sensor in sensors:
                start = sensor['current_index']
                if start < len(sensor['readings']):
                    chunk = sensor['readings'][start:start+10]
                    batch_count = len(chunk)

                    # Convert tokens to doubles
                    values = []
                    for token in chunk:
                        try:
                            values.append(float(token))
                        except Exception:
                            values.append(0.0)

                    fmt = '!' + ('d' * len(values))
                    payload = struct.pack(fmt, *values)

                    # Encrypt binary payload (XOR stream), keeps 8-byte alignment
                    enc_payload = encrypt_bytes(payload, sensor['device_id'], sensor['seq_num'])

                    # 10B header with checksum over base+payload
                    header = build_checksum_header(
                        device_id=sensor['device_id'],
                        batch_count=batch_count,
                        seq_num=sensor['seq_num'],
                        msg_type=MSG_DATA,
                        payload=enc_payload
                    )

                    packet = header + enc_payload
                    sent_history[(sensor['device_id'], sensor['seq_num'])] = packet

                    client_socket.sendto(packet, SERVER_ADDR)
                    print(f"Sent DATA seq={sensor['seq_num']}, device={sensor['device_id']}, interval={interval}s, readings={chunk}")

                    sensor['seq_num'] += 1
                    sensor['current_index'] += 10
                    time.sleep(0.1)  # small pacing

            # sleep until next tick of 'interval'
            elapsed = time.time() - loop_start
            if elapsed < interval:
                time.sleep(interval - elapsed)
>>>>>>> e8a682a (fixing errors)

running = False
<<<<<<< HEAD
time.sleep(0.2)
sock.close()
=======
client_socket.close()
>>>>>>> e8a682a (fixing errors)
print("Client finished.")
