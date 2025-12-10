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

def save_to_csv(data_dict, is_update=False):
    """
    Saves data to CSV. If is_update is True, it reads the entire CSV, finds 
    the existing row matching the device_id and seq_num, updates the duplicate_flag, 
    and rewrites the file. Otherwise, it appends a new row.
    """
    seq = str(data_dict['seq'])
    device_id = str(data_dict['device_id'])
    
    # Map message type to string
    msg_type = data_dict['msg_type']
    if msg_type == MSG_INIT: msg_type_str = "INIT"
    elif msg_type == MSG_DATA: msg_type_str = "DATA"
    elif msg_type == HEART_BEAT: msg_type_str = "HEARTBEAT"
    else: msg_type_str = f"UNKNOWN({msg_type})"

    # Format the data row
    new_row = [
        data_dict['server_timestamp'],
        device_id,
        str(data_dict['batch_count']),
        seq,
        data_dict['timestamp'],
        msg_type_str,
        data_dict['payload'],
        data_dict['client_address'],
        str(data_dict['delay_seconds']),
        str(data_dict['duplicate_flag']),
        str(data_dict['gap_flag']),
        str(data_dict['packet_size']),
        f"{data_dict['cpu_time_ms']:.4f}"
    ]

    try:
        if is_update:
            # --- REWRITE LOGIC FOR DUPLICATES ---
            rows = []
            row_found = False
            with open(CSV_FILENAME, 'r', newline='') as csvfile:
                reader = csv.reader(csvfile)
                # Read headers
                rows.append(next(reader)) 
                
                # Read data rows
                for row in reader:
                    # Check if this row matches the target packet (device_id is index 1, seq is index 3)
                    if row[1] == device_id and row[3] == seq:
                        row[9] = '1'  # Set duplicate_flag (index 9) to '1'
                        print(f" [!] Updated CSV row for duplicate packet (Device:{device_id}, Seq:{seq})")
                        row_found = True
                    rows.append(row)
            
            if row_found:
                # Rewrite the entire file with the updated row
                with open(CSV_FILENAME, 'w', newline='') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerows(rows)
            else:
                 print(f" [!] Error: Duplicate packet {seq} was detected but its original row was not found in CSV.")

        else:
            # --- APPEND LOGIC for new packets ---
            with open(CSV_FILENAME, 'a', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(new_row)
            # Add to the global dictionary to track it was logged
            print(f" Data saved (seq={seq})")
            
    except Exception as e:
        print(f" Error writing/rewriting to CSV: {e}")

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

        payload = data[HEADER_SIZE:].decode('utf-8', errors='ignore')
        
        device_id = header['device_id']
        seq = header['seq']
        
        if device_id not in trackers:
            trackers[device_id] = DeviceTracker()
            trackers[device_id].highest_seq = seq - 1 

        tracker = trackers[device_id]
        
        duplicate_flag = 0
        gap_flag = 0
        
        # --- LOGIC START ---
        
        diff = seq - tracker.highest_seq

        if diff == 1:
            # CASE 1: In-order packet
            tracker.highest_seq = seq
            # print(f" -> Packet {seq} received in order.")

        elif diff > 1:
            # CASE 2: Gap Detected
            gap_flag = 1
            
            # Identify missing packets
            missing_list = []
            for missing_seq in range(tracker.highest_seq + 1, seq):
                tracker.missing_set.add(missing_seq)
                missing_list.append(str(missing_seq))
            
            print(f" [!] Gap Detected! Missing packets: {missing_list}")
            
            # --- SEND NACK TO CLIENT ---
            if missing_list:
                nack_msg = f"NACK:{device_id}:" + ",".join(missing_list)                
                server_socket.sendto(nack_msg.encode('utf-8'), addr)
                print(f" [<<] Sent NACK request for: {missing_list}")

            tracker.highest_seq = seq

        elif diff <= 0:
            # CASE 3: Out-of-Order or Duplicate
            if seq in tracker.missing_set:
                # Subcase 3a: Recovered Packet (Was missing)
                tracker.missing_set.remove(seq)
                print(f" [+] Recovered packet {seq} (was missing).")
            else:
                # Subcase 3b: True Duplicate -> IGNORE LOGIC
                duplicate_flag = 1
                duplicate_count += 1
                received_count-=1

                print(f" [D] Duplicate detected: {seq}. Content ignored.")
                # [cite_start]We still log it to CSV to verify duplication behavior [cite: 46]
                # But you can choose to discard the payload variable if you want "total ignore"

        # --- LOGIC END ---

        delay = round(time.time() - header['timestamp'], 3)
        received_count += 1
        
        end_cpu = time.process_time()
        cpu_time_ms = (end_cpu - start_cpu) * 1000

        csv_data = {
            'server_timestamp': f" {server_receive_time}",
            'device_id': header['device_id'],
            'batch_count': header['batch_count'],
            'seq': header['seq'],
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

        # Save to CSV (even duplicates, so we can prove they were detected)
        if header['msg_type'] == MSG_DATA:
            if duplicate_flag:
                save_to_csv(csv_data,True)
            else:
                save_to_csv(csv_data)
                
            if gap_flag:
                print(f" -> DATA received (seq={seq}) with GAP.")
            elif duplicate_flag:
                print(f" -> DATA received (seq={seq}) DUPLICATE (Ignored).")
            else:
                print(f" -> DATA received (seq={seq})")
                
        elif header['msg_type'] == MSG_INIT:
            print(f" -> INIT message from Device {device_id}")
            # Reset tracker
            trackers[device_id].highest_seq = seq
            trackers[device_id].missing_set.clear()
            
        elif header['msg_type'] == HEART_BEAT:
            print(f" -> HEARTBEAT from Device {device_id}")
        else:
            print("Unknown message type.")

except KeyboardInterrupt:
    print("\nServer interrupted. Generating summary...")
    total_expected = 0
    if tracker:
        for device in tracker:
            total_expected+=device.highest_seq

    missing_count = total_expected - received_count
    delivery_rate = (received_count / total_expected) * 100 if total_expected else 0

    print("\n=== Baseline Test Summary ===")
    print(f"Total received: {received_count}")
    print(f"Missing packets: {missing_count}")
    print(f"Duplicate packets: {duplicate_count}")
    print(f"Delivery rate: {delivery_rate:.2f}%")