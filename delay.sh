#!/usr/bin/env bash
# run_delay.sh
# Delay + jitter scenario
# NetEm: 100ms ±10ms
# Acceptance:
#  - Server does not crash
#  - Packets are reordered correctly by DEVICE timestamp
#  - No buffer overrun / deadlock

set -euo pipefail

mkdir -p logs

# -----------------------------
# Reset network
# -----------------------------
sudo tc qdisc del dev lo root 2>/dev/null || true

# -----------------------------
# Apply NetEm delay + jitter
# -----------------------------
DELAY_MS=100
JITTER_MS=10
sudo tc qdisc add dev lo root netem delay ${DELAY_MS}ms ${JITTER_MS}ms
echo "Applied ${DELAY_MS}ms ±${JITTER_MS}ms delay on loopback"

# -----------------------------
# Detect Python
# -----------------------------
PYTHON=""
for cmd in python3 python py; do
    if command -v "$cmd" >/dev/null 2>&1; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "No Python interpreter found!"
    exit 1
fi
echo "Using Python: $PYTHON"

# -----------------------------
# Test parameters
# -----------------------------
read -p "Enter test duration (s) [default: 60]: " DURATION
DURATION=${DURATION:-60}

read -p "Enter intervals separated by commas [default: 1,5,30]: " INTERVALS
INTERVALS=${INTERVALS:-1,5,30}

DEVICE_ID=1
echo "Running delay+jitter test for Device ${DEVICE_ID}"
echo "Duration=${DURATION}s, Intervals=${INTERVALS}"

# -----------------------------
# Run 5 delay tests
# -----------------------------
for run in {1..5}; do
    echo "=== Delay test run ${run} ==="

    RUN_DIR="logs/delay_run${run}"
    mkdir -p "$RUN_DIR"
    chmod 777 "$RUN_DIR"

    SERVER_CSV="logs/iot_device_data.csv"
    REORDERED_CSV="logs/iot_device_data_reordered.csv"

    rm -f "$SERVER_CSV" "$REORDERED_CSV"

    # -------------------------
    # Start PCAP capture
    # -------------------------
    PCAP_FILE="$RUN_DIR/delay_run${run}.pcap"
    sudo tcpdump -i lo udp port 12001 -w "$PCAP_FILE" &>/dev/null &
    PCAP_PID=$!

    # -------------------------
    # Start server
    # -------------------------
    SERVER_LOG="$RUN_DIR/server.log"
    $PYTHON udpsrv.py > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep 1

    # -------------------------
    # Run client
    # -------------------------
    CLIENT_LOG="$RUN_DIR/client.log"
    $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1

    # -------------------------
    # Stop server & PCAP
    # -------------------------
    kill "$SERVER_PID" 2>/dev/null || true
    kill "$PCAP_PID" 2>/dev/null || true

    echo "PCAP saved: $PCAP_FILE"

    # -------------------------
    # Save NetEm config
    # -------------------------
    echo "sudo tc qdisc add dev lo root netem delay ${DELAY_MS}ms ${JITTER_MS}ms" \
        > "$RUN_DIR/netem_delay_run${run}.txt"

    # -------------------------
    # Save CSV snapshots
    # -------------------------
    if [ -f "$SERVER_CSV" ]; then
        cp "$SERVER_CSV" "$RUN_DIR/delay_run${run}.csv"
        echo "Raw CSV saved: $RUN_DIR/delay_run${run}.csv"
    else
        echo "Warning: raw CSV not found for run ${run}"
    fi

    if [ -f "$REORDERED_CSV" ]; then
        cp "$REORDERED_CSV" "$RUN_DIR/delay_run${run}_reordered.csv"
        echo "Reordered CSV saved: $RUN_DIR/delay_run${run}_reordered.csv"
    else
        echo "Warning: reordered CSV not found for run ${run}"
    fi

    # -------------------------
    # Acceptance criteria
    # -------------------------
    echo "Checking acceptance criteria for run ${run}"

    # 1) Timestamp reordering check (DEVICE timestamp column = 5)
    if [ -f "$RUN_DIR/delay_run${run}_reordered.csv" ]; then
        awk -F, '
        BEGIN { in_order = 1 }
        NR == 1 { next }
        {
            ts = $5
            if (last_ts != "" && ts < last_ts)
                in_order = 0
            last_ts = ts
        }
        END {
            if (in_order)
                print "✔ timestamps correctly reordered"
            else
                print "✘ timestamps OUT OF ORDER"
        }
        ' "$RUN_DIR/delay_run${run}_reordered.csv"
    else
        echo "✘ reordered CSV missing — cannot verify ordering"
    fi

    # 2) Server crash check
    if grep -qiE "ERROR|Traceback|Exception" "$SERVER_LOG"; then
        echo "✘ server crash or exception detected"
    else
        echo "✔ no server crash detected"
    fi

    echo ""
done

# -----------------------------
# Reset network
# -----------------------------
sudo tc qdisc del dev lo root 2>/dev/null || true
echo "Delay+jitter test complete."
