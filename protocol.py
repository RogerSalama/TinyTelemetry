import struct
import time

MAX_BITS = 1600
MAX_BYTES = MAX_BITS // 8  # 200 bytes total
HEADER_SIZE = 9  

# Message types
MSG_INIT = 0
MSG_DATA = 1
HEART_BEAT = 2


def build_header(device_id, batch_count, seq_num, msg_type):
    """
    Build a 9-byte header for the UDP IoT protocol (no custom fields).
    """
    timestamp = int(time.time())
    proto_version = 1
    ms = int((time.time() * 1000) % 1000)  # 0–999 ms
    ms_high = (ms >> 8) & 0x03
    ms_low = ms & 0xFF

    # Byte 1: upper 4 bits device ID, lower 4 bits batch count
    byte1 = ((device_id & 0x0F) << 4) | (batch_count & 0x0F)

    # Byte 8: 2 bits protocol version + 2 bits message type + 00 + 2 bits ms_high
    byte8 = ((proto_version & 0x03) << 6) | ((msg_type & 0x03) << 4) | (ms_high & 0x03)

    # Pack into 9 bytes
    header = struct.pack('!B H I B B', byte1, seq_num, timestamp, byte8, ms_low)
    return header


def parse_header(data):
    """
    Decode a 9-byte header and return a dictionary of fields.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError("Invalid packet: too short")

    byte1, seq, timestamp, byte8, ms_low = struct.unpack('!B H I B B', data[:HEADER_SIZE])

    device_id = (byte1 >> 4) & 0x0F
    batch_count = byte1 & 0x0F
    proto_version = (byte8 >> 6) & 0x03
    msg_type = (byte8 >> 4) & 0x03
    ms_high = byte8 & 0x03
    milliseconds = (ms_high << 8) | ms_low

    return {
        "device_id": device_id,
        "batch_count": batch_count,
        "seq": seq,
        "timestamp": timestamp,
        "proto_version": proto_version,
        "msg_type": msg_type,
        "milliseconds": milliseconds,
    }
