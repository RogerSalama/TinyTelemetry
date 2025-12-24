#!/usr/bin/env bash
# run_delay.sh
# Delay + jitter scenario
# NetEm: 100ms ±10ms with reordering
# Goal: Simulate one packet being significantly delayed

set -euo pipefail

# --- Cleanup Function ---
cleanup() {
    echo "Cleaning up..."
    # Kill background jobs
    jobs -p | xargs -r kill 2>/dev/null || true
    
    # Reset network
    sudo tc qdisc del dev lo root 2>/dev/null || true
    
    # Kill any stray udpsrv.py processes
    # We use || true because pkill might return 1 (no process) or 127 (not found)
    pkill -f udpsrv.py 2>/dev/null || true
}
trap cleanup EXIT

# Kill any existing server instances before starting
echo "Killing stale server instances..."
pkill -f udpsrv.py 2>/dev/null || true


mkdir -p logs

# Detect Python interpreter
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
IFS=',' read -r -a INTERVAL_ARRAY <<< "$INTERVALS"

read -p "Enter device IDs separated by commas [default: 1]: " DEVICE_INPUT
DEVICE_INPUT=${DEVICE_INPUT:-1}
IFS=',' read -r -a DEVICE_IDS <<< "$DEVICE_INPUT"

echo "Running delay+jitter test for devices: ${DEVICE_IDS[*]}"
echo "Duration=${DURATION}s, Intervals=${INTERVALS}"

# -----------------------------
# Network configuration
# -----------------------------
DELAY_MS=100
JITTER_MS=10

# Reset network first
sudo tc qdisc del dev lo root 2>/dev/null || true

# Apply NetEm delay + jitter + reordering
sudo tc qdisc add dev lo root netem delay ${DELAY_MS}ms ${JITTER_MS}ms
echo "Applied network conditions:"
echo "  Base delay: ${DELAY_MS}ms"
echo "  Jitter: ±${JITTER_MS}ms"

# -----------------------------
# Run 5 delay tests
# -----------------------------
for run in {1..5}; do
    echo ""
    echo "=== Delay test run ${run} ==="

    # Create per-run directory
    RUN_DIR="logs/delay_run${run}"
    if [ -f "$RUN_DIR" ]; then
    rm "$RUN_DIR"
    fi
    mkdir -p "$RUN_DIR"
    chmod 777 "$RUN_DIR"
    
    # Set paths for this run
    RUN_SERVER_CSV="$RUN_DIR/iot_device_data.csv"
    RUN_REORDERED_CSV="$RUN_DIR/delay_run${run}_reordered.csv"
    
    # Clean up any existing CSV files in logs root directory
    rm -f logs/iot_device_data.csv logs/delay_run${run}_reordered.csv

    # -------------------------
    # Start PCAP capture
    # -------------------------
    PCAP_FILE="$RUN_DIR/delay_run${run}.pcap"
    sudo tcpdump -i lo udp port 12001 -w "$PCAP_FILE" &>/dev/null &
    PCAP_PID=$!
    sleep 0.5

    # -------------------------
    # Start server
    # -------------------------
    SERVER_LOG="$RUN_DIR/server.log"
    $PYTHON udpsrv.py > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep 1  # Give server time to start

    # -------------------------
    # Run clients for all devices
    # -------------------------
    CLIENT_PIDS=()
    CLIENT_LOGS=()
    
    echo "Starting clients for devices: ${DEVICE_IDS[*]}"
    for DEVICE_ID in "${DEVICE_IDS[@]}"; do
        CLIENT_LOG="$RUN_DIR/client_device${DEVICE_ID}.log"
        echo "  Starting client for Device $DEVICE_ID..."
        $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1 &
        CLIENT_PIDS+=($!)
        CLIENT_LOGS+=("$CLIENT_LOG")
    done

    # Wait for all clients to finish
    echo "Waiting for all clients to complete..."
    for pid in "${CLIENT_PIDS[@]}"; do
        wait $pid 2>/dev/null || true
    done

    # Give server time to process final packets
    sleep 3

    # Stop server
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi

    # Stop PCAP capture
    if kill -0 "$PCAP_PID" 2>/dev/null; then
        sudo kill "$PCAP_PID" 2>/dev/null || true
        wait "$PCAP_PID" 2>/dev/null || true
    fi
    
    echo "PCAP saved: $PCAP_FILE"

    # -------------------------
    # Move CSV files to run directory
    # -------------------------
    if [ -f "logs/iot_device_data.csv" ]; then
        mv "logs/iot_device_data.csv" "$RUN_SERVER_CSV"
        echo "Raw CSV moved to: $RUN_SERVER_CSV"
    else
        echo "Warning: No raw CSV data found for run $run"
    fi
    
    if [ -f "logs/iot_device_data_reordered.csv" ]; then
        mv "logs/iot_device_data_reordered.csv" "$RUN_REORDERED_CSV"
        echo "Reordered CSV moved to: $RUN_REORDERED_CSV"
    else
        echo "Warning: No reordered CSV data found for run $run"
    fi

    # Generate metrics CSV for this run
    METRICS_FILE="$RUN_DIR/metrics_delay_run${run}.csv"
    if [ -f logs/metrics.csv ]; then
        cp logs/metrics.csv "$METRICS_FILE"
        chmod 666 "$METRICS_FILE"
        echo "Metrics CSV saved for run $run: $METRICS_FILE"
    else
        echo "Warning: logs/metrics.csv not found, skipping metrics copy."
    fi

    # -------------------------
    # Save NetEm configuration
    # -------------------------
    NETEM_LOG="$RUN_DIR/netem_config.txt"
    {
        echo "Network configuration for delay test run ${run}:"
        echo "  sudo tc qdisc add dev lo root netem delay ${DELAY_MS}ms ${JITTER_MS}ms"
        echo "  Base delay: ${DELAY_MS}ms"
        echo "  Jitter: ±${JITTER_MS}ms"
        echo ""
        echo "Goal: Simulate one packet being significantly delayed"
        echo "Expected behavior: Packets may arrive out of order (e.g., 1,3,4,2)"
    } > "$NETEM_LOG"

    # -------------------------
    # Acceptance Criteria Check
    # -------------------------
    echo ""
    echo "--- Acceptance Criteria Check ---"
    
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
echo ""
echo "========================================"
echo "Delay+jitter test complete!"
echo "Summary:"
echo "- Completed 5 runs for devices: ${DEVICE_IDS[*]}"
echo "- Network conditions: ${DELAY_MS}ms delay ±${JITTER_MS}ms"
echo "- All data stored in logs/delay_run[1-5]/ directories"
echo "- Check each run directory for detailed logs and CSV files"
echo "========================================"