import socket
import threading
import time
import os
import sys
from protocol import MAX_BYTES, build_header, MSG_INIT, MSG_DATA, HEART_BEAT, unit_to_code, parse_header, NACK_MSG, HEADER_SIZE

# Configurable defaults
DEFAULT_INTERVAL_DURATION = 20  # total test duration = 60s * 3 intervals
DEFAULT_INTERVALS = [1, 5, 30]
sensors = []

# Parse command-line arguments
if len(sys.argv) > 1:
    try:
        Interval_Duration = int(sys.argv[1])
    except ValueError:
        Interval_Duration = DEFAULT_INTERVAL_DURATION
else:
    Interval_Duration = DEFAULT_INTERVAL_DURATION

if len(sys.argv) > 2:
    try:
        intervals = [int(x) for x in sys.argv[2].split(",")]
    except ValueError:
        intervals = DEFAULT_INTERVALS
else:
    intervals = DEFAULT_INTERVALS

SERVER_ADDR = ('localhost', 12000)
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

running = True

# HISTORY STORAGE (Key is now (device_id, seq_num) tuple)
sent_history = {} 

# Send INIT message once
# header = build_header(device_id=MY_DEVICE_ID, batch_count=0, seq_num=seq_num, msg_type=MSG_INIT)
# client_socket.sendto(header, SERVER_ADDR)
# sent_history[(MY_DEVICE_ID, seq_num)] = header # Store with device ID
# print(f"Sent INIT (Device={MY_DEVICE_ID}, seq={seq_num})")
# seq_num += 1

def send_heartbeat():
    """Send heartbeat messages every 1 second from all devices."""
    global running
    while running:
        time.sleep(10)
        # time.sleep(1)
        #sending heartbeat message for all sensors in text file
        for sensor in sensors:
            header = build_header(device_id=sensor['device_id'], batch_count=0, seq_num=0, msg_type=HEART_BEAT)
            client_socket.sendto(header, SERVER_ADDR)
            print(f"Sent HEARTBEAT for Device {sensor['device_id']} (seq={sensor['seq_num']})")
            # sensor['seq_num'] += 1
        # header = build_header(device_id=MY_DEVICE_ID, batch_count=0, seq_num=seq_num, msg_type=HEART_BEAT)
        # client_socket.sendto(header, SERVER_ADDR)
        # sent_history[(MY_DEVICE_ID, seq_num)] = header # Store with device ID
        # print(f"Sent HEARTBEAT (Device={MY_DEVICE_ID}, seq={seq_num})")
        # seq_num += 1

def receive_nacks():
    """Thread to listen for NACK messages from server."""
    global running
    # Optional: set a short timeout so recvfrom can exit periodically
    try:
        client_socket.settimeout(1.0)
    except Exception:
        pass  # ignore if not supported
    # client_socket.settimeout(1.0) # non-blocking with timeout
    while running:
        try:
            # Data received is a bytes object
            data, addr = client_socket.recvfrom(1200) 
            
            # Parse the header (which requires bytes)
            header = parse_header(data)
            
            # The payload is the part of the bytes object after the header
            payload_bytes = data[HEADER_SIZE:]
            payload_str = payload_bytes.decode('utf-8', errors='ignore').strip()

            if header['msg_type'] == NACK_MSG:
                # Server sends one NACK packet per missing sequence in the format: "DEVICE_ID:MISSING_SEQ"
                parts = payload_str.split(":") 
                
                if len(parts) == 2:
                    try:
                        # 1. FIX: Convert both parts to integers.
                        nack_device_id = int(parts[0])
                        missing_seq = int(parts[1]) # Server sends a single sequence number
                    except ValueError:
                        print(f" [x] Failed to parse NACK payload into integers: {payload_str}")
                        continue
                else:
                    # The original check here was too broad. Now we know exactly why it's malformed.
                    print(f" [x] Received malformed NACK payload format: {payload_str}")
                    continue

                print(f"\n [!] Received NACK for Device {nack_device_id}, seq: {missing_seq}")
                
                # 2. FIX: Use the single integer sequence number to look up the packet
                history_key = (nack_device_id, missing_seq)
                if history_key in sent_history:
                    packet = sent_history[history_key]
                    client_socket.sendto(packet, SERVER_ADDR)
                    print(f" [>>] Retransmitting DATA seq={missing_seq}")
                else:
                    print(f" [x] Cannot retransmit seq={missing_seq} (not in history or INIT/HEARTBEAT)")

                # 3. FIX: Handle re-INIT NACK (seq=1) - logic cleaned up
                if missing_seq == 1:
                    print(f" [^] Server requested re-INIT for Device {nack_device_id}.")
                    for sensor in sensors:
                        if sensor['device_id'] == nack_device_id:
                            # We can simply resend the INIT packet (seq=1)
                            init_header = build_header(
                                device_id=sensor['device_id'], 
                                batch_count=sensor['unit_code'], 
                                seq_num=1, 
                                msg_type=MSG_INIT
                            )
                            client_socket.sendto(init_header, SERVER_ADDR)
                            # Remove state reset that would cause duplicate seq numbers (seq_num=1, current_index=0)
                            # The client should continue sending data packets starting from its current seq_num
                            print(f" [>>] Sent re-INIT (seq=1, unit={sensor['unit']})")
                            time.sleep(0.1) 
                            break 

        except socket.timeout:
            continue
        except OSError as e:
            # Handle Windows-specific socket closure errors (WinError 10022)
            if not running:
                break
            print(f"Receiver thread OSError: {e}")
            time.sleep(0.1)
            continue
        except Exception as e:
            if running:
                print(f"Error in receiver thread: {e}")
            time.sleep(0.1)

# Start background threads
# threading.Thread(target=send_heartbeat, daemon=True).start()
threading.Thread(target=receive_nacks, daemon=True).start()

# Load sensor data
if not os.path.exists("sensor_values.txt"):
    print("sensor_values.txt not found. Please create it with comma-separated values.")
    running = False
else:
    with open("sensor_values.txt") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        print("sensor_values.txt is empty.")
        running = False
    else:
        for line in lines:
            parts = [v.strip() for v in line.split(",") if v.strip()]
            device_id = int(parts[0])
            unit = parts[1]
            readings = parts[2:]
            sensors.append({'device_id': device_id, 'unit': unit, 'readings': readings, 'unit_code': unit_to_code(unit), 'current_index': 0, 'seq_num': 1})
         # sending init message for all sensoors in text file
        for sensor in sensors:
           
            header = build_header(device_id=sensor['device_id'], batch_count=sensor['unit_code'], seq_num=sensor['seq_num'], msg_type=MSG_INIT)
            client_socket.sendto(header, SERVER_ADDR)
            print(f"Sent INIT (seq={sensor['seq_num']}, unit={sensor['unit']})")
            sensor['seq_num'] += 1
            time.sleep(1)

        # Start heartbeat thread
        threading.Thread(target=send_heartbeat, daemon=True).start()

        print(f"Starting test for intervals {intervals} ({Interval_Duration}s each)...")

        for interval in intervals:
            print(f"\n--- Running {interval}s interval for {Interval_Duration} seconds ---")
            start_interval = time.time()
            while time.time() - start_interval < Interval_Duration:
                for sensor in sensors:
                    start = sensor['current_index']
                    if start < len(sensor['readings']):
                        chunk = sensor['readings'][start:start+10]
                        batch_count = len(chunk)
                        payload = ",".join(chunk).encode("utf-8")

                        header = build_header(device_id=sensor['device_id'], batch_count=batch_count,
                                              seq_num=sensor['seq_num'], msg_type=MSG_DATA)
                       
                        packet = header + payload 
                        sent_history[(sensor['device_id'], sensor['seq_num'])] = packet

                        client_socket.sendto(packet, SERVER_ADDR)
                        print(f"Sent DATA seq={sensor['seq_num']}, interval={interval}s, readings={chunk}")
                        sensor['seq_num'] += 1
                        sensor['current_index'] += 10
                        time.sleep(0.1)  # Small delay between packets
                time.sleep(interval)

print("Test finished. Closing client...")
running = False
client_socket.close()
print("Client finished.")
