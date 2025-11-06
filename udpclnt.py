import socket
import threading
import time
import os
from protocol import MAX_BYTES, build_header, MSG_INIT, MSG_DATA, HEART_BEAT

SERVER_ADDR = ('localhost', 12000)
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

seq_num = 1
running = True

# --- Send INIT message once at the start ---
header = build_header(device_id=1, batch_count=0, seq_num=seq_num, msg_type=MSG_INIT)
client_socket.sendto(header, SERVER_ADDR)
print(f"→ Sent INIT (seq={seq_num})")
seq_num += 1


def send_heartbeat():
    """
    Periodically send a heartbeat message to the server.
    Runs in a background thread so it doesn't block data sending.
    """
    global seq_num, running
    while running:
        time.sleep(10)  # send every 10 seconds
        header = build_header(device_id=1, batch_count=0, seq_num=seq_num, msg_type=HEART_BEAT)
        client_socket.sendto(header, SERVER_ADDR)
        print(f"→ Sent HEARTBEAT (seq={seq_num})")
        seq_num += 1


# Start heartbeat thread
threading.Thread(target=send_heartbeat, daemon=True).start()

# --- DATA message loop (file input only) ---
if not os.path.exists("sensor_values.txt"):
    print(" sensor_values.txt not found. Please create the file with comma-separated values.")
else:
    print("Reading values from sensor_values.txt...")
    with open("sensor_values.txt") as f:
        for line in f:
            values = [v.strip() for v in line.strip().split(",") if v.strip()]
            if not values:
                continue

            batch_count = len(values)
            payload = ",".join(values).encode('utf-8')

            # Build header with correct sequence number
            header = build_header(device_id=1, batch_count=batch_count, seq_num=seq_num, msg_type=MSG_DATA)

            # Pad packet to MAX_BYTES
            filler = b'\x00' * (MAX_BYTES - len(header) - len(payload))
            packet = header + payload + filler

            # Send packet
            client_socket.sendto(packet, SERVER_ADDR)
            print(f"→ Sent DATA (seq={seq_num}, {batch_count} readings: {values})")
            seq_num += 1

            time.sleep(1)  # send one line per second

# Cleanup
running = False
client_socket.close()
print("Client finished.")