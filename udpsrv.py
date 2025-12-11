import socket
import time
import csv
import os
import sys
from datetime import datetime
import struct
from protocol import MAX_BYTES, HEADER_SIZE, parse_header, MSG_INIT, MSG_DATA, HEART_BEAT, code_to_unit, build_header, NACK_MSG, decrypt_bytes

# --- Real-time logging ---
sys.stdout.reconfigure(line_buffering=True)
SERVER_ID=1
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
    "server_timestamp", "device_id", "unit/batch_count", "sequence_number",
    "device_timestamp", "message_type", "payload",
    "client_address", "delay_seconds", "duplicate_flag", "gap_flag", 
    "packet_size", "cpu_time_ms"
]    
def init_csv_file():
    """Ensure CSV always has headers at the top, even if file already exists."""
    rows = []

    # If the file exists, read all existing rows
    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, 'r', newline='') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)  # read all rows

    # Always overwrite the file with header + existing rows
    with open(CSV_FILENAME, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(CSV_HEADERS)  # write header first
        if rows:
            # Append the old data, but skip the old header if it exists
            if rows[0] == CSV_HEADERS:
                rows = rows[1:]  # remove old header
            writer.writerows(rows)

    print(f"CSV initialized with headers (file: {CSV_FILENAME})")


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
        code_to_unit(data_dict['batch_count']) if msg_type == MSG_INIT else (data_dict['batch_count'] if msg_type == MSG_DATA else ""),
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
                 print(f" [!] Error: Duplicate packet (ID:{device_id}, seq:{seq}) was detected but its original row was not found in CSV.")

        else:
            # --- APPEND LOGIC for new packets ---
            with open(CSV_FILENAME, 'a', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(new_row)
            # Add to the global dictionary to track it was logged
            print(f" Data saved (ID:{device_id}, seq={seq})")
            
    except Exception as e:
        print(f" Error writing/rewriting to CSV: {e}")

server_seq = 1
def send_NACK(device_id, addr, missing_seq):
    global server_seq
    print(f" [!] Gap Detected! ID:{device_id}, Missing packets: {missing_seq}")
    srv_header = build_header(device_id=SERVER_ID, batch_count=1, seq_num=server_seq, msg_type = NACK_MSG)
    server_seq+=1
    srv_payload = str(device_id) +":"+ str(missing_seq)
    Nack_packet=srv_header+srv_payload.encode('utf-8')

    server_socket.sendto(Nack_packet, addr)
    print(f" [<<] Sent NACK request for ID:{device_id}, seq: {missing_seq}")

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
        
        start_cpu = time.perf_counter()
        server_receive_time = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        try:
            header = parse_header(data)
        except ValueError as e:
            print("Header error:", e)
            continue

        payload_bytes = data[HEADER_SIZE:]

        # For DATA messages, payload is binary doubles (8 bytes each)
        if header['msg_type'] == MSG_DATA:
            num = header['batch_count']
            expected_len = num * 8
            if len(payload_bytes) >= expected_len and num > 0:
                try:
                    # Decrypt first (XOR stream). This preserves the exact
                    # binary layout so struct.unpack works as before.
                    enc = payload_bytes[:expected_len]
                    dec = decrypt_bytes(enc, header['device_id'], header['seq'])
                    fmt = '!' + ('d' * num)
                    values = list(struct.unpack(fmt, dec))
                    # Format values to fixed decimal string for CSV/logging
                    payload = ",".join(f"{v:.6f}" for v in values)
                except Exception as e:
                    payload = payload_bytes.decode('utf-8', errors='ignore')
            else:
                # not enough bytes — fallback to text
                payload = payload_bytes.decode('utf-8', errors='ignore')
        else:
            # INIT or HEARTBEAT: treat payload as text (usually empty)
            payload = payload_bytes.decode('utf-8', errors='ignore')
        
        device_id = header['device_id']
        seq = header['seq']
        
        if (device_id not in trackers):
            if header['msg_type']==MSG_INIT:
                trackers[device_id] = DeviceTracker()
                trackers[device_id].highest_seq = seq - 1 
            else:
                print(f" [!] Gap Detected! ID:{device_id}, Missing packets: 1")

                send_NACK(device_id=device_id,addr=addr,missing_seq=1)
                continue


        tracker = trackers[device_id]

        duplicate_flag = 0
        gap_flag = 0
        
        # --- LOGIC START ---
        
        diff = seq - tracker.highest_seq
        if seq==0 :
            # Special case for seq=0 (heartbeat)
            print("Heartbeat Received")
        
        elif diff == 1:
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
                send_NACK(device_id=device_id,addr=addr,missing_seq=missing_seq)
                #missing_list.append(str(missing_seq))
            
            
            # --- SEND NACK TO CLIENT ---
            # if missing_list:
            #     nack_msg = f"NACK:{device_id}:" + ",".join(missing_list)
            #     server_socket.sendto(nack_msg.encode('utf-8'), addr)
            #     print(f" [<<] Sent NACK request for ID:{device_id}, seq: {missing_list}")

            tracker.highest_seq = seq

        elif diff <= 0:
            # CASE 3: Out-of-Order or Duplicate
            if seq in tracker.missing_set:
                # Subcase 3a: Recovered Packet (Was missing)
                tracker.missing_set.remove(seq)
                print(f" [+] Recovered packet ID:{device_id}, seq:{seq} (was missing).")
            else:
                # Subcase 3b: True Duplicate -> IGNORE LOGIC
                duplicate_flag = 1
                duplicate_count += 1
                received_count-=1

                print(f" [D] Duplicate detected: ID:{device_id}, seq:{seq}. Content ignored.")
                # [cite_start]We still log it to CSV to verify duplication behavior [cite: 46]
                # But you can choose to discard the payload variable if you want "total ignore"

        # --- LOGIC END ---

        delay = round(time.time() - header['timestamp'], 3)
        received_count += 1
        
        end_cpu = time.perf_counter()
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
                print(f" -> DATA received (ID:{device_id}, seq={seq}) with GAP.")
            elif duplicate_flag:
                print(f" -> DATA received (ID:{device_id}, seq={seq}) DUPLICATE (Ignored).")
            else:
                print(f" -> DATA received (ID:{device_id}, seq={seq})")
                 
        elif header['msg_type'] == MSG_INIT:
            unit = code_to_unit(header['batch_count'])
            print(f" -> INIT message from Device {device_id} (unit={unit})")
            save_to_csv(csv_data)
            # Reset tracker
            trackers[device_id].highest_seq = seq
            trackers[device_id].missing_set.clear()
            
        elif header['msg_type'] == HEART_BEAT:
            print(f" -> HEARTBEAT from Device {device_id}")
            save_to_csv(csv_data)
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
    


