ECHOP v1 - Efficient Compact Header Optimization Protocol
Overview
Lightweight UDP protocol for IoT telemetry with 10-byte headers, smart compression, and NACK-based reliability.


Quick Start
Install:

git clone https://github.com/RogerSalama/ECHOP-Network_Protocol.git
cd ECHOP-Network_Protocol
chmod +x baseline.sh delay.sh loss.sh
mkdir -p logs data


Configure devices in device_config.txt:
1, celsius, data/temperature.txt
2, volts, data/voltage.txt


Run:
Server: python3 udpsrv.py

Client: python3 udpclnt.py 1 60 "1,5,30"


Testing
./baseline.sh - No network impairment

./delay.sh - 100ms delay + jitter

./loss.sh - 5% packet loss


Features
10-byte fixed header

Dynamic int32/float64 compression

XOR stream cipher encryption

NACK-based retransmission

Timestamp reordering buffer

CSV logging and metrics


Demo
https://drive.google.com/drive/folders/1XC33gCCz_gk7otickG4abKQIfXO1v2AI


Protocol: ECHOP v1 | Port: 12001 | Max Payload: 200 bytes

