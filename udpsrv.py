import socket
import time
import csv
import os
from datetime import datetime
from protocol import (
<<<<<<< HEAD
    MAX_BYTES, HEADER_SIZE, parse_header, MSG_INIT, MSG_DATA, HEART_BEAT,
    validate_packet
)

sys.stdout.reconfigure(line_buffering=True)

SERVER_PORT = 12000
=======
    MAX_BYTES, HEADER_SIZE, parse_header, MSG_INIT, MSG_DATA, HEART_BEAT
)

SERVER_PORT = 12002

# Line-buffer stdout for immediate logs (safe if available)
try:
    import sys
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

>>>>>>> e8a682a (fixing errors)
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(('', SERVER_PORT))
print(f"UDP Server running on port {SERVER_PORT} (max {MAX_BYTES} bytes)")

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
CSV_FILENAME = os.path.join(LOG_DIR, "iot_device_data.csv")
CSV_HEADERS = [
    "server_timestamp", "device_id", "batch_count", "sequence_number",
    "device_timestamp", "message_type", "payload",
    "client_address", "delay_seconds", "duplicate_flag", "gap_flag",
    "packet_size", "cpu_time_ms"
]

def init_csv_file():
    needs_header = not os.path.exists(CSV_FILENAME) or os.path.getsize(CSV_FILENAME) == 0
    if needs_header:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile, quoting=csv.QUOTE_ALL)
            writer.writerow(CSV_HEADERS)
            csvfile.flush(); os.fsync(csvfile.fileno())
        print(f"Created new CSV file: {CSV_FILENAME}")
    else:
        print(f"Appending to existing CSV: {CSV_FILENAME}")

def save_to_csv_row(row):
    try:
        with open(CSV_FILENAME, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile, quoting=csv.QUOTE_ALL)
            writer.writerow(row)
            csvfile.flush()
            try:
                os.fsync(csvfile.fileno())
            except Exception:
                pass
    except Exception as e:
        print(f"✗ Error writing to CSV: {e}")
<<<<<<< HEAD
=======

# ----- packet validation -----
def validate_packet(data: bytes):
    if not data:
        return False, "empty packet"
    if len(data) > MAX_BYTES:
        return False, f"packet too large ({len(data)} > {MAX_BYTES})"
    if len(data) < HEADER_SIZE:
        # Allow plain-text NACK coming from server? (Server doesn't receive NACKs)
        return False, f"too small for header ({len(data)} < {HEADER_SIZE})"
    try:
        h = parse_header(data)
    except Exception as e:
        return False, f"header parse error: {e}"

    msg_type = h.get('msg_type')
    if msg_type not in (MSG_INIT, MSG_DATA, HEART_BEAT):
        return False, f"unknown msg_type {msg_type}"
    ms = h.get('milliseconds', 0)
    if not (0 <= ms <= 999):
        return False, f"invalid milliseconds {ms}"
    return True, ""
>>>>>>> e8a682a (fixing errors)

class DeviceTracker:
    def __init__(self):
        self.highest_seq = 0
        self.missing_set = set()

<<<<<<< HEAD
=======
# Reorder buffer & metrics
reorder_buffer = []
MAX_BUFFER_SIZE = 15
delay_samples = []
cpu_samples = []
out_of_order_count = 0
last_seq_seen = {}
start_time_server = time.time()

>>>>>>> e8a682a (fixing errors)
init_csv_file()
trackers = {}
received_count = 0
duplicate_count = 0

try:
    while True:
        data, addr = server_socket.recvfrom(MAX_BYTES)
        start_cpu = time.process_time()
        server_receive_time = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        # --- VALIDATION ---
        valid, reason = validate_packet(data)
        if not valid:
            print(f"[!] Invalid packet from {addr}: {reason}")
            continue

        # --- HEADER PARSE ---
        header = parse_header(data)
        payload = data[HEADER_SIZE:].rstrip(b'\x00').decode('utf-8', errors='ignore')
        device_id = header['device_id']
        seq = header['seq']
        batch_count = header['batch_count']
        msg_type = header['msg_type']

        device_timestamp_ms = header['timestamp'] * 1000 + header['milliseconds']
<<<<<<< HEAD
        device_ts_str = time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(header['timestamp'])) + f".{header['milliseconds']:03d}"
=======
        device_ts_str = time.strftime('%d/%m/%Y %H:%M:%S',
                                      time.localtime(header['timestamp'])) + f".{header['milliseconds']:03d}"
>>>>>>> e8a682a (fixing errors)

        if device_id not in trackers:
            trackers[device_id] = DeviceTracker()
            trackers[device_id].highest_seq = seq - 1

        tracker = trackers[device_id]
        duplicate_flag = 0
        gap_flag = 0

        diff = seq - tracker.highest_seq
        if diff == 1:
            tracker.highest_seq = seq
        elif diff > 1:
            gap_flag = 1
            missing_list = list(range(tracker.highest_seq + 1, seq))
            tracker.missing_set.update(missing_list)
            if missing_list:
                nack_msg = f"NACK:{device_id}:" + ",".join(map(str, missing_list))
                server_socket.sendto(nack_msg.encode('utf-8'), addr)
            tracker.highest_seq = seq
        else:
            if seq in tracker.missing_set:
                tracker.missing_set.remove(seq)
            else:
                duplicate_flag = 1
                duplicate_count += 1

<<<<<<< HEAD
        delay = (time.time() * 1000 - device_timestamp_ms) / 1000.0
        received_count += 1
        cpu_time_ms = (time.process_time() - start_cpu) * 1000

        msg_str = {MSG_INIT: "INIT", MSG_DATA: "DATA", HEART_BEAT: "HEARTBEAT"}.get(msg_type, f"UNKNOWN({msg_type})")
=======
        # --- Delay calculation ---
        delay = (time.time() * 1000 - device_timestamp_ms) / 1000.0

        # --- Tracking ---
        received_count += 1
        cpu_time_ms = (time.process_time() - start_cpu) * 1000

        msg_str = {MSG_INIT: "INIT", MSG_DATA: "DATA", HEART_BEAT: "HEARTBEAT"}.get(
            msg_type, f"UNKNOWN({msg_type})"
        )
>>>>>>> e8a682a (fixing errors)

        row = [
            server_receive_time,
            device_id,
            batch_count,
            seq,
            device_ts_str,
            msg_str,
            payload,
            f"{addr[0]}:{addr[1]}",
            f"{delay:.3f}",
            duplicate_flag,
            gap_flag,
            len(data),
            f"{cpu_time_ms:.4f}"
        ]

<<<<<<< HEAD
        save_to_csv_row(row)

=======
        # push into reorder buffer
        reorder_buffer.append((device_timestamp_ms, row))

        # metrics
        delay_samples.append(delay)
        cpu_samples.append(cpu_time_ms)

        # Out-of-order detection
        if device_id in last_seq_seen:
            if device_timestamp_ms < last_seq_seen[device_id]:
                out_of_order_count += 1
        last_seq_seen[device_id] = device_timestamp_ms

        # Flush buffer when full
        if len(reorder_buffer) >= MAX_BUFFER_SIZE:
            reorder_buffer.sort(key=lambda x: x[0])
            for _, sorted_row in reorder_buffer:
                save_to_csv_row(sorted_row)
            reorder_buffer = []

        # Console printing
>>>>>>> e8a682a (fixing errors)
        if msg_type == MSG_DATA:
            if gap_flag:
                print(f" -> DATA received (device={device_id}, seq={seq}, batch={batch_count}) with GAP.")
            elif duplicate_flag:
                print(f" -> DATA received (device={device_id}, seq={seq}, batch={batch_count}) DUPLICATE.")
            else:
                print(f" -> DATA received (device={device_id}, seq={seq}, batch={batch_count}).")
        elif msg_type == MSG_INIT:
            print(f" -> INIT message from device={device_id} seq={seq} payload=[{payload}]")
            tracker.highest_seq = seq
            tracker.missing_set.clear()
        elif msg_type == HEART_BEAT:
            print(f" -> HEARTBEAT from device={device_id} seq={seq}")

except KeyboardInterrupt:
<<<<<<< HEAD
    pass  
finally:
=======
    pass

finally:
    # Flush remaining buffer on exit
    if reorder_buffer:
        reorder_buffer.sort(key=lambda x: x[0])
        for _, sorted_row in reorder_buffer:
            save_to_csv_row(sorted_row)

    # Build summary lines safely
    uptime_s = time.time() - start_time_server
    avg_delay = (sum(delay_samples) / len(delay_samples)) if delay_samples else 0.0
    avg_cpu = (sum(cpu_samples) / len(cpu_samples)) if cpu_samples else 0.0
    summary_lines = [
        f"Server uptime: {uptime_s:.2f} s",
        f"Packets received: {received_count}",
        f"Duplicates: {duplicate_count}",
        f"Out-of-order events: {out_of_order_count}",
        f"Avg delay: {avg_delay:.3f} s",
        f"Avg CPU per packet: {avg_cpu:.3f} ms",
        f"Buffer size threshold: {MAX_BUFFER_SIZE}",
    ]

    for line in summary_lines:
        print(line)

    METRICS_PATH = os.path.join(LOG_DIR, "metrics.log")
    try:
        with open(METRICS_PATH, "a", encoding="utf-8") as mf:
            mf.write("\n".join(summary_lines) + "\n")
        print(f"\nMetrics saved to {METRICS_PATH}")
    except Exception as e:
        print(f"✗ Error saving metrics to {METRICS_PATH}: {e}")

>>>>>>> e8a682a (fixing errors)
    server_socket.close()
