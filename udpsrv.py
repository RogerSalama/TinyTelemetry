import socket
import time
import csv
import os
import sys
from datetime import datetime
from protocol import MAX_BYTES, HEADER_SIZE, parse_header, MSG_INIT, MSG_DATA, HEART_BEAT

# --- Real-time logging ---
sys.stdout.reconfigure(line_buffering=True)

# --- Server setup ---
SERVER_PORT = 12000
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(('', SERVER_PORT))
print(f"UDP Server running on port {SERVER_PORT} (max {MAX_BYTES} bytes)")

# --- CSV Configuration ---
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
        try:
            with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(CSV_HEADERS)
                csvfile.flush(); os.fsync(csvfile.fileno())
            print(f"Created new CSV file with headers: {CSV_FILENAME}")
        except Exception as e:
            print(f"✗ Could not create CSV file {CSV_FILENAME}: {e}")
    else:
        print(f"Appending to existing CSV: {CSV_FILENAME}")

def save_to_csv_row(row):
    """Append one row and fsync immediately so Excel can see it."""
    try:
        with open(CSV_FILENAME, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(row)
            csvfile.flush()
            try:
                os.fsync(csvfile.fileno())
            except Exception:
                pass
    except Exception as e:
        print(f"✗ Error writing to CSV: {e}")

# --- State Tracking Class ---
class DeviceTracker:
    def __init__(self):
        self.highest_seq = 0
        self.missing_set = set()

# --- Initialize ---
init_csv_file()
trackers = {}
received_count = 0
duplicate_count = 0

try:
    while True:
        data, addr = server_socket.recvfrom(MAX_BYTES)

        start_cpu = time.process_time()
        server_receive_time = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        try:
            header = parse_header(data)
        except ValueError as e:
            print("Header error:", e)
            continue

        # decode payload (strip any trailing nulls)
        payload = data[HEADER_SIZE:].rstrip(b'\x00').decode('utf-8', errors='ignore')

        device_id = header['device_id']
        seq = header['seq']
        batch_count = header['batch_count']
        msg_type = header['msg_type']

        # combine timestamp + ms to a single ms integer and human string
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
            missing_list = []
            for missing_seq in range(tracker.highest_seq + 1, seq):
                tracker.missing_set.add(missing_seq)
                missing_list.append(str(missing_seq))
            print(f" [!] Gap Detected (device={device_id}): missing {missing_list}")
            if missing_list:
                nack_msg = f"NACK:{device_id}:" + ",".join(missing_list)
                server_socket.sendto(nack_msg.encode('utf-8'), addr)
                print(f" [<<] Sent NACK request for device={device_id}: {missing_list}")
            tracker.highest_seq = seq
        else:  # diff <= 0
            if seq in tracker.missing_set:
                tracker.missing_set.remove(seq)
                print(f" [+] Recovered packet device={device_id} seq={seq} (was missing).")
            else:
                duplicate_flag = 1
                duplicate_count += 1
                print(f" [D] Duplicate detected device={device_id} seq={seq}.")

        # compute delay in seconds (floating)
        delay = (time.time() * 1000 - device_timestamp_ms) / 1000.0
        received_count += 1
        cpu_time_ms = (time.process_time() - start_cpu) * 1000

        # Map message type to string
        if msg_type == MSG_INIT:
            msg_str = "INIT"
        elif msg_type == MSG_DATA:
            msg_str = "DATA"
        elif msg_type == HEART_BEAT:
            msg_str = "HEARTBEAT"
        else:
            msg_str = f"UNKNOWN({msg_type})"

        # Build CSV row (payload will be quoted automatically by csv.writer)
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

        # For DATA, append immediately (preserve arrival order)
        if msg_type == MSG_DATA:
            save_to_csv_row(row)
            if gap_flag:
                print(f" -> DATA received (device={device_id}, seq={seq}, batch={batch_count}) with GAP.")
            elif duplicate_flag:
                print(f" -> DATA received (device={device_id}, seq={seq}, batch={batch_count}) DUPLICATE.")
            else:
                print(f" -> DATA received (device={device_id}, seq={seq}, batch={batch_count}).")

        elif msg_type == MSG_INIT:
            print(f" -> INIT message from device={device_id} seq={seq} payload=[{payload}]")
            # reset tracker for this device
            trackers[device_id].highest_seq = seq
            trackers[device_id].missing_set.clear()
            save_to_csv_row(row)

        elif msg_type == HEART_BEAT:
            print(f" -> HEARTBEAT from device={device_id} seq={seq}")
            save_to_csv_row(row)

        else:
            print(f"Unknown message type {msg_type} from device={device_id} seq={seq}")

except KeyboardInterrupt:
    print("\nServer interrupted. Generating summary...")
    total_expected = sum(tr.highest_seq for tr in trackers.values()) if trackers else 0
    missing_count = max(0, total_expected - received_count) if total_expected else 0
    delivery_rate = (received_count / total_expected)*100 if total_expected else 0.0

    print("\n=== Baseline Test Summary ===")
    print(f"Total received: {received_count}")
    print(f"Missing packets (est): {missing_count}")
    print(f"Duplicate packets: {duplicate_count}")
    print(f"Delivery rate (est): {delivery_rate:.2f}%")
finally:
    try:
        server_socket.close()
    except Exception:
        pass
    print("Server exiting.")
