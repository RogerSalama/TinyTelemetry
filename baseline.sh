#!/usr/bin/env bash
# run_baseline.sh
# Baseline test: 1 device, 5 runs, PCAP capture
# Acceptance criteria: ≥99% of packets received per interval, sequence numbers in order

set -euo pipefail
mkdir -p logs

# Reset network (ignore errors if none)
if command -v sudo >/dev/null 2>&1; then
  sudo tc qdisc del dev lo root 2>/dev/null || true
else
  tc qdisc del dev lo root 2>/dev/null || true
fi

# Detect Python 3
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
  echo "❌ No working Python interpreter found!"
  exit 1
fi
echo "✅ Using Python: $PYTHON"

# Ask user for test duration and intervals
read -p "Enter test duration (s) [default: 60]: " DURATION
DURATION=${DURATION:-60}

read -p "Enter intervals separated by commas [default: 1,5,30]: " INTERVALS
INTERVALS=${INTERVALS:-1,5,30}

DEVICE_ID=1
echo "➡ Running baseline test for Device $DEVICE_ID (duration=${DURATION}s, intervals=${INTERVALS})"

for i in {1..5}; do
  echo "=== Baseline run $i ==="

  # Start PCAP capture on UDP port 12002
  if command -v sudo >/dev/null 2>&1; then
    sudo tcpdump -i lo udp port 12002 -w "logs/baseline_run${i}.pcap" >/dev/null 2>&1 &
  else
    tcpdump -i lo udp port 12002 -w "logs/baseline_run${i}.pcap" >/dev/null 2>&1 &
  fi
  PCAP_PID=$!

  # Start server (background)
  $PYTHON udpsrv.py > "logs/server_baseline_run${i}.log" 2>&1 &
  SERVER_PID=$!
  sleep 1

  # Run client (foreground)
  $PYTHON udpclnt.py "$DEVICE_ID" "$DURATION" "$INTERVALS" > "logs/client_baseline_run${i}.log" 2>&1
  echo "Client finished."

  # Give the server a moment to process + flush
  sleep 1

  # Send SIGINT and wait (up to 20s) for graceful shutdown; then SIGTERM if needed
  if ps -p "$SERVER_PID" >/dev/null 2>&1; then
    kill -INT "$SERVER_PID" || true
    for t in $(seq 1 20); do
      if ! ps -p "$SERVER_PID" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if ps -p "$SERVER_PID" >/dev/null 2>&1; then
      echo "Server still running; sending SIGTERM..."
      kill "$SERVER_PID" || true
    fi
    echo "Server stopped."
  else
    echo "Server already exited."
  fi

  # Stop tcpdump with SIGINT to finalize the pcap file
  if ps -p "$PCAP_PID" >/dev/null 2>&1; then
    kill -INT "$PCAP_PID" || true
    sleep 1
  fi
  echo "PCAP saved: logs/baseline_run${i}.pcap"

  # Copy CSV (avoid mv in case file is root-owned); sudo fallback; wait up to 5s for file
  CSV_SRC="logs/iot_device_data.csv"
  CSV_DST="logs/baseline_run${i}.csv"
  for t in $(seq 1 5); do
    [ -f "$CSV_SRC" ] && break
    sleep 1
  done
  if [ -f "$CSV_SRC" ]; then
    if cp "$CSV_SRC" "$CSV_DST" 2>/dev/null; then
      echo "CSV saved: $CSV_DST"
    elif command -v sudo >/dev/null 2>&1 && sudo cp "$CSV_SRC" "$CSV_DST"; then
      echo "CSV saved with sudo: $CSV_DST"
      command -v sudo >/dev/null 2>&1 && sudo chown "$USER":"$USER" "$CSV_SRC" 2>/dev/null || true
    else
      echo "Could not copy CSV (permission denied). Try: sudo chown \"$USER\":\"$USER\" $CSV_SRC"
    fi
  else
    echo "CSV not found for run $i (server may not have written it yet)."
  fi

  # Copy metrics (written on SIGINT) with sudo fallback; wait up to 5s
  MET_SRC="logs/metrics.json"
  MET_DST="logs/baseline_metrics_run${i}.json"
  for t in $(seq 1 5); do
    [ -f "$MET_SRC" ] && break
    sleep 1
  done
  if [ -f "$MET_SRC" ]; then
    if cp "$MET_SRC" "$MET_DST" 2>/dev/null; then
      echo "Metrics saved: $MET_DST"
    elif command -v sudo >/dev/null 2>&1 && sudo cp "$MET_SRC" "$MET_DST"; then
      echo "Metrics saved with sudo: $MET_DST"
      command -v sudo >/dev/null 2>&1 && sudo chown "$USER":"$USER" "$MET_SRC" 2>/dev/null || true
    else
      echo "Could not copy metrics.json (permission denied)."
    fi
  else
    echo "No metrics.json found — ensure server received SIGINT and had time to exit."
  fi

  # Acceptance check (simple): sequence_number contiguous per interval
  awk -F, -v device_id="$DEVICE_ID" -v duration="$DURATION" -v intervals="$INTERVALS" '
    BEGIN { split(intervals, intv, ","); rows=0; }
    NR>1 && $2==device_id { seq[++rows] = $4 + 0; }
    END {
      if (rows==0) { print "No rows for device", device_id; exit; }
      start=1;
      for (j=1; j in intv; j++) {
        interval = intv[j] + 0;
        expected = int(duration/interval) + 1;
        end = start + expected - 1;
        if (end > rows) end = rows;
        count = (end - start + 1);
        in_order=1;
        for (k=start+1; k<=end; k++) { if (seq[k] != seq[k-1] + 1) { in_order=0; break; } }
        perc = (count/expected)*100.0;
        report = (perc >= 99.0) ? "✅ sufficient packets" : "❌ insufficient packets";
        order  = (in_order) ? "✅ sequence numbers OK" : "❌ sequence numbers OUT OF ORDER";
        printf "Interval %ds: %d/%d packets received (%.2f%%) %s, %s\n", interval, count, expected, perc, report, order;
        start = end + 1;
      }
    }
  ' "$CSV_DST"

done

echo "✅ Baseline test complete (5 runs)."
