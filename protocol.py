import struct
import time

MAX_BITS = 1600
MAX_BYTES = MAX_BITS // 8  # 200 bytes total
HEADER_SIZE = 10 

# Message types
MSG_INIT = 0
MSG_DATA = 1
HEART_BEAT = 2
NACK_MSG = 3



# Units mapping
UNITS = {
    "celsius": 0,
    "fahrenheit": 1,
    "kelvin": 2,
    "percent": 3,
    "volts": 4,
    "amps": 5,
    "watts": 6,
    "meters": 7,
    "liters": 8,
    "grams": 9,
    "pascal": 10,
    "hertz": 11,
    "lux": 12,
    "db": 13,
    "ppm": 14,
    "unknown": 15
}

def unit_to_code(unit):
    """Convert unit name to code."""
    return UNITS.get(unit.lower(), 15)  # default to unknown

def code_to_unit(code):
    """Convert code to unit name."""
    for name, c in UNITS.items():
        if c == code:
            return name
    return "unknown"


def build_checksum_header(device_id, batch_count, seq_num, msg_type, payload=None):
    # 1. Build the base 9-byte header.
    header = build_header(device_id, batch_count, seq_num, msg_type)

    # 2. Combine header and payload for checksum calculation.
    # header is already bytes. If payload is None, use empty bytes.
    # If payload exists, it must be a bytes object for concatenation.
    if payload is None:
        data_to_checksum = header
    else:
        # Assumes payload is a bytes object (e.g., from struct.pack or .encode()).
        data_to_checksum = header + payload 
        
    # 3. Calculate 1-byte checksum.
    checksum = ascii_sum_checksum(data_to_checksum) 

    # 4. Repack the base header (9 bytes) along with the new 1-byte checksum.
    # Total header size is now 9 + 1 = 10 bytes.
    # We unpack the 9-byte header and prepend the new 1-byte checksum to the *data* it holds.
    # The simplest way is to just append the checksum byte to the header bytes.
    
    # The original struct.pack line was fundamentally wrong:
    # checksum_header = struct.pack('!B H I B B B', header, checksum)
    # Easiest fix: append the checksum byte to the existing header bytes.
    checksum_header = header + struct.pack('!B', checksum) 
    
    return checksum_header


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

    byte1, seq, timestamp, byte8, ms_low, byte10 = struct.unpack('!B H I B B B', data[:HEADER_SIZE])

    device_id = (byte1 >> 4) & 0x0F
    batch_count = byte1 & 0x0F
    proto_version = (byte8 >> 6) & 0x03
    msg_type = (byte8 >> 4) & 0x03
    ms_high = byte8 & 0x03
    milliseconds = (ms_high << 8) | ms_low
    checksum = (byte10)

    return {
        "device_id": device_id,
        "batch_count": batch_count,
        "seq": seq,
        "timestamp": timestamp,
        "proto_version": proto_version,
        "msg_type": msg_type,
        "milliseconds": milliseconds,
        "checksum": checksum,
    }


# --- Simple LCG-based stream cipher helpers ---
# NOTE: This is a very small/fast stream cipher using an LCG to produce
# a byte keystream which is XORed with the payload. It provides a
# minimal confidentiality layer for the payload bytes while preserving
# binary layout (so packed floats remain 8-byte doubles).

# Shared secret between client and server (change if you want a different key)
SECRET = 0xA5A5A5A5


def _lcg_generator(seed):
    a = 1664525
    c = 1013904223
    m = 2 ** 32
    state = seed & 0xFFFFFFFF
    while True:
        state = (a * state + c) % m
        yield state


def _keystream_bytes(seed, length):
    gen = _lcg_generator(seed)
    out = bytearray()
    while len(out) < length:
        val = next(gen)
        out.extend(val.to_bytes(4, 'big'))
    return bytes(out[:length])


def encrypt_bytes(data: bytes, device_id: int, seq: int) -> bytes:
    """Encrypt bytes using LCG-derived keystream.

    Parameters:
    - data: bytes to encrypt (or decrypt)
    - device_id, seq: used to derive a per-packet seed so both sides
      can deterministically generate the same keystream.

    This uses XOR and is symmetric: calling it twice restores the data.
    """
    # Compose a 32-bit-ish seed from inputs and shared SECRET
    seed = (((device_id & 0xFFFF) << 16) ^ (seq & 0xFFFF) ^ (SECRET & 0xFFFFFFFF)) & 0xFFFFFFFF
    ks = _keystream_bytes(seed, len(data))
    return bytes(b ^ k for b, k in zip(data, ks))


def decrypt_bytes(data: bytes, device_id: int, seq: int) -> bytes:
    """Same as `encrypt_bytes` (XOR stream cipher)."""
    return encrypt_bytes(data, device_id, seq)

def ascii_sum_checksum(data):
    """
    Simple 1-byte checksum: Sum all ASCII values, mod 256
    For binary data, treats each byte as a character code (0-255)
    """
    total = 0
    for byte in data:
        total += byte  # ASCII value of the byte
    
    # Return only the lower 8 bits (1 byte)
    return total & 0xFF

def calculate_expected_checksum(header_data_dict, payload):
    """
    Calculates the 1-byte checksum based on the base header contents and payload.
    """
    
    # 2. Concatenate base header and payload bytes.
    data_to_checksum = header_data_dict + payload
    
    # 3. Calculate the 1-byte checksum.
    return ascii_sum_checksum(data_to_checksum)

def encode_smart_payload(values, flag_batches=None):
    """
    Encode a payload with smart compression flags.
    values: list of numbers (int or float) to send
    flag_batches: list of batch numbers (1-indexed) that should use float (8 bytes) instead of int32 (4 bytes)
    Returns: bytes object containing the payload
    """
    n = len(values)
    
    # If no flags provided, send all as int32 (4 bytes)
    if flag_batches is None:
        flag_batches = []
    
    # Create the flag section
    flag_byte_count = len(flag_batches)
    flag_byte_count = struct.pack('B', flag_byte_count)
    flag_bytes = bytes(flag_batches)  # Each flag is 1 byte (1-10)
    
    # Create the data section
    data_bytes = bytearray()
    for i, value in enumerate(values, start=1):
        if i in flag_batches:
            # Store as float (8 bytes) - Python float is double precision
            data_bytes.extend(struct.pack('!d', float(value)))
        else:
            # Store as int32 (4 bytes)
            data_bytes.extend(struct.pack('!i', int(value)))
    
    # Combine: flag_byte_count (1 byte) + flag_bytes + data_bytes
    return flag_byte_count + flag_bytes + bytes(data_bytes)

def decode_smart_payload(payload_bytes, batch_count):
    """
    Decode the smart payload structure.
    Returns: list of numbers (either int or float)
    """
    if len(payload_bytes) == 0:
        return []
    
    # Read flag byte count
    flag_byte_count = payload_bytes[0]
    
    # Read flag bytes (batch numbers that use 8-byte float)
    if flag_byte_count > 0:
        flag_bytes = payload_bytes[1:1+flag_byte_count]
        flag_batches = list(flag_bytes)  # Convert to list of batch numbers
    else:
        flag_batches = []
    
    # Calculate total data size
    num_float_batches = len(flag_batches)
    num_int_batches = batch_count - num_float_batches
    expected_data_size = num_float_batches * 8 + num_int_batches * 4
    
    # Check if we have enough data
    data_start = 1 + flag_byte_count
    if len(payload_bytes) < data_start + expected_data_size:
        raise ValueError(f"Expected {expected_data_size} bytes of data, got {len(payload_bytes) - data_start}")
    
    # Read data section
    data_section = payload_bytes[data_start:data_start+expected_data_size]
    
    # Parse the data
    values = []
    data_pos = 0
    
    for batch_num in range(1, batch_count + 1):
        if batch_num in flag_batches:
            # Read 8 bytes for float/double
            value_bytes = data_section[data_pos:data_pos+8]
            if len(value_bytes) < 8:
                break
            value = struct.unpack('!d', value_bytes)[0]
            data_pos += 8
        else:
            # Read 4 bytes for int32
            value_bytes = data_section[data_pos:data_pos+4]
            if len(value_bytes) < 4:
                break
            value = struct.unpack('!i', value_bytes)[0]
            data_pos += 4
            value /= 10**6
        values.append(value)
    
    return values

def calculate_smart_payload_size(batch_count, flag_batches=None):
    """
    Calculate the total payload size for smart encoding.
    batch_count: total number of batches
    flag_batches: list of batches using float (8 bytes) instead of int32 (4 bytes)
    Returns: total size in bytes
    """
    if flag_batches is None:
        flag_batches = []
    
    flag_byte_count = len(flag_batches)
    num_float_batches = len(flag_batches)
    num_int_batches = batch_count - num_float_batches
    
    # 1 byte for flag count + flag bytes + data bytes
    total_size = 1 + flag_byte_count + (num_float_batches * 8) + (num_int_batches * 4)
    return total_size