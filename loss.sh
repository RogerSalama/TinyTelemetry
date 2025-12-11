#!/bin/bash
# run_loss.sh

# Reset and apply 5% packet loss
sudo tc qdisc del dev lo root 2>/dev/null
sudo tc qdisc add dev lo root netem loss 20%

# Start the UDP server
python3 udpsrv.py > server_loss.log 2>&1 &
SERVER_PID=$!

# Run the client
python3 udpclnt.py > client_loss.log 2>&1

# Stop the server after client finishes
kill $SERVER_PID

# Create log folder
mkdir -p logs

# Move CSV only if it exists
if [ -f "logs/iot_device_data.csv" ]; then
    mv logs/iot_device_data.csv logs/loss_5percent.csv
    echo "Saved results to logs/loss_5percent.csv"
elif [ -f "iot_device_data.csv" ]; then
    mv iot_device_data.csv logs/loss_5percent.csv
    echo "Saved results to logs/loss_5percent.csv"
else
    echo "Warning: No CSV log found — maybe no DATA messages were received."
fi

# Reset network conditions
sudo tc qdisc del dev lo root

echo "Loss test complete."
