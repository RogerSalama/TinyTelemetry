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

# --- Real-time logging ---
sys.stdout.reconfigure(line_buffering=True)
SERVER_ID = 1

# --- Server setup ---
SERVER_PORT = 12002
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(('', SERVER_PORT))
print(f"UDP Server running on port {SERVER_PORT} (max {MAX_BYTES} bytes)")

NACK_DELAY_SECONDS = 0.1
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
    """Ensure CSV always has headers at the top, even if file exists."""
    rows = []
    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, 'r', newline='') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)
    with open(CSV_FILENAME, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(CSV_HEADERS)
        if rows:
            if rows[0] == CSV_HEADERS:
                rows = rows[1:]
            writer.writerows(rows)
    print(f"CSV initialized with headers (file: {CSV_FILENAME})")

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

    # --- Base64 encode payload ---
    payload = data_dict['payload']
    if isinstance(payload, str):
        payload_bytes = payload.encode('utf-8', errors='ignore')
    else:
        payload_bytes = payload
    payload_b64 = base64.b64encode(payload_bytes).decode('ascii')

    new_row = [
        data_dict['server_timestamp'],
        device_id,
        code_to_unit(data_dict['batch_count']) if msg_type == MSG_INIT else (data_dict['batch_count'] if msg_type == MSG_DATA else ""),
        seq,
        data_dict['timestamp'],
        msg_type_str,
        payload_b64,
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
            with open(CSV_FILENAME, 'r', newline='') as csvfile:
                reader = csv.reader(csvfile)
                rows.append(next(reader))
                for row in reader:
                    if row[1] == device_id and row[3] == seq:
                        row[9] = '1'
                        print(f" [!] Updated CSV row for duplicate packet (Device:{device_id}, Seq:{seq})")
                        row_found = True
                    rows.append(row)
            if row_found:
                with open(CSV_FILENAME, 'w', newline='') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerows(rows)
            else:
                print(f" [!] Error: Duplicate packet (ID:{device_id}, seq:{seq}) was detected but not found in CSV.")
        else:
            with open(CSV_FILENAME, 'a', newline='') as csvfile:
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

# --------------------------------------------------------------------
#                 ADDED: timestamp reordering + metrics (CSV)
# --------------------------------------------------------------------
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
        self.heap = []          # holds (pkt, arrival_ms)
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

def _save_reordered(pkt_list):  # ADDED
    """Append flushed packets to the reordered CSV with the same columns."""
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
            # Original CSV already base64 encodes the payload in save_to_csv(),
            # but here we write a comparable row for analysis.
            if isinstance(payload, str):
                payload_bytes = payload.encode('utf-8', errors='ignore')
            else:
                payload_bytes = payload
            payload_b64 = base64.b64encode(payload_bytes).decode('ascii')
            writer.writerow([
                d['server_timestamp'],
                d['device_id'],
                code_to_unit(d['batch_count']) if msg_type == MSG_INIT else (d['batch_count'] if msg_type == MSG_DATA else ""),
                d['seq'],
                d['timestamp'],
                msg_type_str,
                payload_b64,
                d['client_address'],
                d['delay_seconds'],
                1 if p.dup else 0,
                1 if p.gap else 0,
                d['packet_size'],
                f"{d['cpu_time_ms']:.4f}",
            ])

# ADDED: metrics accumulators (aggregate for metrics.csv)
metrics_packets = 0
metrics_bytes = 0
metrics_cpu_ms = 0.0
metrics_dup_total = 0
metrics_gap_total = 0

_reorder = _ReorderBuffer(guard_ms=150, max_buffer_ms=1000)  # ADDED

def _now_ms():  # ADDED
    return int(time.time() * 1000)
# --------------------------------------------------------------------

# --- Initialize ---
init_csv_file()
trackers = {}
received_count = 0
duplicate_count = 0
corruption_count = 0

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
            num = header['batch_count']
            expected_len = num * 8
            if len(payload_bytes) >= expected_len and num > 0:
                try:
                    enc = payload_bytes[:expected_len]
                    dec = decrypt_bytes(enc, header['device_id'], header['seq'])
                    fmt = '!' + ('d' * num)     
                    values = list(struct.unpack(fmt, dec))
                    payload = ",".join(f"{v:.6f}" for v in values)
                except Exception:
                    payload = payload_bytes
            else:
                payload = payload_bytes
        else:
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
            # ADDED: accumulate the gap count for metrics
            metrics_gap_total += (seq - tracker.highest_seq - 1)
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
                metrics_dup_total += 1
                received_count -= 1
                print(f" [D] Duplicate detected: ID:{device_id}, seq:{seq}. Content ignored.")

        delay = round(time.time() - header['timestamp'], 3)
        received_count += 1

        end_cpu = time.perf_counter()
        cpu_time_ms = (end_cpu - start_cpu) * 1000

        # ADDED: accumulate metrics
        metrics_packets += 1
        metrics_bytes += len(data)
        metrics_cpu_ms += cpu_time_ms

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
            trackers[device_id].highest_seq = seq
            trackers[device_id].missing_set.clear()
        elif header['msg_type'] == HEART_BEAT:
            print(f" -> HEARTBEAT from Device {device_id}")
            save_to_csv(csv_data)
        else:
            print("Unknown message type.")

        # -------------------- ADDED: push to reorder buffer and flush --------------------
        ts_key_ms = int(header['timestamp'] * 1000 + header['milliseconds'])
        pkt = _Pkt(ts_key_ms=ts_key_ms, csv_dict=csv_data, dup=duplicate_flag, gap=gap_flag)
        _reorder.push(pkt, _now_ms())
        flushed = _reorder.flush_ready(_now_ms())
        _save_reordered(flushed)
        # -------------------------------------------------------------------------------

except KeyboardInterrupt:
    print("\nServer interrupted. Generating summary...")

    # ADDED: final flush of reorder buffer and save
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

    # ADDED: write metrics.csv (append a single summary row per run)
    MET_CSV = os.path.join(LOG_DIR, "metrics.csv")
    metrics_row = [
        metrics_packets,
        (metrics_bytes / metrics_packets) if metrics_packets else 0.0,
        (metrics_dup_total / metrics_packets) if metrics_packets else 0.0,
        int(metrics_gap_total),
        (metrics_cpu_ms / metrics_packets) if metrics_packets else 0.0,
        time.strftime('%Y-%m-%d %H:%M:%S')  # finished_at
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
                            "finished_at"])
            w.writerow(metrics_row)
        print(f"\nMetrics appended to: {MET_CSV}")
        print(f"Reordered CSV (timestamp order) written to: {REORDER_CSV}")
    except Exception as e:
        print(f"Failed to write metrics.csv: {e}")
