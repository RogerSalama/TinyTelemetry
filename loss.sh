#!/usr/bin/env bash
# run_loss.sh
# Packet loss scenario: 5% loss, single device, PCAP capture, acceptance criteria
# Count packets and detect sequence gaps/duplicates from client log instead of CSV

set -euo pipefail

mkdir -p logs

# Reset network
sudo tc qdisc del dev lo root 2>/dev/null || true

 SERVER_CSV="logs/iot_device_data.csv"

# Apply 5% packet loss
LOSS_PERCENT=5
sudo tc qdisc add dev lo root netem loss ${LOSS_PERCENT}%
echo " Applied ${LOSS_PERCENT}% packet loss on loopback"

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

DEVICE_ID=1
echo " Running loss scenario test for Device $DEVICE_ID (duration=${DURATION}s, intervals=${INTERVALS})"

# Ask user for device IDs
read -p "Enter device IDs separated by commas [default: 1]: " DEVICE_INPUT
DEVICE_IDS=${DEVICE_INPUT:-1}
IFS=',' read -r -a DEVICE_IDS <<< "$DEVICE_IDS"




for i in {1..5}; do
    echo "=== Loss test run $i ==="

    # Create per-run directory FIRST
    RUN_DIR="logs/loss_run${i}"
    mkdir -p "$RUN_DIR"
    chmod 777 "$RUN_DIR"

    # Start PCAP capture
    PCAP_FILE="$RUN_DIR/loss_run${i}.pcap"
    sudo tcpdump -i lo udp port 12001 -w "$PCAP_FILE" &>/dev/null &
    PCAP_PID=$!

    # Remove previous server CSV to start fresh
    SERVER_CSV=logs/iot_device_data.csv
    rm -f "$SERVER_CSV"

    # Start server
    SERVER_LOG="$RUN_DIR/server.log"
    $PYTHON udpsrv.py > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep 1

    # Run client
    CLIENT_LOG="$RUN_DIR/client.log"
    set +e
    $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1
    CLIENT_EXIT=$?
    set -e
    if [ $CLIENT_EXIT -ne 0 ]; then
        echo "Client crashed during run $i (exit code $CLIENT_EXIT)"
    fi

    # Create per-run directory
    RUN_DIR="logs/loss_run${i}"
    mkdir -p "$RUN_DIR"
    chmod 777 "$RUN_DIR"

    # Stop server & PCAP
    kill "$SERVER_PID" 2>/dev/null || true
    kill "$PCAP_PID" 2>/dev/null || true


    # Save NetEm command to log (do not execute)
    NETEM_LOG="$RUN_DIR/netem_loss_run${i}.txt"
    echo "sudo tc qdisc add dev lo root netem loss ${LOSS_PERCENT}%" > "$NETEM_LOG"
    echo "NetEm log saved: $NETEM_LOG"



    # ---- Generate CSV for this run ----
     CSV_FILE="$RUN_DIR/loss_run${i}.csv"
    if [ -f "$SERVER_CSV" ]; then
        cp "$SERVER_CSV" "$CSV_FILE"
        chmod 666 "$CSV_FILE"
        echo "CSV saved: $CSV_FILE"
    else
        echo "Warning: CSV not found for run $i"
    fi

    # ---- Packets per interval and sequence gaps/duplicates from client log ----
    echo " Device $DEVICE_ID: Packets and sequence info for run $i"
    awk -v duration="$DURATION" -v intervals="$INTERVALS" '
    BEGIN {
        split(intervals, intv_arr, ",")
    }
    /Sent DATA/ {
        match($0, /interval=([0-9]+)s/, m)
        intv = m[1]
        count[intv]++
        match($0, /seq=([0-9]+)/, s)
        seq[intv, s[1]]++
        if (last_seq[intv] != "" && s[1] != last_seq[intv]+1) gaps[intv]++
        last_seq[intv] = s[1]
    }
    END {
        for (j in intv_arr) {
            interval = intv_arr[j]
            expected = int(duration / interval)
            received = count[interval]+0
            perc = (received/expected)*100
            status = (perc>=99 ? " sufficient packets" : " insufficient packets")

            # Duplicates
            dup_count=0
            for (k in seq) {
                split(k, a, SUBSEP)
                if (a[1]==interval && seq[k]>1) dup_count+=seq[k]-1
            }
            dup_rate = (dup_count/ (received>0?received:1))*100
            dup_status = (dup_rate<=1 ? " duplicates ≤ 1%" : " duplicates > 1%")

            gap_status = (gaps[interval]>0 ? " sequence gaps detected" : " no sequence gaps")
            printf "Interval %ds: %d/%d packets sent (%.2f%%) %s, %s, %s (dup rate %.2f%%)\n", interval, received, expected, perc, status, gap_status, dup_status, dup_rate
        }
    }
    ' "$CLIENT_LOG"

done

# Reset network
sudo tc qdisc del dev lo root 2>/dev/null || true
echo " Loss test complete."