# udpclnt.py (updated)
import socket
import threading
import time
import os
import sys
from protocol import MAX_BYTES, HEADER_SIZE, build_header, MSG_INIT, MSG_DATA, HEART_BEAT

# --- CONFIG ---
DEFAULT_INTERVAL_DURATION = 60
DEFAULT_INTERVALS = [1, 5, 30]
MAX_BATCH = 10                # hard cap on number of readings per packet
SERVER_ADDR = ('localhost', 12000)
REPEAT_READINGS = True       # if True: cycle readings instead of exhausting them

# compute max payload bytes allowed to avoid truncation
MAX_PAYLOAD_BYTES = MAX_BYTES - HEADER_SIZE
if MAX_PAYLOAD_BYTES <= 0:
    raise RuntimeError("MAX_BYTES too small for header")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
running = True

# thread-safe containers
sent_history = {}             # (device_id, seq) -> bytes
sent_history_lock = threading.Lock()
device_seq = {}               # device_id -> next seq
device_seq_lock = threading.Lock()

# preserve file order: list of entries exactly as file lines appear
# each entry: dict { device_id:int, sensor_type:str, readings: list[str] }
sensor_entries = []

# load file
if not os.path.exists("sensor_values.txt"):
    print("sensor_values.txt not found. create lines like: device_id,sensor_type,val1,val2,...")
    sys.exit(1)

with open("sensor_values.txt") as f:
    for raw in f:
        line = raw.strip()
        if not line:
            continue
        # split, strip, **filter out empty tokens**
        parts = [p.strip() for p in line.split(",")]
        parts = [p for p in parts if p != ""]  # remove empty tokens caused by trailing commas
        if len(parts) < 3:
            print("Skipping invalid line (needs at least device_id,sensor_type,value):", line)
            continue
        try:
            dev = int(parts[0])
        except ValueError:
            print("Skipping invalid device id line:", line)
            continue
        sensor_entries.append({
            "device_id": dev,
            "sensor_type": parts[1],
            "readings": parts[2:]  # list of strings
        })
        # init seq if not present
        with device_seq_lock:
            device_seq.setdefault(dev, 1)

if not sensor_entries:
    print("No valid sensor entries loaded.")
    sys.exit(1)

print(f"Loaded {len(sensor_entries)} sensor entries (preserved order).")

# send INIT messages in file order (one INIT per entry)
for entry in sensor_entries:
    dev = entry["device_id"]
    stype = entry["sensor_type"]
    with device_seq_lock:
        seq = device_seq[dev]
        device_seq[dev] += 1
    header = build_header(device_id=dev, batch_count=0, seq_num=seq, msg_type=MSG_INIT)
    packet = header + stype.encode("utf-8")
    sock.sendto(packet, SERVER_ADDR)
    with sent_history_lock:
        sent_history[(dev, seq)] = packet
    print(f"Sent INIT Device={dev} Sensor={stype} seq={seq}")

# heartbeat thread
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
            header = build_header(device_id=dev, batch_count=0, seq_num=seq, msg_type=HEART_BEAT)
            sock.sendto(header, SERVER_ADDR)
            with sent_history_lock:
                sent_history[(dev, seq)] = header
            print(f"Sent HEARTBEAT Device={dev} seq={seq}")

# NACK receiver
def nack_thread():
    global running
    sock.settimeout(1.0)
    while running:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except Exception as e:
            if running:
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

# helper: split readings into a batch that respects both MAX_BATCH and MAX_PAYLOAD_BYTES
def make_batch_bytes(readings):
    """Return (batch_list, count) where batch_list contains up to MAX_BATCH readings
       and joining them with commas produces <= MAX_PAYLOAD_BYTES bytes."""
    if not readings:
        return [], 0
    batch = []
    total_bytes = 0
    for r in readings:
        # predicted size: comma + len(r) (no leading comma for first)
        predicted = (len(r) if not batch else (1 + len(r)))
        if len(batch) >= MAX_BATCH:
            break
        if total_bytes + predicted > MAX_PAYLOAD_BYTES:
            break
        batch.append(r)
        total_bytes += predicted
    return batch, len(batch)

# start threads
threading.Thread(target=heartbeat_thread, daemon=True).start()
threading.Thread(target=nack_thread, daemon=True).start()

# parse CLI args
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
    finished_early = False
    for interval in intervals:
        print(f"\n--- Running interval={interval}s for {interval_duration}s ---")
        start = time.time()
        while time.time() - start < interval_duration:
            any_left = False
            # iterate in file order (preserved)
            for entry in sensor_entries:
                dev = entry["device_id"]
                stype = entry["sensor_type"]
                readings = entry["readings"]
                if not readings:
                    continue
                any_left = True

                # pick batch that fits both count and bytes limit
                batch, cnt = make_batch_bytes(readings)
                if cnt == 0:
                    # a single reading is too large for payload limit; skip and drop it
                    print(f"[!] single reading too large for payload limit, dropping: {readings[0]}")
                    if REPEAT_READINGS:
                        # rotate and continue
                        entry["readings"] = readings[1:] + [readings[0]]
                    else:
                        entry["readings"] = readings[1:]
                    continue

                payload = ",".join(batch).encode("utf-8")
                with device_seq_lock:
                    seq = device_seq[dev]
                    device_seq[dev] += 1

                header = build_header(device_id=dev, batch_count=cnt, seq_num=seq, msg_type=MSG_DATA)
                packet = header + payload
                sock.sendto(packet, SERVER_ADDR)
                with sent_history_lock:
                    sent_history[(dev, seq)] = packet

                print(f"Sent DATA seq={seq} dev={dev} sensor={stype} count={cnt} readings={batch}")

                # remove or rotate the sent readings
                if REPEAT_READINGS:
                    # rotate: move sent chunk to the back (keeps cycling)
                    entry["readings"] = readings[cnt:] + batch
                else:
                    # exhaust: remove sent chunk
                    entry["readings"] = readings[cnt:]

            if not any_left:
                print("All readings exhausted in file — finishing now.")
                finished_early = True
                break

            time.sleep(interval)

        if finished_early:
            break

except KeyboardInterrupt:
    print("Interrupted by user.")

# optional FIN marker (server must be prepared to detect this)
try:
    fin_payload = b"__FIN__"
    fin_header = build_header(device_id=0, batch_count=0, seq_num=0, msg_type=MSG_INIT)
    sock.sendto(fin_header + fin_payload, SERVER_ADDR)
    print("Sent FIN marker.")
except Exception as e:
    print("Failed to send FIN:", e)

running = False
time.sleep(0.2)
sock.close()
print("Client finished.")
