#!/usr/bin/env bash
# baseline.sh - Run baseline tests for TinyTelemetry
# Fixed for WSL/Linux environments with proper permissions

set -euo pipefail

# Ensure logs directory exists
mkdir -p logs
chmod 777 logs  # Allow all read/write/execute for WSL users

# Reset network (NetEm, if needed)
sudo tc qdisc del dev lo root 2>/dev/null || true

# Detect Python interpreter
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

# Ask user for test duration and intervals
read -p "Enter test duration (s) [default: 60]: " DURATION
DURATION=${DURATION:-60}

read -p "Enter intervals separated by commas [default: 1,5,30]: " INTERVALS
INTERVALS=${INTERVALS:-1,5,30}
IFS=',' read -r -a INTERVAL_ARRAY <<< "$INTERVALS"

# Ask user for device IDs
read -p "Enter device IDs separated by commas [default: 1]: " DEVICE_INPUT
DEVICE_INPUT=${DEVICE_INPUT:-1}
IFS=',' read -r -a DEVICE_IDS <<< "$DEVICE_INPUT"

echo "Running baseline test for devices: ${DEVICE_IDS[*]} (duration=${DURATION}s, intervals=${INTERVALS})"

# Run 5 baseline runs
for run in {1..5}; do
    echo "=== Baseline run $run ==="

    # Create per-run directory
    RUN_DIR="logs/baseline_run${run}"
    mkdir -p "$RUN_DIR"
    chmod 777 "$RUN_DIR"

    # Start PCAP capture
    PCAP_FILE="$RUN_DIR/baseline_run${run}.pcap"
    sudo touch "$PCAP_FILE"
    sudo chmod 666 "$PCAP_FILE"
    sudo tcpdump -i lo udp port 12002 -w "$PCAP_FILE" &
    PCAP_PID=$!

    # Start server
    SERVER_LOG="$RUN_DIR/server_baseline_run${run}.log"
    touch "$SERVER_LOG"
    chmod 666 "$SERVER_LOG"
    $PYTHON udpsrv.py > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep 1

    # Run clients for all devices
    CLIENT_PIDS=()
    for DEVICE_ID in "${DEVICE_IDS[@]}"; do
        CLIENT_LOG="$RUN_DIR/client_baseline_run${run}_dev${DEVICE_ID}.log"
        touch "$CLIENT_LOG"
        chmod 666 "$CLIENT_LOG"
        $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1 &
        CLIENT_PIDS+=($!)
    done

    # Wait for clients to finish
    for pid in "${CLIENT_PIDS[@]}"; do
        wait $pid
    done

    # Stop server and PCAP
   if kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID"
    fi

    if kill -0 "$PCAP_PID" 2>/dev/null; then
        kill "$PCAP_PID"
    fi
    echo "PCAP saved: $PCAP_FILE"

    # NetEm log (none for baseline)
    NETEM_LOG="$RUN_DIR/netem_baseline_run${run}.txt"
    echo "None" > "$NETEM_LOG"
    chmod 666 "$NETEM_LOG"
    echo "NetEm log saved: $NETEM_LOG"

    # Generate CSV for this run
    CSV_FILE="$RUN_DIR/baseline_run${run}.csv"
    if [ -f logs/iot_device_data.csv ]; then
        cp logs/iot_device_data.csv "$CSV_FILE"
        chmod 666 "$CSV_FILE"
        echo "CSV saved for run $run: $CSV_FILE"
    else
        echo "Warning: logs/iot_device_data.csv not found, skipping CSV copy."
    fi

    # Acceptance Criteria Check per interval per device
    for DEVICE_ID in "${DEVICE_IDS[@]}"; do
        CLIENT_LOG="$RUN_DIR/client_baseline_run${run}_dev${DEVICE_ID}.log"
        echo "Device $DEVICE_ID: Checking packets per interval for run $run"

        awk -v duration="$DURATION" -v intervals="$INTERVALS" '
        BEGIN {
            split(intervals, intv_arr, ",")
            for (j in intv_arr) {
                count[intv_arr[j]] = 0
                prev_seq[intv_arr[j]] = -1
                in_order[intv_arr[j]] = 1
            }
        }
        /Sent DATA/ {
            match($0, /interval=([0-9]+)s/, m)
            intv = m[1]
            match($0, /seq=([0-9]+)/, s)
            seq = s[1]+0
            count[intv]++
            if (prev_seq[intv] != -1 && seq != prev_seq[intv]+1) in_order[intv] = 0
            prev_seq[intv] = seq
        }
        END {
            for (j in intv_arr) {
                interval = intv_arr[j]
                expected = int(duration / interval)
                received = count[interval]+0
                perc = (received / expected) * 100
                report_status = (perc >= 99 ? "sufficient packets" : "insufficient packets")
                seq_status = (in_order[interval] ? "sequence numbers OK" : "sequence numbers OUT OF ORDER")
                printf "Interval %ds: %d/%d packets received (%.2f%%) %s, %s\n", interval, received, expected, perc, report_status, seq_status
            }
        }
        ' "$CLIENT_LOG"
    done

done

echo "Baseline test complete (5 runs for ${#DEVICE_IDS[@]} devices)."
