import struct
import time

MAX_BITS = 1600
MAX_BYTES = MAX_BITS // 8  # 200 bytes
HEADER_SIZE = 9

# Message types
MSG_INIT = 0
MSG_DATA = 1
HEART_BEAT = 2

VALID_MSG_TYPES = {MSG_INIT, MSG_DATA, HEART_BEAT}
CURRENT_PROTO_VERSION = 1


def build_header(device_id, batch_count, seq_num, msg_type):
    """Build a 9-byte header for the UDP IoT protocol."""
    timestamp = int(time.time())
    proto_version = CURRENT_PROTO_VERSION
    ms = int((time.time() * 1000) % 1000)
    ms_high = (ms >> 8) & 0x03
    ms_low = ms & 0xFF

    # Byte1: upper 4 bits device_id, lower 4 bits batch_count
    byte1 = ((device_id & 0x0F) << 4) | (batch_count & 0x0F)

    # Byte8: 2 bits proto_version, 2 bits msg_type, 00, 2 bits ms_high
    byte8 = ((proto_version & 0x03) << 6) | ((msg_type & 0x03) << 4) | (ms_high & 0x03)

    header = struct.pack('!B H I B B', byte1, seq_num, timestamp, byte8, ms_low)
    return header


def parse_header(data):
    """Decode a 9-byte header into a dictionary."""
    if len(data) < HEADER_SIZE:
        raise ValueError("Packet too short")

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
        "milliseconds": milliseconds
    }


def validate_packet(data):
    """
    Validate a packet header.
    Returns (True, "") if valid, or (False, reason) if invalid.
    """
    if len(data) < HEADER_SIZE:
        return False, "Packet too short"

    try:
        header = parse_header(data)
    except Exception as e:
        return False, f"Header parse error: {e}"

    if header['proto_version'] != CURRENT_PROTO_VERSION:
        return False, f"Unsupported protocol version: {header['proto_version']}"

    if header['msg_type'] not in VALID_MSG_TYPES:
        return False, f"Unknown message type: {header['msg_type']}"

    return True, ""
