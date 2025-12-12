#!/usr/bin/env bash
# run_delay.sh
# Delay+jitter scenario: 100ms ±10ms, single device, PCAP capture
# Acceptance criteria: server reorders by timestamp, no buffer overrun, no crash

set -euo pipefail
mkdir -p logs

# Reset network
sudo tc qdisc del dev lo root 2>/dev/null || true

# Apply 100ms ±10ms delay
DELAY_MS=100
JITTER_MS=10
sudo tc qdisc add dev lo root netem delay ${DELAY_MS}ms ${JITTER_MS}ms
echo "Applied ${DELAY_MS}ms ±${JITTER_MS}ms delay"

# Detect Python
PYTHON=""
for cmd in python3 python py; do
    command -v "$cmd" >/dev/null 2>&1 && { PYTHON="$cmd"; break; }
done
[ -z "$PYTHON" ] && { echo " No Python found!"; exit 1; }
echo "Using Python: $PYTHON"

# Test parameters
read -p "Duration (s) [60]: " DURATION
DURATION=${DURATION:-60}
read -p "Intervals (s) [1,5,30]: " INTERVALS
INTERVALS=${INTERVALS:-1,5,30}
IFS=',' read -r -a INTERVAL_ARRAY <<< "$INTERVALS"

DEVICE_ID=1
echo " Running delay+jitter test for Device $DEVICE_ID (duration=${DURATION}s, intervals=${INTERVALS})"

for i in {1..5}; do
    echo "=== Run $i ==="

    # Start PCAP
    sudo tcpdump -i lo udp port 12002 -w logs/delay_run${i}.pcap &
    PCAP_PID=$!

    # Start server
    SERVER_LOG=logs/server_delay_run${i}.log
    $PYTHON udpsrv.py > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep 1

    # Run client
    CLIENT_LOG=logs/client_delay_run${i}.log
    $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1

    # Stop server & PCAP
    kill $SERVER_PID
    kill $PCAP_PID
    echo "PCAP saved: logs/delay_run${i}.pcap"

    # Move CSV
    CSV_FILE=logs/delay_run${i}.csv
    if [ -f logs/iot_device_data.csv ]; then
        mv logs/iot_device_data.csv "$CSV_FILE"
    elif [ -f iot_device_data.csv ]; then
        mv iot_device_data.csv "$CSV_FILE"
    else
        echo " CSV not found for run $i"
        continue
    fi
    echo "CSV saved: $CSV_FILE"

    # ---- Acceptance criteria ----
    echo " Checking acceptance criteria for run $i"

    awk -F, -v intervals="${INTERVALS}" '
    BEGIN {
        split(intervals,intv_arr,",")
        crash=0
        overrun=0
    }
    FNR==1 {next} # skip header if any
    {
        ts=$6
        if(last_ts!="" && ts<last_ts) in_order=0
        last_ts=ts
    }
    END {
        order_status=(in_order==0 ? " timestamps out of order" : " timestamps correctly ordered")
        crash_status=(system("grep -i ERROR '$SERVER_LOG' >/dev/null 2>&1") ? " server crash detected" : " no server crash")
        printf "%s, %s\n", order_status, crash_status
    }
    ' "$CSV_FILE"

done

# Reset network
sudo tc qdisc del dev lo root
echo " Delay+jitter test complete."
