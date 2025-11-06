#!/bin/bash
#delay.sh

# Reset and apply 100ms delay with 10ms jitter
sudo tc qdisc del dev lo root 2>/dev/null
sudo tc qdisc add dev lo root netem delay 100ms 10ms

# Start server
python3 udpsrv.py > server_delay.log 2>&1 &
SERVER_PID=$!

# Run client
python3 udpclnt.py > client_delay.log 2>&1

# Stop server
kill $SERVER_PID

# Ensure log folder exists
mkdir -p logs

# Move CSV only if it exists
if [ -f "logs/iot_device_data.csv" ]; then
    mv logs/iot_device_data.csv logs/delay_100ms.csv
    echo " Saved results to logs/delay_100ms.csv"
elif [ -f "iot_device_data.csv" ]; then
    mv iot_device_data.csv logs/delay_100ms.csv
    echo "Saved results to logs/delay_100ms.csv"
else
    echo "Warning: No CSV log found — maybe no DATA messages were received."
fi

# Reset network conditions
sudo tc qdisc del dev lo root

echo "Delay+jitter test complete."
