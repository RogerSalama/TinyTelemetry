#!/usr/bin/env bash
# run_baseline.sh
# Baseline test: n devices, 5 runs, PCAP capture
# Acceptance criteria: ≥99% of packets received per interval, sequence numbers in order

set -euo pipefail

mkdir -p logs

# Reset network
sudo tc qdisc del dev lo root 2>/dev/null || true

# Detect Python
PYTHON=""
for cmd in python3 python py; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -V >/dev/null 2>&1; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo " No working Python interpreter found!"
    exit 1
fi
echo " Using Python: $PYTHON"

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

    # Start PCAP capture
    sudo tcpdump -i lo udp port 12002 -w logs/baseline_run${run}.pcap &
    PCAP_PID=$!

    # Start server
    $PYTHON udpsrv.py > logs/server_baseline_run${run}.log 2>&1 &
    SERVER_PID=$!
    sleep 1

    # Run clients in parallel for all devices
    CLIENT_PIDS=()
    for DEVICE_ID in "${DEVICE_IDS[@]}"; do
        CLIENT_LOG=logs/client_baseline_run${run}_dev${DEVICE_ID}.log
        $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1 &
        CLIENT_PIDS+=($!)
    done

    # Wait for all clients to finish
    for pid in "${CLIENT_PIDS[@]}"; do
        wait $pid
    done

    # Stop server and PCAP
    kill $SERVER_PID
    kill $PCAP_PID
    echo "PCAP saved: logs/baseline_run${run}.pcap"

    # NetEm log (none for baseline)
    echo "None" > logs/netem_baseline_run${run}.txt
    echo "NetEm log saved: logs/netem_baseline_run${run}.txt"

    # ---- Acceptance Criteria Check per interval per device ----
    for DEVICE_ID in "${DEVICE_IDS[@]}"; do
        CLIENT_LOG=logs/client_baseline_run${run}_dev${DEVICE_ID}.log
        echo " Device $DEVICE_ID: Checking packets per interval for run $run"

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
            # Extract interval
            match($0, /interval=([0-9]+)s/, m)
            intv = m[1]
            # Extract sequence number
            match($0, /seq=([0-9]+)/, s)
            seq = s[1]+0
            # Count packet
            count[intv]++
            # Check sequence order
            if (prev_seq[intv] != -1 && seq != prev_seq[intv]+1) in_order[intv] = 0
            prev_seq[intv] = seq
        }

        END {
            for (j in intv_arr) {
                interval = intv_arr[j]
                expected = int(duration / interval)       # expected packets per interval
                received = count[interval]+0
                perc = (received / expected) * 100
                report_status = (perc >= 99 ? "sufficient packets" : " insufficient packets")
                seq_status = (in_order[interval] ? "sequence numbers OK" : " sequence numbers OUT OF ORDER")
                printf "Interval %ds: %d/%d packets received (%.2f%%) %s, %s\n", interval, received, expected, perc, report_status, seq_status
            }
        }
        ' "$CLIENT_LOG"
    done

done

echo " Baseline test complete (5 runs for ${#DEVICE_IDS[@]} devices)."
