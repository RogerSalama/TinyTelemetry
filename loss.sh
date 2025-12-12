#!/usr/bin/env bash
# run_loss.sh
# Packet loss scenario: 5% loss, single device, PCAP capture, acceptance criteria
# Count packets and detect sequence gaps/duplicates from client log instead of CSV

set -euo pipefail

mkdir -p logs

# Reset network
sudo tc qdisc del dev lo root 2>/dev/null || true

# Apply 5% packet loss
LOSS_PERCENT=5
sudo tc qdisc add dev lo root netem loss ${LOSS_PERCENT}%
echo "Applied ${LOSS_PERCENT}% packet loss on loopback"

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
echo "Using Python: $PYTHON"

# Test parameters
read -p "Enter test duration (s) [default: 60]: " DURATION
DURATION=${DURATION:-60}

read -p "Enter intervals separated by commas [default: 1,5,30]: " INTERVALS
INTERVALS=${INTERVALS:-1,5,30}
IFS=',' read -r -a INTERVAL_ARRAY <<< "$INTERVALS"

DEVICE_ID=1
echo "➡️ Running loss scenario test for Device $DEVICE_ID (duration=${DURATION}s, intervals=${INTERVALS})"

for i in {1..5}; do
    echo "=== Loss test run $i ==="

    # Start PCAP capture
    sudo tcpdump -i lo udp port 12002 -w logs/loss_run${i}.pcap &
    PCAP_PID=$!

    # Start server
    $PYTHON udpsrv.py > logs/server_loss_run${i}.log 2>&1 &
    SERVER_PID=$!
    sleep 1

    # Run client
    CLIENT_LOG=logs/client_loss_run${i}.log
    $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "$CLIENT_LOG" 2>&1

    # Stop server and PCAP
    kill $SERVER_PID
    kill $PCAP_PID
    echo "PCAP saved: logs/loss_run${i}.pcap"

    # NetEm log
    echo "sudo tc qdisc add dev lo root netem loss ${LOSS_PERCENT}%" > logs/netem_loss_run${i}.txt
    echo "NetEm log saved: logs/netem_loss_run${i}.txt"

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
            
            gap_status = (gaps[interval]>0 ? "sequence gaps detected" : " no sequence gaps")
            printf "Interval %ds: %d/%d packets sent (%.2f%%) %s, %s, %s (dup rate %.2f%%)\n", interval, received, expected, perc, status, gap_status, dup_status, dup_rate
        }
    }
    ' "$CLIENT_LOG"

done


sudo tc qdisc del dev lo root
echo " Loss test complete."