import socket
import time
import csv
import os
import sys
from datetime import datetime
import struct
import threading
from protocol import *
import base64
import signal
# --- Real-time logging ---
sys.stdout.reconfigure(line_buffering=True)
SERVER_ID = 1

# --- Server setup ---
SERVER_PORT = 12001
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(('', SERVER_PORT))
print(f"UDP Server running on port {SERVER_PORT} (max {MAX_BYTES} bytes)")

NACK_DELAY_SECONDS = 0.35
nack_lock = threading.Lock()
delayed_nack_requests = []

# --- CSV Configuration ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
CSV_FILENAME = os.path.join(LOG_DIR, "iot_device_data.csv")
CSV_HEADERS = [
    "server_timestamp", "device_id", "unit/batch_count", "sequence_number",
    "device_timestamp", "message_type", "payload",
    "client_address", "delay_seconds", "duplicate_flag", "gap_flag",
    "packet_size", "cpu_time_ms"
]

def init_csv_file():
    """
    Initialize the CSV file by truncating it and writing only the header.
    This ensures each run starts with a fresh CSV instead of preserving rows
    from previous runs.
    """
    with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(CSV_HEADERS)
    print(f"CSV initialized (truncated) at: {CSV_FILENAME}")


def save_to_csv(data_dict, is_update=False):
    """Save data to CSV; update duplicate_flag if needed."""
    seq = str(data_dict['seq'])
    device_id = str(data_dict['device_id'])

    msg_type = data_dict['msg_type']
    if msg_type == MSG_INIT:
        msg_type_str = "INIT"
    elif msg_type == MSG_DATA:
        msg_type_str = "DATA"
    elif msg_type == HEART_BEAT:
        msg_type_str = "HEARTBEAT"
    else:
        msg_type_str = f"UNKNOWN({msg_type})"

    # --- FIX START: Do not Base64 encode if it's already a string ---
    payload = data_dict['payload']
    
    # If payload is bytes, we might want to Base64 encode it to be safe.
    # If it is a string (which your DATA packets are now), save it directly.
    if isinstance(payload, bytes):
        final_payload = base64.b64encode(payload).decode('ascii')
    else:
        # It's already a string (e.g., "12.50,30.00"), just use it
        final_payload = str(payload)
    # --- FIX END ---

    new_row = [
        data_dict['server_timestamp'],
        device_id,
        code_to_unit(data_dict['batch_count']) if msg_type == MSG_INIT else (data_dict['batch_count'] if msg_type == MSG_DATA else ""),
        seq,
        data_dict['timestamp'],
        msg_type_str,
        final_payload,  # Use the fixed variable here
        data_dict['client_address'],
        str(data_dict['delay_seconds']),
        str(data_dict['duplicate_flag']),
        str(data_dict['gap_flag']),
        str(data_dict['packet_size']),
        f"{data_dict['cpu_time_ms']:.4f}"
    ]

    try:
        if is_update:
            rows = []
            row_found = False
            # Read all rows into memory
            if os.path.exists(CSV_FILENAME):
                with open(CSV_FILENAME, 'r', newline='', encoding='utf-8') as csvfile:
                    reader = csv.reader(csvfile)
                    try:
                        headers = next(reader)
                        rows.append(headers)
                    except StopIteration:
                        pass # Empty file
                    
                    for row in reader:
                        # Row indices: 1=device_id, 3=seq
                        if len(row) > 3 and row[1] == device_id and row[3] == seq:
                            # Update duplicate flag (index 9 based on your structure)
                            row[9] = '1'
                            print(f" [!] Updated CSV row for duplicate packet (Device:{device_id}, Seq:{seq})")
                            row_found = True
                        rows.append(row)

            if row_found:
                with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerows(rows)
            else:
                # Fallback if not found (though logic suggests it should be there)
                pass 
        else:
            # --- APPEND LOGIC for new packets ---
            with open(CSV_FILENAME, 'a', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(new_row)
            print(f" Data saved (ID:{device_id}, seq={seq})")
    except Exception as e:
        print(f" Error writing/rewriting to CSV: {e}")


# --- NACK Handling ---
server_seq = 1

def schedule_NACK(device_id, addr, missing_seq):
    unique_key = (device_id, missing_seq)
    nack_time = time.time() + NACK_DELAY_SECONDS
    request = {'device_id': device_id, 'missing_seq': missing_seq, 'addr': addr, 'nack_time': nack_time}

    with nack_lock:
        if not any((req['device_id'], req['missing_seq']) == unique_key for req in delayed_nack_requests):
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
    Nack_packet = srv_header + srv_payload_bytes
    server_socket.sendto(Nack_packet, addr)
    print(f" [<<] Sent NACK request for ID:{device_id}, seq: {missing_seq}")


def nack_scheduler():
    global delayed_nack_requests
    print("NACK Scheduler Thread started.")
    while True:
        now = time.time()
        with nack_lock:
            requests_to_send = [req for req in delayed_nack_requests if req['nack_time'] <= now]
            delayed_nack_requests = [req for req in delayed_nack_requests if req['nack_time'] > now]
        for req in requests_to_send:
            device_id = req['device_id']
            missing_seq = req['missing_seq']
            if (device_id in trackers and missing_seq in trackers[device_id].missing_set) or (missing_seq == 1 and device_id not in trackers):
                send_NACK_now(device_id=device_id, addr=req['addr'], missing_seq=missing_seq)
        time.sleep(0.1)


# --- Device State Tracking ---
class DeviceTracker:
    def __init__(self):
        self.highest_seq = 0
        self.missing_set = set()



# ----------------------------------------------
# ADDED: timestamp reordering + metrics (CSV)
# ----------------------------------------------
import heapq  # ADDED
# ADDED: second CSV in timestamp order (analysis only; original CSV unchanged)
REORDER_CSV = os.path.join(LOG_DIR, "iot_device_data_reordered.csv")
_REORDER_INIT = False
def _init_reorder_csv():  # ADDED
    """Create (or reset) the reordered CSV with the same headers as the main CSV."""
    global _REORDER_INIT
    if _REORDER_INIT:
        return
    with open(REORDER_CSV, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)  # same columns for easy comparison
    _REORDER_INIT = True
class _Pkt:  # ADDED
    __slots__ = ("ts_key_ms", "csv_dict", "dup", "gap")
    def __init__(self, ts_key_ms, csv_dict, dup, gap):
        self.ts_key_ms = ts_key_ms
        self.csv_dict = csv_dict
        self.dup = dup
        self.gap = gap
    def __lt__(self, other):
        # order by device timestamp key in milliseconds
        return self.ts_key_ms < other.ts_key_ms
class _ReorderBuffer:  # ADDED
    """
    Small jitter-guarded buffer keyed by sensor/device timestamp (ms).
    Packets stay until they are safe to release in timestamp order.
    """
    def __init__(self, guard_ms=150, max_buffer_ms=1000):
        self.guard_ms = guard_ms
        self.max_buffer_ms = max_buffer_ms
        self.heap = []  # holds (pkt, arrival_ms)
        self.max_seen_ts = 0
    def push(self, pkt, arrival_ms: int):
        if pkt.ts_key_ms > self.max_seen_ts:
            self.max_seen_ts = pkt.ts_key_ms
        heapq.heappush(self.heap, (pkt, arrival_ms))
    def flush_ready(self, now_ms: int):
        """
        Flush packets whose sensor timestamp is <= watermark
        or have stayed longer than max_buffer_ms.
        """
        ready = []
        watermark = self.max_seen_ts - self.guard_ms
        while self.heap:
            top_pkt, top_arrival = self.heap[0]
            if top_pkt.ts_key_ms <= watermark or (now_ms - top_arrival) >= self.max_buffer_ms:
                ready.append(heapq.heappop(self.heap)[0])
            else:
                break
        return ready
    def flush_all(self):
        """
        Flush all remaining packets in timestamp order
        (used at graceful shutdown).
        """
        out = [heapq.heappop(self.heap)[0] for _ in range(len(self.heap))]
        out.sort(key=lambda p: p.ts_key_ms)
        return out
def _save_reordered(pkt_list):
    """Append flushed packets to the reordered CSV with normal payload (no base64)."""
    if not pkt_list:
        return
    _init_reorder_csv()
    with open(REORDER_CSV, 'a', newline='') as f:
        writer = csv.writer(f)
        for p in pkt_list:
            d = p.csv_dict
            msg_type = d['msg_type']
            if msg_type == MSG_INIT:
                msg_type_str = "INIT"
            elif msg_type == MSG_DATA:
                msg_type_str = "DATA"
            elif msg_type == HEART_BEAT:
                msg_type_str = "HEARTBEAT"
            else:
                msg_type_str = f"UNKNOWN({msg_type})"

            payload = d['payload']
            if isinstance(payload, bytes):
                payload = payload.decode('utf-8', errors='ignore')

            writer.writerow([
                d['server_timestamp'],
                d['device_id'],
                code_to_unit(d['batch_count']) if msg_type == MSG_INIT else (d['batch_count'] if msg_type == MSG_DATA else ""),
                d['seq'],
                d['timestamp'],
                msg_type_str,
                payload,  # <-- normal payload
                d['client_address'],
                d['delay_seconds'],
                1 if p.dup else 0,
                1 if p.gap else 0,
                d['packet_size'],
                f"{d['cpu_time_ms']:.4f}",
            ])


def graceful_shutdown(signum, frame):
    print(f"\n[Shutdown] Signal {signum} received — flushing reorder buffer")

    remaining = _reorder.flush_all()
    _save_reordered(remaining)

    server_socket.close()
    print(f"[Shutdown] Reordered CSV finalized: {REORDER_CSV}")
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)  # kill PID
signal.signal(signal.SIGINT, graceful_shutdown)   # Ctrl+C


# ADDED: metrics accumulators (aggregate for metrics.csv)
metrics_packets = 0
metrics_bytes = 0
metrics_cpu_ms = 0.0
metrics_dup_total = 0
metrics_gap_total = 0
_reorder = _ReorderBuffer(guard_ms=150, max_buffer_ms=1000)  # ADDED
def _now_ms():  # ADDED
    return int(time.time() * 1000)

# PATCH: reporting interval tracking (per device)
last_data_ts_ms = {}        # device_id -> last valid DATA device timestamp in ms
report_intervals_ms = []    # collected intervals across the run



# --- Initialize ---
init_csv_file()
trackers = {}
received_count = 0
duplicate_count = 0
corruption_count = 0
# --- FORCE reordered CSV creation at startup ---
_init_reorder_csv()
print(f"Reordered CSV initialized: {REORDER_CSV}")


threading.Thread(target=nack_scheduler, daemon=True).start()


# --- Main Server Loop ---
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

        if header['msg_type'] == MSG_DATA:
            num = header['batch_count']  # Total number of batches
            if len(payload_bytes) > 0 and num > 0:
                try:
                    # Decrypt the entire payload
                    dec = decrypt_bytes(payload_bytes, header['device_id'], header['seq'])
                    
                    # Parse using smart structure
                    values = decode_smart_payload(dec, num)
                    
                    payload = ",".join(f"{v:.6f}" for v in values)
                except Exception as e:
                    print(f"Error parsing smart payload: {e}")
                    # Fallback to text
                    payload = payload_bytes.decode('utf-8', errors='ignore')
            else:
                payload = payload_bytes.decode('utf-8', errors='ignore')
        else:  
            # INIT or HEARTBEAT: treat payload as text (usually empty)
            payload = payload_bytes.decode('utf-8', errors='ignore')

        device_id = header['device_id']
        seq = header['seq']

        if device_id not in trackers:
            if header['msg_type'] == MSG_INIT:
                trackers[device_id] = DeviceTracker()
                trackers[device_id].highest_seq = seq - 1
            else:
                print(f" [!] Gap Detected! ID:{device_id}, Missing packets: 1")
                schedule_NACK(device_id=device_id, addr=addr, missing_seq=1)
                continue

        tracker = trackers[device_id]
        duplicate_flag = 0
        gap_flag = 0
        received_checksum = header['checksum']
        checksum_valid = (received_checksum == calculated_checksum)
        if not checksum_valid:
            corruption_count += 1
            print(f"⚠️ Checksum mismatch: received={received_checksum}, calculated={calculated_checksum}")
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
        elif diff <= 0:
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

        csv_data = {
            'server_timestamp': f" {server_receive_time}",
            'device_id': device_id,
            'batch_count': header['batch_count'],
            'seq': seq,
            'timestamp': f" {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(header['timestamp']))}.{header['milliseconds']:03d}",
            'msg_type': header['msg_type'],
            'payload': payload,
            'client_address': f"{addr[0]}:{addr[1]}",
            'delay_seconds': delay,
            'duplicate_flag': duplicate_flag,
            'gap_flag': gap_flag,
            'packet_size': len(data),
            'cpu_time_ms': cpu_time_ms
        }

        if header['msg_type'] == MSG_DATA:
            if duplicate_flag:
                save_to_csv(csv_data, True)
            else:
                save_to_csv(csv_data)
                 # ---------- REORDER BUFFER (ALWAYS ACTIVE) ----------
                ts_ms = int(header['timestamp'] * 1000) + int(header['milliseconds'])

                pkt = _Pkt(
                    ts_key_ms=ts_ms,
                    csv_dict=csv_data,
                    dup=bool(duplicate_flag),
                    gap=bool(gap_flag)
                )

                _reorder.push(pkt, _now_ms())

                ready = _reorder.flush_ready(_now_ms())
                _save_reordered(ready)

                metrics_packets += 1
                metrics_bytes += len(data)          # total bytes on the wire for this reading
                metrics_cpu_ms += cpu_time_ms 

                ts_ms = int(header['timestamp'] * 1000) + int(header['milliseconds'])
                prev = last_data_ts_ms.get(device_id)
                if prev is not None and ts_ms > prev:
                    report_intervals_ms.append(ts_ms - prev)
                last_data_ts_ms[device_id] = ts_ms

            if gap_flag:
                print(f" -> DATA received (ID:{device_id}, seq={seq}) with GAP.")
            elif duplicate_flag:
                print(f" -> DATA received (ID:{device_id}, seq={seq}) DUPLICATE (Ignored).")
            else:
                print(f" -> DATA received (ID:{device_id}, seq={seq})")
        elif header['msg_type'] == MSG_INIT:
            unit = code_to_unit(header['batch_count'])
            print(f" -> INIT message from Device {device_id} (unit={unit})")
            save_to_csv(csv_data)
            # Also include INIT messages in the timestamp-reordered CSV
            try:
                ts_ms = int(header['timestamp'] * 1000) + int(header['milliseconds'])
            except Exception:
                ts_ms = _now_ms()

            pkt = _Pkt(
                ts_key_ms=ts_ms,
                csv_dict=csv_data,
                dup=bool(duplicate_flag),
                gap=bool(gap_flag)
            )

            _reorder.push(pkt, _now_ms())
            ready = _reorder.flush_ready(_now_ms())
            _save_reordered(ready)

            trackers[device_id].highest_seq = seq
            trackers[device_id].missing_set.clear()
        elif header['msg_type'] == HEART_BEAT:
            print(f" -> HEARTBEAT from Device {device_id}")
            save_to_csv(csv_data)
        else:
            print("Unknown message type.")

except KeyboardInterrupt:
    print("\nServer interrupted. Generating summary...")

    remaining = _reorder.flush_all()
    _save_reordered(remaining)

    total_expected = sum(t.highest_seq for t in trackers.values())
    missing_count = total_expected - received_count
    delivery_rate = (received_count / total_expected) * 100 if total_expected else 0
    print("\n=== Baseline Test Summary ===")
    print(f"Total received: {received_count}")
    print(f"Missing packets: {missing_count}")
    print(f"Duplicate packets: {duplicate_count}")
    print(f"Delivery rate: {delivery_rate:.2f}%")


    if report_intervals_ms:
        report_intervals_ms.sort()
        mid = len(report_intervals_ms) // 2
        if len(report_intervals_ms) % 2 == 1:
            reporting_interval_ms = report_intervals_ms[mid]
        else:
            reporting_interval_ms = (report_intervals_ms[mid-1] + report_intervals_ms[mid]) / 2.0
    else:
        reporting_interval_ms = 0.0


    # ADDED: write metrics.csv (append a single summary row per run)
    MET_CSV = os.path.join(LOG_DIR, "metrics.csv")
    metrics_row = [
        metrics_packets,
        (metrics_bytes / metrics_packets) if metrics_packets else 0.0,
        (metrics_dup_total / metrics_packets) if metrics_packets else 0.0,
        int(metrics_gap_total),
        (metrics_cpu_ms / metrics_packets) if metrics_packets else 0.0,
        reporting_interval_ms,  # PATCH: new column
        time.strftime('%Y-%m-%d %H:%M:%S') # finished_at
    ]
    try:
        need_header = not os.path.exists(MET_CSV)
        with open(MET_CSV, 'a', newline='') as f:
            w = csv.writer(f)
            if need_header:
                w.writerow(["packets_received",
                            "bytes_per_report",
                            "duplicate_rate",
                            "sequence_gap_count",
                            "cpu_ms_per_report",
                            "reporting_interval_ms",  # PATCH: new header
                            "finished_at"])
            w.writerow(metrics_row)
        print(f"\nMetrics appended to: {MET_CSV}")
        print(f"Reordered CSV (timestamp order) written to: {REORDER_CSV}")
    except Exception as e:
        print(f"Failed to write metrics.csv: {e}")
