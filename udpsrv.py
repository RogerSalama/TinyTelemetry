#!/usr/bin/env python3
# udpsrv.py - UDP server for TinyTelemetry
# Writes logs/iot_device_data.csv with normalized readings (one reading per CSV row).
# Keeps payload as base64 of original packet payload for compatibility.

import socket
import time
import csv
import os
import sys
from datetime import datetime
import struct
import threading
import tempfile
import base64

# protocol import must provide required names
from protocol import *  # e.g., MAX_BYTES, HEADER_SIZE, parse_header, decrypt_bytes, build_checksum_header, calculate_expected_checksum, MSG_DATA, MSG_INIT, HEART_BEAT, NACK_MSG

# --- Real-time logging ---
sys.stdout.reconfigure(line_buffering=True)
SERVER_ID = 1

# --- Server setup ---
SERVER_PORT = 12002
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(('', SERVER_PORT))
print(f"UDP Server running on port {SERVER_PORT} (max {MAX_BYTES} bytes)")

# --- NACK config ---
NACK_DELAY_SECONDS = 0.1
nack_lock = threading.Lock()
delayed_nack_requests = []

# --- CSV config ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

CSV_FILENAME = os.path.join(LOG_DIR, "iot_device_data.csv")

CSV_HEADERS = [
    "server_timestamp", "device_id", "unit/batch_count", "sequence_number",
    "device_timestamp", "message_type", "payload",
    "client_address", "delay_seconds", "duplicate_flag", "gap_flag",
    "packet_size", "cpu_time_ms",
    "decoded_reading", "reading_index"
]

def ensure_csv_has_header():
    """Ensure CSV exists and has the header as the first row."""
    if not os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "w", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
        try:
            os.chmod(CSV_FILENAME, 0o664)
        except Exception:
            pass
        print(f"Created CSV with header: {CSV_FILENAME}")
        return

    # If exists, verify header
    try:
        with open(CSV_FILENAME, "r", newline='') as f:
            reader = csv.reader(f)
            try:
                first = next(reader)
            except StopIteration:
                first = None
    except Exception as e:
        # fallback: recreate file with header
        print(f"Warning reading CSV ({e}), recreating with header.")
        with open(CSV_FILENAME, "w", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
        return

    if first == CSV_HEADERS:
        return

    # Prepend header safely
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=LOG_DIR, text=True)
        with os.fdopen(tmp_fd, "w", newline='') as tmpf:
            writer = csv.writer(tmpf)
            writer.writerow(CSV_HEADERS)
            with open(CSV_FILENAME, "r", newline='') as origf:
                for line in origf:
                    tmpf.write(line)
        os.replace(tmp_path, CSV_FILENAME)
        print(f"Prepended header to existing CSV: {CSV_FILENAME}")
    except Exception as e:
        print(f"Error ensuring CSV header: {e}")

def mark_duplicate_rows(device_id, seq):
    """
    Mark duplicate_flag = 1 for all rows matching device_id and sequence_number.
    Uses atomic replace.
    Returns True if any rows updated.
    """
    updated = False
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=LOG_DIR, text=True)
        with os.fdopen(tmp_fd, "w", newline='') as tmpf:
            writer = csv.writer(tmpf)
            writer.writerow(CSV_HEADERS)
            if os.path.exists(CSV_FILENAME):
                with open(CSV_FILENAME, "r", newline='') as origf:
                    reader = csv.reader(origf)
                    # skip original header
                    try:
                        next(reader)
                    except StopIteration:
                        pass
                    for row in reader:
                        # ensure row long enough
                        if len(row) >= 4 and row[1] == str(device_id) and row[3] == str(seq):
                            # ensure duplicate_flag index exists (index 9)
                            while len(row) <= 9:
                                row.append("")
                            row[9] = '1'
                            updated = True
                        writer.writerow(row)
        if updated:
            os.replace(tmp_path, CSV_FILENAME)
            print(f" [!] Marked duplicate_flag for Device:{device_id}, Seq:{seq}")
        else:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(f"Error marking duplicate rows: {e}")
    return updated

def append_rows(rows):
    """Append a list of rows to the CSV (rows are lists)."""
    try:
        with open(CSV_FILENAME, "a", newline='') as csvfile:
            writer = csv.writer(csvfile)
            for r in rows:
                writer.writerow(r)
    except Exception as e:
        print(f"Error appending to CSV: {e}")

# --- NACK handling ---
server_seq = 1

def schedule_NACK(device_id, addr, missing_seq):
    unique_key = (device_id, missing_seq)
    nack_time = time.time() + NACK_DELAY_SECONDS
    request = {'device_id': device_id, 'missing_seq': missing_seq, 'addr': addr, 'nack_time': nack_time}
    with nack_lock:
        found = False
        for req in delayed_nack_requests:
            if req['device_id'] == device_id and req['missing_seq'] == missing_seq:
                found = True
                break
        if not found:
            delayed_nack_requests.append(request)
            print(f" [~] Scheduled NACK for ID:{device_id}, seq: {missing_seq} at T + {NACK_DELAY_SECONDS}s")
        else:
            print(f" [X] Ignoring duplicate schedule request for ID:{device_id}, seq: {missing_seq}")

def send_NACK_now(device_id, addr, missing_seq):
    global server_seq
    srv_payload_str = f"{device_id}:{missing_seq}"
    srv_payload_bytes = srv_payload_str.encode('utf-8')
    srv_header = build_checksum_header(device_id=SERVER_ID, batch_count=1, seq_num=server_seq, msg_type=NACK_MSG, payload=srv_payload_bytes)
    server_seq += 1
    packet = srv_header + srv_payload_bytes
    try:
        server_socket.sendto(packet, addr)
        print(f" [<<] Sent NACK request for ID:{device_id}, seq: {missing_seq}")
    except Exception as e:
        print(f"Error sending NACK: {e}")

def nack_scheduler():
    global delayed_nack_requests
    print("NACK Scheduler Thread started.")
    while True:
        now = time.time()
        with nack_lock:
            ready = [req for req in delayed_nack_requests if req['nack_time'] <= now]
            delayed_nack_requests = [req for req in delayed_nack_requests if req['nack_time'] > now]
        for req in ready:
            device_id = req['device_id']
            missing_seq = req['missing_seq']
            if (device_id in trackers and missing_seq in trackers[device_id].missing_set) or (missing_seq == 1 and device_id not in trackers):
                send_NACK_now(device_id=device_id, addr=req['addr'], missing_seq=missing_seq)
        time.sleep(0.05)

# --- Device tracking ---
class DeviceTracker:
    def __init__(self):
        self.highest_seq = 0
        self.missing_set = set()

# Initialize CSV header (safe)
ensure_csv_has_header()

trackers = {}
received_count = 0
duplicate_count = 0
corruption_count = 0

threading.Thread(target=nack_scheduler, daemon=True).start()

# --- Main loop ---
try:
    while True:
        data, addr = server_socket.recvfrom(MAX_BYTES)
        start_cpu = time.perf_counter()
        server_receive_time = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        try:
            header = parse_header(data)
        except ValueError as e:
            print("Header error:", e)
            continue

        payload_bytes = data[HEADER_SIZE:]
        BASE_HEADER_SIZE = 9
        base_header_bytes = data[:BASE_HEADER_SIZE]
        calculated_checksum = calculate_expected_checksum(base_header_bytes, payload_bytes)

        # Interpret payload
        if header['msg_type'] == MSG_DATA:
            num = header.get('batch_count', 0)
            expected_len = num * 8
            if isinstance(num, int) and num > 0 and len(payload_bytes) >= expected_len:
                try:
                    enc = payload_bytes[:expected_len]
                    dec = decrypt_bytes(enc, header['device_id'], header['seq'])
                    fmt = '!' + ('d' * num)
                    values = list(struct.unpack(fmt, dec))
                    payload_str = ",".join(f"{v:.6f}" for v in values)
                except Exception as e:
                    print(f" Payload decode error (ID:{header['device_id']} seq:{header['seq']}): {e}")
                    try:
                        payload_str = payload_bytes.decode('utf-8', errors='ignore')
                    except Exception:
                        payload_str = payload_bytes
            else:
                try:
                    payload_str = payload_bytes.decode('utf-8', errors='ignore')
                except Exception:
                    payload_str = payload_bytes
        else:
            try:
                payload_str = payload_bytes.decode('utf-8', errors='ignore')
            except Exception:
                payload_str = payload_bytes

        device_id = header['device_id']
        seq = header['seq']

        # require INIT before DATA
        if device_id not in trackers:
            if header['msg_type'] == MSG_INIT:
                trackers[device_id] = DeviceTracker()
                trackers[device_id].highest_seq = seq - 1
            else:
                print(f" [!] Gap Detected! ID:{device_id}, Missing packets: 1 (no INIT)")
                schedule_NACK(device_id=device_id, addr=addr, missing_seq=1)
                continue

        tracker = trackers[device_id]
        duplicate_flag = 0
        gap_flag = 0

        received_checksum = header.get('checksum')
        checksum_valid = (received_checksum == calculated_checksum)
        if not checksum_valid:
            corruption_count += 1
            print(f"⚠️ Checksum mismatch: received={received_checksum}, calculated={calculated_checksum} (ID:{device_id} seq:{seq})")
            continue

        diff = seq - tracker.highest_seq
        if seq == 0:
            print("Heartbeat Received")
        elif diff == 1:
            tracker.highest_seq = seq
        elif diff > 1:
            gap_flag = 1
            for missing_seq in range(tracker.highest_seq + 1, seq):
                tracker.missing_set.add(missing_seq)
                schedule_NACK(device_id=device_id, addr=addr, missing_seq=missing_seq)
            tracker.highest_seq = seq
        else:  # diff <= 0
            if seq in tracker.missing_set:
                tracker.missing_set.remove(seq)
                print(f" [+] Recovered packet ID:{device_id}, seq:{seq} (was missing).")
            else:
                duplicate_flag = 1
                duplicate_count += 1
                received_count -= 1
                print(f" [D] Duplicate detected: ID:{device_id}, seq:{seq}. Content ignored.")

        delay = round(time.time() - header['timestamp'], 3)
        received_count += 1
        end_cpu = time.perf_counter()
        cpu_time_ms = (end_cpu - start_cpu) * 1000

                # Build base payload_b64 (base64 of the original raw payload bytes)
        try:
            # prefer using the original raw payload bytes (the network bytes)
            raw_payload_bytes = payload_bytes if isinstance(payload_bytes, (bytes, bytearray)) else (payload_str.encode('utf-8', errors='ignore') if isinstance(payload_str, str) else b"")
            payload_b64 = base64.b64encode(raw_payload_bytes).decode('ascii') if raw_payload_bytes else ""
        except Exception:
            payload_b64 = ""

        # Choose the human-readable payload cell for the CSV:
        # prefer payload_str (decoded readings) when it's a string, otherwise fall back to payload_b64.
        if isinstance(payload_str, str) and payload_str.strip() != "":
            payload_cell = payload_str.strip()
        else:
            payload_cell = payload_b64

        # Avoid writing extremely large cells into CSV; truncate if necessary
        MAX_PAYLOAD_LEN = 2000
        if isinstance(payload_cell, str) and len(payload_cell) > MAX_PAYLOAD_LEN:
            payload_cell = payload_cell[:MAX_PAYLOAD_LEN] + "...(truncated)"

        # For DATA: split payload_str into readings if it's a string
        rows_to_append = []
        if header['msg_type'] == MSG_DATA and isinstance(payload_str, str):
            readings = [r for r in payload_str.split(",") if r != ""]
            if readings:
                for idx, reading in enumerate(readings, start=1):
                    decoded = reading.strip()
                    # validation: ensure numeric; if not numeric, keep as-is but note
                    try:
                        _ = float(decoded)
                        decoded_reading = f"{float(decoded):.6f}"
                    except Exception:
                        decoded_reading = decoded  # could be non-numeric
                    row = [
                        f" {server_receive_time}",
                        str(device_id),
                        code_to_unit(header.get('batch_count', 0)),
                        str(seq),
                        f" {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(header['timestamp']))}.{header.get('milliseconds', 0):03d}",
                        "DATA",
                        payload_cell,                   # human-readable payload (was base64 before)
                        f"{addr[0]}:{addr[1]}",
                        str(delay),
                        str(duplicate_flag),
                        str(gap_flag),
                        str(len(data)),
                        f"{cpu_time_ms:.4f}",
                        decoded_reading,
                        str(idx)
                    ]
                    rows_to_append.append(row)
            else:
                # No readings parsed: write a single row with empty decoded_reading
                row = [
                    f" {server_receive_time}",
                    str(device_id),
                    code_to_unit(header.get('batch_count', 0)),
                    str(seq),
                    f" {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(header['timestamp']))}.{header.get('milliseconds', 0):03d}",
                    "DATA",
                    payload_cell,
                    f"{addr[0]}:{addr[1]}",
                    str(delay),
                    str(duplicate_flag),
                    str(gap_flag),
                    str(len(data)),
                    f"{cpu_time_ms:.4f}",
                    "",
                    "0"
                ]
                rows_to_append.append(row)
        else:
            # INIT or HEARTBEAT: single row, no decoded_reading
            msg_type_str = "INIT" if header['msg_type'] == MSG_INIT else ("HEARTBEAT" if header['msg_type'] == HEART_BEAT else f"UNKNOWN({header['msg_type']})")
            row = [
                f" {server_receive_time}",
                str(device_id),
                code_to_unit(header.get('batch_count', 0)) if header['msg_type'] == MSG_INIT else "",
                str(seq),
                f" {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(header['timestamp']))}.{header.get('milliseconds', 0):03d}",
                msg_type_str,
                payload_cell,
                f"{addr[0]}:{addr[1]}",
                str(delay),
                str(duplicate_flag),
                str(gap_flag),
                str(len(data)),
                f"{cpu_time_ms:.4f}",
                "",
                "0"
            ]
            rows_to_append.append(row)

        # Save rows to CSV (append)
        try:
            append_rows(rows_to_append)
        except Exception as e:
            print(f"Error saving rows to CSV: {e}")

        # If duplicate, mark existing rows' duplicate_flag
        if header['msg_type'] == MSG_DATA:
            if duplicate_flag:
                # Mark duplicates in existing file (all rows with same device_id+seq)
                marked = mark_duplicate_rows(device_id, seq)
                if not marked:
                    print(f" [!] Duplicate detected but could not find rows to mark for Device:{device_id}, Seq:{seq}")
                else:
                    print(f" -> DATA received (ID:{device_id}, seq={seq}) DUPLICATE (Marked).")
            else:
                if gap_flag:
                    print(f" -> DATA received (ID:{device_id}, seq={seq}) with GAP.")
                else:
                    print(f" -> DATA received (ID:{device_id}, seq={seq})")
        elif header['msg_type'] == MSG_INIT:
            print(f" -> INIT message from Device {device_id} (unit={code_to_unit(header.get('batch_count',0))})")
            # reset tracker state
            trackers[device_id].highest_seq = seq
            trackers[device_id].missing_set.clear()
        elif header['msg_type'] == HEART_BEAT:
            print(f" -> HEARTBEAT from Device {device_id}")

except KeyboardInterrupt:
    print("\nServer interrupted. Generating summary...")
    total_expected = sum(t.highest_seq for t in trackers.values())
    missing_count = total_expected - received_count
    delivery_rate = (received_count / total_expected) * 100 if total_expected else 0
    print("\n=== Baseline Test Summary ===")
    print(f"Total received: {received_count}")
    print(f"Missing packets: {missing_count}")
    print(f"Duplicate packets: {duplicate_count}")
    print(f"Checksum mismatches: {corruption_count}")
    print(f"Delivery rate: {delivery_rate:.2f}%")
