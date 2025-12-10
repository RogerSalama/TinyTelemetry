import socket
import time
import csv
import os
import sys
from datetime import datetime
from protocol import (
    MAX_BYTES, HEADER_SIZE, parse_header, MSG_INIT, MSG_DATA, HEART_BEAT,
    validate_packet
)

sys.stdout.reconfigure(line_buffering=True)

SERVER_PORT = 12000
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

class DeviceTracker:
    def __init__(self):
        self.highest_seq = 0
        self.missing_set = set()

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
        device_ts_str = time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(header['timestamp'])) + f".{header['milliseconds']:03d}"

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

        delay = (time.time() * 1000 - device_timestamp_ms) / 1000.0
        received_count += 1
        cpu_time_ms = (time.process_time() - start_cpu) * 1000

        msg_str = {MSG_INIT: "INIT", MSG_DATA: "DATA", HEART_BEAT: "HEARTBEAT"}.get(msg_type, f"UNKNOWN({msg_type})")

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

        save_to_csv_row(row)

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
    pass  
finally:
    server_socket.close()
