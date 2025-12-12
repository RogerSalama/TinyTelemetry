#!/usr/bin/env bash
# baseline.sh — Baseline run (no impairment)

set -euo pipefail

# 1) Reset any previous netem settings on loopback (ignore errors)
if command -v sudo >/dev/null 2>&1; then
  sudo tc qdisc del dev lo root 2>/dev/null || true
else
  tc qdisc del dev lo root 2>/dev/null || true
fi

# 2) Start the server in the background (writes to logs/iot_device_data.csv)
python3 udpsrv.py > server_baseline.log 2>&1 &
SERVER_PID=$!
echo "Server started with PID ${SERVER_PID}"

# 3) Small wait to ensure server is listening
sleep 1

# 4) Run the client in foreground (uses its defaults)
python3 udpclnt.py > client_baseline.log 2>&1
echo "Client finished."

# 5) Give the server a moment to process + flush buffer
sleep 1

# 6) Stop the server with SIGINT (KeyboardInterrupt) for graceful shutdown
if ps -p "${SERVER_PID}" >/dev/null 2>&1; then
  kill -INT "${SERVER_PID}" || true
  # wait briefly for exit
  sleep 1
  if ps -p "${SERVER_PID}" >/dev/null 2>&1; then
    echo "Server still running; sending SIGTERM..."
    kill "${SERVER_PID}" || true
  fi
  echo "Server stopped."
else
  echo "Server already exited."
fi

# 7) Collect artifacts into logs/
mkdir -p logs

# Copy CSV to scenario file
if [ -f "logs/iot_device_data.csv" ]; then
  cp "logs/iot_device_data.csv" "logs/baseline.csv"
  echo "CSV saved to logs/baseline.csv"
else
  echo "No CSV found at logs/iot_device_data.csv — did the server receive data?"
fi

# Copy metrics (written on SIGINT shutdown) if present
if [ -f "logs/metrics.json" ]; then
  cp "logs/metrics.json" "logs/baseline_metrics.json"
  echo "Metrics saved to logs/baseline_metrics.json"
else
  echo "No metrics.json found — ensure server was stopped via SIGINT."
fi

echo "Baseline test complete."
