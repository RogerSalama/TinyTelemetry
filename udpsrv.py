import socket
import time
import csv
import os
from datetime import datetime
from protocol import MAX_BYTES, HEADER_SIZE, parse_header, MSG_INIT, MSG_DATA, HEART_BEAT

SERVER_PORT = 12000
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.bind(('', SERVER_PORT))
print(f"UDP Server running on port {SERVER_PORT} (max {MAX_BYTES} bytes)")

# CSV Configuration
CSV_FILENAME = "iot_device_data.csv"
CSV_HEADERS = [
    "server_timestamp", "device_id", "batch_count", "sequence_number", 
    "device_timestamp", "message_type", "milliseconds", "payload",
    "client_address"
]

def init_csv_file():
    """Initialize CSV file with headers if it doesn't exist"""
    if not os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(CSV_HEADERS)
        print(f"Created new CSV file: {CSV_FILENAME}")

def save_to_csv(data_dict):
    """Save data to CSV file"""
    try:
        with open(CSV_FILENAME, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            
            # Convert message type to readable string
            msg_type = data_dict['msg_type']
            if msg_type == MSG_INIT:
                msg_type_str = "INIT"
            elif msg_type == MSG_DATA:
                msg_type_str = "DATA"
            elif msg_type == HEART_BEAT:
                msg_type_str = "HEARTBEAT"
            else:
                msg_type_str = f"UNKNOWN({msg_type})"
            
            # Prepare row data
            row = [
                data_dict['server_timestamp'],  # When server received it
                data_dict['device_id'],
                data_dict['batch_count'],
                data_dict['seq'],
                data_dict['timestamp'],
                msg_type_str,
                data_dict['milliseconds'],
                data_dict['payload'],
                data_dict['client_address']
            ]
            
            writer.writerow(row)
            print(f"✓ Data saved to {CSV_FILENAME}")
            
    except Exception as e:
        print(f"✗ Error saving to CSV: {e}")


init_csv_file()

device_state = {}


while True:
    data, addr = server_socket.recvfrom(MAX_BYTES)
    server_receive_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\nReceived {len(data)} bytes from {addr}")

    try:
        header = parse_header(data)
    except ValueError as e:
        print("Header error:", e)
        continue

    print(f"Device ID: {header['device_id']}")
    print(f"Batch Count (# of readings): {header['batch_count']}")
    print(f"Seq Num: {header['seq']}")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(header['timestamp']))}")
    print(f"Message Type: {header['msg_type']}")
    print(f"Milliseconds: {header['milliseconds']}")

    payload = data[HEADER_SIZE:].rstrip(b'\x00')
    payload_str=payload.decode('utf-8', errors='ignore')

    if payload:
        print("Payload (message):", payload_str)
    else:
        print("No payload.")

    csv_data = {
        'server_timestamp': server_receive_time,
        'device_id': header['device_id'],
        'batch_count': header['batch_count'],
        'seq': header['seq'],
        'timestamp': f" {time.strftime('%d/%m/%Y  %I:%M:%S', time.localtime(header['timestamp']))}.{header['milliseconds']:03d}",
        'msg_type': header['msg_type'],
        'milliseconds': header['milliseconds'],
        'payload': payload_str,
        'client_address': f"{addr[0]}:{addr[1]}"
    }

# --- Duplicate & Gap Detection ---

    device_id = header['device_id']
    seq = header['seq']
    duplicate_flag = 0
    gap_flag = 0

    if device_id in device_state:
        last_seq = device_state[device_id]
        if seq == last_seq:
            duplicate_flag = 1
        elif seq > last_seq + 1:
            gap_flag = 1

    device_state[device_id] = seq  # update last seen sequence

    csv_data = {
        'server_timestamp': server_receive_time,
        'device_id': header['device_id'],
        'batch_count': header['batch_count'],
        'seq': header['seq'],
        'timestamp': f"{time.strftime('%d/%m/%Y  %I:%M:%S', time.localtime(header['timestamp']))}.{header['milliseconds']:03d}",
        'msg_type': header['msg_type'],
        'milliseconds': header['milliseconds'],
        'payload': payload_str,
        'client_address': f"{addr[0]}:{addr[1]}",
        'duplicate_flag': duplicate_flag,
        'gap_flag': gap_flag
    }

    #  Only save DATA messages in csv files
    if header['msg_type'] == MSG_DATA:
        save_to_csv(csv_data)
        print(f"→ DATA message received with {header['batch_count']} readings.")
    elif header['msg_type'] == MSG_INIT:
        print("→ INIT message received (not saved to CSV).")
    elif header['msg_type'] == HEART_BEAT:
        print(f"→ HEARTBEAT received from Device {header['device_id']} at {time.ctime(header['timestamp'])} (not saved to CSV).")
    else:
        print("Unknown message type.")
