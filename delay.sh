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
    echo "=== Delay test Run $i ==="

    RUN_DIR="logs/delay_run${i}"
    mkdir -p "$RUN_DIR"
    chmod 777 "$RUN_DIR"

    SERVER_CSV="logs/iot_device_data.csv"
    CSV_FILE="$RUN_DIR/delay_run${i}.csv"

    # Reset CSV
    rm -f "$SERVER_CSV"

    # Start PCAP
    PCAP_FILE="$RUN_DIR/delay_run${i}.pcap"
    sudo tcpdump -i lo udp port 12002 -w "$PCAP_FILE" &>/dev/null &
    PCAP_PID=$!

    # Start server
    SERVER_LOG="$RUN_DIR/server.log"
    $PYTHON udpsrv.py > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep 1

    # Run client
     CLIENT_LOG="$RUN_DIR/client.log"
    $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1

    # Stop server & PCAP
    kill $SERVER_PID
    kill $PCAP_PID
    echo "PCAP saved: logs/delay_run${i}.pcap"

    # NetEm log
    echo "sudo tc qdisc add dev lo root netem delay ${DELAY_MS}ms ${JITTER_MS}ms" \
        > "$RUN_DIR/netem.txt"

    # CSV snapshot
    if [ -f "$SERVER_CSV" ]; then
        cp "$SERVER_CSV" "$RUN_DIR/delay_run${i}.csv"
        echo "CSV saved: $RUN_DIR/delay_run${i}.csv"
    else
        echo "CSV not found for run $i"
    fi

    # ---- Acceptance criteria ----
    echo " Checking acceptance criteria for run $i"

    awk -F, -v intervals="$INTERVALS" -v server_log="$SERVER_LOG" '
    BEGIN {
        split(intervals,intv_arr,",")
        in_order=1
    }
    FNR==1 {next} # skip header
    {
        ts=$1
        if(last_ts!="" && ts<last_ts) in_order=0
        last_ts=ts
    }
    END {
        if(in_order==0)
            order_status="timestamps out of order"
        else
            order_status="timestamps correctly ordered"

        # Check for server crash
        crash_status="no server crash"
        if(system("grep -i ERROR " server_log " >/dev/null 2>&1") == 0)
            crash_status="server crash detected"

        printf "%s, %s\n", order_status, crash_status
    }
    ' "$CSV_FILE"



done

# Reset network
sudo tc qdisc del dev lo root
echo " Delay+jitter test complete."