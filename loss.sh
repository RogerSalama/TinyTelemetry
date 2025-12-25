#!/usr/bin/env bash
# run_loss.sh
# Packet loss scenario: 5% loss, single device, PCAP capture, acceptance criteria
# Count packets and detect sequence gaps/duplicates from client log instead of CSV

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

# Reset network
# sudo tc qdisc del dev lo root 2>/dev/null || true

# Detect Python
PYTHON=""
for cmd in python3 python py; do
    if command -v "$cmd" >/dev/null 2>&1; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "No working Python interpreter found!"
    exit 1
fi
echo "Using Python: $PYTHON"

# Test parameters
read -p "Enter test duration (s) [default: 60]: " DURATION
DURATION=${DURATION:-60}

read -p "Enter intervals separated by commas [default: 1,5,30]: " INTERVALS
INTERVALS=${INTERVALS:-1,5,30}
IFS=',' read -r -a INTERVAL_ARRAY <<< "$INTERVALS"

read -p "Enter device IDs separated by commas [default: 1]: " DEVICE_INPUT
DEVICE_INPUT=${DEVICE_INPUT:-1}
IFS=',' read -r -a DEVICE_IDS <<< "$DEVICE_INPUT"

echo "Running loss test for devices: ${DEVICE_IDS[*]}"
echo "Duration=${DURATION}s, Intervals=${INTERVALS}"


# Apply 5% packet loss
LOSS_PERCENT=5
sudo tc qdisc add dev lo root netem loss ${LOSS_PERCENT}%
echo "Applied network conditions:"
echo " Loss Percent: ${LOSS_PERCENT}%"



for i in {1..5}; do
    echo ""
    echo "=== Loss test run $i ==="

    # Create per-run directory FIRST
    RUN_DIR="logs/loss_run${i}"
    mkdir -p "$RUN_DIR"
    chmod 777 "$RUN_DIR"

    RUN_SERVER_CSV="$RUN_DIR/loss_run${i}.csv"
    RUN_REORDERED_CSV="$RUN_DIR/loss_run${i}_reordered.csv"

    rm -f logs/iot_device_data.csv logs/loss_run${i}_reordered.csv

    # Start PCAP capture
    PCAP_FILE="$RUN_DIR/loss_run${i}.pcap"
    sudo tcpdump -i lo udp port 12001 -w "$PCAP_FILE" &>/dev/null &
    PCAP_PID=$!

    # Remove previous server CSV to start fresh
    # SERVER_CSV=logs/iot_device_data.csv
    # rm -f "$SERVER_CSV"

    # Start server
    SERVER_LOG="$RUN_DIR/server.log"
    $PYTHON udpsrv.py > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep 1

    # Run client
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
        echo "Warning: No raw CSV data found for run $i"
    fi
    
    if [ -f "logs/iot_device_data_reordered.csv" ]; then
        mv "logs/iot_device_data_reordered.csv" "$RUN_REORDERED_CSV"
        echo "Reordered CSV moved to: $RUN_REORDERED_CSV"
    else
        echo "Warning: No reordered CSV data found for run $i"
    fi

    # Generate metrics CSV for this run
    METRICS_FILE="$RUN_DIR/metrics_loss_run${i}.csv"
    if [ -f logs/metrics.csv ]; then
        cp logs/metrics.csv "$METRICS_FILE"
        chmod 666 "$METRICS_FILE"
        echo "Metrics CSV saved for run $i: $METRICS_FILE"
    else
        echo "Warning: logs/metrics.csv not found, skipping metrics copy."
    fi

    # -------------------------
    # Save NetEm configuration
    # -------------------------
    NETEM_LOG="$RUN_DIR/netem_config.txt"
    {
        echo "Network configuration for delay test run ${i}:"
        echo "  sudo tc qdisc add dev lo root netem loss ${LOSS_PERCENT}%"
        echo "  Loss Percent: ${LOSS_PERCENT}%"
        echo ""
    } > "$NETEM_LOG"

    # ---- Packets per interval and sequence gaps/duplicates from client log ----
    echo " Device $DEVICE_ID: Packets and sequence info for run $i"
    awk -v duration="$DURATION" -v intervals="$INTERVALS" '
    BEGIN {
        split(intervals, intv_arr, ",")
        current_interval = -1
    }

    /Running [0-9]+s interval/ {
        match($0, /Running ([0-9]+)s interval/, m)
        current_interval = m[1]
    }

    /Sent DATA/ && current_interval != -1 {
        match($0, /seq=([0-9]+)/, s)
        seq_num = s[1] + 0

        count[current_interval]++
        seq[current_interval, seq_num]++

        if (last_seq[current_interval] != "" && seq_num != last_seq[current_interval] + 1)
            gaps[current_interval]++

        last_seq[current_interval] = seq_num
    }

    END {
        for (i in intv_arr) {
            interval = intv_arr[i]
            expected = int(duration / interval)
            received = count[interval] + 0
            perc = (received / expected) * 100
            status = (perc >= 99 ? "sufficient packets" : "insufficient packets")

            dup_count = 0
            for (k in seq) {
                split(k, a, SUBSEP)
                if (a[1] == interval && seq[k] > 1)
                    dup_count += seq[k] - 1
            }

            dup_rate = (dup_count / (received > 0 ? received : 1)) * 100
            dup_status = (dup_rate <= 1 ? "duplicates ≤ 1%" : "duplicates > 1%")
            gap_status = (gaps[interval] > 0 ? "sequence gaps detected" : "no sequence gaps")

            printf "Interval %ds: %d/%d packets sent (%.2f%%) %s, %s, %s (dup rate %.2f%%)\n",
                interval, received, expected, perc, status, gap_status, dup_status, dup_rate
        }
    }
    ' "$CLIENT_LOG"
done

# Reset network
sudo tc qdisc del dev lo root 2>/dev/null || true
echo ""
echo "========================================"
echo "Loss test complete!"
echo "Summary:"
echo "- Completed 5 runs for devices: ${DEVICE_IDS[*]}"
echo "- Network conditions: ${LOSS_PERCENT}% loss"
echo "- All data stored in logs/loss_run[1-5]/ directories"
echo "- Check each run directory for detailed logs and CSV files"
echo "========================================"