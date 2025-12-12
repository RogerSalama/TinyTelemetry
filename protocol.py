
import struct
import time

# Sizes / limits
MAX_BITS = 1600
MAX_BYTES = MAX_BITS // 8  # 200 bytes
<<<<<<< HEAD
HEADER_SIZE = 9
=======
HEADER_SIZE = 10           # 9-byte base header + 1-byte checksum
>>>>>>> e8a682a (fixing errors)

# Message types
MSG_INIT = 0
MSG_DATA = 1
HEART_BEAT = 2
NACK_MSG = 3

<<<<<<< HEAD
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
=======
# Units mapping
UNITS = {
    "celsius": 0, "fahrenheit": 1, "kelvin": 2, "percent": 3,
    "volts": 4, "amps": 5, "watts": 6, "meters": 7, "liters": 8,
    "grams": 9, "pascal": 10, "hertz": 11, "lux": 12, "db": 13,
    "ppm": 14, "unknown": 15
}

def unit_to_code(unit: str) -> int:
    return UNITS.get(unit.lower(), 15)

def code_to_unit(code: int) -> str:
    for name, c in UNITS.items():
        if c == code:
            return name
    return "unknown"

def ascii_sum_checksum(data: bytes) -> int:
    """1-byte checksum = sum(data) mod 256"""
    return sum(data) & 0xFF

def build_header(device_id: int, batch_count: int, seq_num: int, msg_type: int) -> bytes:
    """
    Build a 9-byte base header:
      B   H    I    B   B
      1 + 2 +  4 +  1 + 1 = 9
    """
    timestamp = int(time.time())                 # seconds
    proto_version = 1
    ms = int((time.time() * 1000) % 1000)        # 0–999 ms

    ms_high = (ms >> 8) & 0x03
    ms_low  = ms & 0xFF

    # byte1: [device_id(4) | batch_count(4)]
    byte1 = ((device_id & 0x0F) << 4) | (batch_count & 0x0F)

    # byte8: [proto_version(2) | msg_type(2) | 00(2) | ms_high(2)]
    byte8 = ((proto_version & 0x03) << 6) | ((msg_type & 0x03) << 4) | (ms_high & 0x03)

    return struct.pack('!B H I B B', byte1, seq_num, timestamp, byte8, ms_low)
>>>>>>> e8a682a (fixing errors)

def build_checksum_header(device_id: int, batch_count: int, seq_num: int, msg_type: int,
                          payload: bytes | None = None) -> bytes:
    """
    Return a 10-byte header = 9-byte base + 1-byte checksum
    Checksum is over (base_header + payload_bytes).
    """
    base = build_header(device_id, batch_count, seq_num, msg_type)
    payload_bytes = payload or b''
    checksum = ascii_sum_checksum(base + payload_bytes)
    return base + struct.pack('!B', checksum)

<<<<<<< HEAD
def parse_header(data):
    """Decode a 9-byte header into a dictionary."""
    if len(data) < HEADER_SIZE:
        raise ValueError("Packet too short")

    byte1, seq, timestamp, byte8, ms_low = struct.unpack('!B H I B B', data[:HEADER_SIZE])
    device_id = (byte1 >> 4) & 0x0F
    batch_count = byte1 & 0x0F
=======
def parse_header(data: bytes) -> dict:
    """Decode a 10-byte header and return fields."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Invalid packet: too short ({len(data)} < {HEADER_SIZE})")

    # 10 bytes: B H I B B B
    byte1, seq, timestamp, byte8, ms_low, checksum = struct.unpack('!B H I B B B', data[:HEADER_SIZE])

    device_id    = (byte1 >> 4) & 0x0F
    batch_count  =  byte1       & 0x0F
>>>>>>> e8a682a (fixing errors)
    proto_version = (byte8 >> 6) & 0x03
    msg_type      = (byte8 >> 4) & 0x03
    ms_high       =  byte8       & 0x03
    milliseconds  = (ms_high << 8) | ms_low

    return {
        "device_id": device_id,
        "batch_count": batch_count,
        "seq": seq,
        "timestamp": timestamp,
        "proto_version": proto_version,
        "msg_type": msg_type,
<<<<<<< HEAD
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
=======
        "milliseconds": milliseconds,
        "checksum": checksum,
    }

# --- Simple XOR stream over an LCG keystream (for binary payload confidentiality) ---
SECRET = 0xA5A5A5A5

def _lcg_generator(seed: int):
    a = 1664525
    c = 1013904223
    m = 2 ** 32
    state = seed & 0xFFFFFFFF
    while True:
        state = (a * state + c) % m
        yield state

def _keystream_bytes(seed: int, length: int) -> bytes:
    gen = _lcg_generator(seed)
    out = bytearray()
    while len(out) < length:
        val = next(gen)
        out.extend(val.to_bytes(4, 'big'))
    return bytes(out[:length])

def encrypt_bytes(data: bytes, device_id: int, seq: int) -> bytes:
    seed = (((device_id & 0xFFFF) << 16) ^ (seq & 0xFFFF) ^ (SECRET & 0xFFFFFFFF)) & 0xFFFFFFFF
    ks = _keystream_bytes(seed, len(data))
    return bytes(b ^ k for b, k in zip(data, ks))

def decrypt_bytes(data: bytes, device_id: int, seq: int) -> bytes:
    return encrypt_bytes(data, device_id, seq)

def calculate_expected_checksum(base_header_9_bytes: bytes, payload: bytes | None) -> int:
    """Expected checksum over 9-byte base header + payload."""
    return ascii_sum_checksum(base_header_9_bytes + (payload or b''))
>>>>>>> e8a682a (fixing errors)
