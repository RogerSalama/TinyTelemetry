import socket
import time
import csv
import os
import sys
from datetime import datetime
from protocol import MAX_BYTES, HEADER_SIZE, parse_header, MSG_INIT, MSG_DATA, HEART_BEAT, code_to_unit

# --- Real-time logging ---
sys.stdout.reconfigure(line_buffering=True)

# --- Server setup ---
SERVER_PORT = 12002
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(('', SERVER_PORT))
print(f"UDP Server running on port {SERVER_PORT} (max {MAX_BYTES} bytes)")

# --- CSV Configuration ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

CSV_FILENAME = os.path.join(LOG_DIR, "iot_device_data.csv")
CSV_HEADERS = [
    "server_timestamp", "device_id", "unit", "sequence_number",
    "device_timestamp", "message_type", "milliseconds", "payload",
    "client_address", "delay_seconds", "duplicate_flag", "gap_flag"
]

#  Reordering buffer + metrics

def _now_ms():
    return int(time.time() * 1000)

class _Pkt:
   
    __slots__ = ("device_id","seq","ts_key_ms","server_timestamp_str","unit_field",
                 "device_ts_str","msg_type","milliseconds","payload_str",
                 "client_addr_str","delay_seconds","raw_len")
    def __init__(self, device_id, seq, ts_key_ms, server_timestamp_str, unit_field,
                 device_ts_str, msg_type, milliseconds, payload_str, client_addr_str,
                 delay_seconds, raw_len):
        self.device_id = device_id
        self.seq = seq
        self.ts_key_ms = ts_key_ms
        self.server_timestamp_str = server_timestamp_str
        self.unit_field = unit_field
        self.device_ts_str = device_ts_str
        self.msg_type = msg_type
        self.milliseconds = milliseconds
        self.payload_str = payload_str
        self.client_addr_str = client_addr_str
        self.delay_seconds = delay_seconds
        self.raw_len = raw_len

    def __lt__(self, other):
        # Order strictly by the device/sensor timestamp
        return self.ts_key_ms < other.ts_key_ms

class _ReorderBuffer:
    
    def __init__(self, jitter_guard_ms=150, max_buffer_ms=1000):
        import heapq
        self._heapq = heapq
        self.heap = []
        self.max_seen_ts = 0
        self.jitter_guard_ms = jitter_guard_ms
        self.max_buffer_ms = max_buffer_ms

    def push(self, pkt: _Pkt, arrival_ms: int):
        if pkt.ts_key_ms > self.max_seen_ts:
            self.max_seen_ts = pkt.ts_key_ms
        self._heapq.heappush(self.heap, (pkt, arrival_ms))

    def flush_ready(self, now_ms: int):
        ready = []
        watermark = self.max_seen_ts - self.jitter_guard_ms
        while self.heap:
            top_pkt, top_arrival = self.heap[0]
            if top_pkt.ts_key_ms <= watermark or (now_ms - top_arrival) >= self.max_buffer_ms:
                ready.append(self._heapq.heappop(self.heap)[0])
            else:
                break
        return ready

    def flush_all(self):
        out = []
        while self.heap:
            out.append(self.heap.pop(0)[0])  
        out.sort(key=lambda p: p.ts_key_ms)
        return out

# Duplicate/gap state tracking (per device)
device_state = {}        # device_id -> last_seq
_dup_count_map = {}      # device_id -> duplicates observed (for rate)
_gap_count_map = {}      # device_id -> sum of missing sequences

def _update_seq(device_id: int, seq: int):
    """
    Returns (duplicate_flag, gap_flag) and updates state maps.
    """
    duplicate_flag = 0
    gap_flag = 0
    last_seq = device_state.get(device_id, None)
    if last_seq is not None:
        if seq == last_seq:
            duplicate_flag = 1
            _dup_count_map[device_id] = _dup_count_map.get(device_id, 0) + 1
        elif seq > last_seq + 1:
            gap_flag = 1
            _gap_count_map[device_id] = _gap_count_map.get(device_id, 0) + (seq - last_seq - 1)
    device_state[device_id] = seq
    return duplicate_flag, gap_flag

# Metrics accumulators 
received_count = 0
duplicate_count = 0
all_sequences = {}       # device_id -> set of seqs
_bytes_sum = 0
_cpu_ms_sum = 0.0        # using process_time() as CPU measure
_reorder = _ReorderBuffer(jitter_guard_ms=150, max_buffer_ms=1000)

def _metrics_summary():
    total_reports = max(1, received_count)
    seq_gap_total = sum(_gap_count_map.values()) if _gap_count_map else 0
    dup_total = sum(_dup_count_map.values()) if _dup_count_map else 0
    return {
        "packets_received": received_count,
        "bytes_per_report": _bytes_sum / total_reports,
        "duplicate_rate": dup_total / total_reports,
        "sequence_gap_count": int(seq_gap_total),
        "cpu_ms_per_report": _cpu_ms_sum / total_reports
    }


def init_csv_file():
    """Initialize CSV file with headers if it doesn't exist or is empty."""
    needs_header = False
    if not os.path.exists(CSV_FILENAME):
        needs_header = True
    elif os.path.getsize(CSV_FILENAME) == 0:
        needs_header = True

    if needs_header:
        with open(CSV_FILENAME, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(CSV_HEADERS)
        print(f"Created new CSV file with headers: {CSV_FILENAME}")
    else:
        print(f"Appending to existing CSV: {CSV_FILENAME}")

def save_to_csv_row(writer, pkt: _Pkt, duplicate_flag: int, gap_flag: int):
    """Write one ordered row to CSV using your original columns."""
    msg_type = pkt.msg_type
    if msg_type == MSG_INIT:
        msg_type_str = "INIT"
    elif msg_type == MSG_DATA:
        msg_type_str = "DATA"
    elif msg_type == HEART_BEAT:
        msg_type_str = "HEARTBEAT"
    else:
        msg_type_str = f"UNKNOWN({msg_type})"

    row = [
        pkt.server_timestamp_str,            # server_timestamp
        pkt.device_id,                       # device_id
        pkt.unit_field if msg_type == MSG_INIT else (pkt.unit_field if msg_type == MSG_DATA else ""),  # unit / batch_count
        pkt.seq,                             # sequence_number
        pkt.device_ts_str,                   # device_timestamp
        msg_type_str,                        # message_type
        pkt.milliseconds,                    # milliseconds
        pkt.payload_str,                     # payload
        pkt.client_addr_str,                 # client_address
        pkt.delay_seconds,                   # delay_seconds
        duplicate_flag,                      # duplicate_flag
        gap_flag                             # gap_flag
    ]
    writer.writerow(row)
    print(f"✓ Data saved (seq={pkt.seq})")

# Initialize CSV and run
init_csv_file()

try:
    # Open CSV once in append mode; use a writer for ordered flushes
    csv_file = open(CSV_FILENAME, 'a', newline='')
    csv_writer = csv.writer(csv_file)

    print("Collector ready; buffering for timestamp-based reordering (jitter guard = 150 ms).")

    while True:
        # Blocking receive
        data, addr = server_socket.recvfrom(MAX_BYTES)
        server_receive_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Parse header 
        try:
            header = parse_header(data)
        except ValueError as e:
            print("Header error:", e)
            continue

        # Payload parse 
        payload = data[HEADER_SIZE:].rstrip(b'\x00').decode('utf-8', errors='ignore')

        device_id = header['device_id']
        seq = header['seq']

        # Delay calculation 
        delay = round(time.time() - header['timestamp'], 3)

        # Tracking
        received_count += 1
        _bytes_sum += len(data)
        if device_id not in all_sequences:
            all_sequences[device_id] = set()
        if seq in all_sequences[device_id]:
            duplicate_count += 1
        all_sequences[device_id].add(seq)

        device_ts_str = f"{time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(header['timestamp']))}.{header['milliseconds']:03d}"
        unit_field = code_to_unit(header['batch_count']) if header['msg_type'] == MSG_INIT else header['batch_count']
        client_addr_str = f"{addr[0]}:{addr[1]}"

        # Sensor timestamp key in ms 
        ts_key_ms = int(header['timestamp'] * 1000 + header['milliseconds'])

        pkt = _Pkt(
            device_id=device_id,
            seq=seq,
            ts_key_ms=ts_key_ms,
            server_timestamp_str=server_receive_time,
            unit_field=unit_field,
            device_ts_str=device_ts_str,
            msg_type=header['msg_type'],
            milliseconds=header['milliseconds'],
            payload_str=payload,
            client_addr_str=client_addr_str,
            delay_seconds=delay,
            raw_len=len(data)
        )

       
        arrival_ms = _now_ms()
        cpu_t0 = time.process_time()

        _reorder.push(pkt, arrival_ms)

        # Flush ready packets in timestamp order
        for out_pkt in _reorder.flush_ready(_now_ms()):
            dup_flag, gap_flag = _update_seq(out_pkt.device_id, out_pkt.seq)
            save_to_csv_row(csv_writer, out_pkt, dup_flag, gap_flag)

        cpu_t1 = time.process_time()
        _cpu_ms_sum += (cpu_t1 - cpu_t0) * 1000.0

        # --- Message prints ---
        if header['msg_type'] == MSG_DATA:
            print(f"→ DATA message received (seq={seq}, delay={delay}s)")
        elif header['msg_type'] == MSG_INIT:
            unit = code_to_unit(header['batch_count'])
            print(f"→ INIT message from Device {device_id} (unit={unit})")
        elif header['msg_type'] == HEART_BEAT:
            print(f"→ HEARTBEAT from Device {device_id} at {time.ctime(header['timestamp'])}")
        else:
            print("Unknown message type.")

except KeyboardInterrupt:
    print("\nServer interrupted. Flushing buffer and generating summary...")

    # Final flush of any remaining packets
    try:
        # Reopen writer if needed
        if 'csv_file' not in locals() or csv_file.closed:
            csv_file = open(CSV_FILENAME, 'a', newline='')
            csv_writer = csv.writer(csv_file)
        for out_pkt in _reorder.flush_all():
            dup_flag, gap_flag = _update_seq(out_pkt.device_id, out_pkt.seq)
            save_to_csv_row(csv_writer, out_pkt, dup_flag, gap_flag)
    finally:
        try:
            csv_file.flush()
            csv_file.close()
        except Exception:
            pass

    total_expected = max(max(seqs) for seqs in all_sequences.values()) if all_sequences else 0
    missing_count = total_expected - received_count
    delivery_rate = (received_count / total_expected) * 100 if total_expected else 0

    print("\n=== Baseline Test Summary ===")
    print(f"Total received: {received_count}")
    print(f"Missing packets: {missing_count}")
    print(f"Duplicate packets (raw recv duplicates): {duplicate_count}")
    print(f"Delivery rate: {delivery_rate:.2f}%")

    #Metrics summary per spec + write metrics.json next to CSV
    metrics = _metrics_summary()
    print("\n=== Metrics Summary (spec-required) ===")
    for k, v in metrics.items():
        print(f"{k}: {v}")

    try:
        metrics_path = os.path.join(LOG_DIR, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            import json
            json.dump(metrics, f, indent=2)
        print(f"\nMetrics written to: {metrics_path}")
    except Exception as e:
        print(f"Failed to write metrics.json: {e}")
