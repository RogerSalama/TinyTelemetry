# IoT UDP Protocol Test Suite

This project implements and evaluates a custom IoT communication protocol using UDP sockets. It simulates sensor data transmission from a client device to a server, performing baseline, delay, and packet loss experiments.

The goal is to analyze the reliability and performance of the designed UDP-based protocol under various network conditions.

---

## Project Structure

```
protocol.py        → Defines the UDP protocol header structure and parsing logic  
udpclnt.py         → Client script that sends INIT, DATA, and HEARTBEAT packets  
udpsrv.py          → Server script that receives packets, logs data, and detects duplicates/gaps  
baseline.sh    → Runs the baseline (no delay/loss) test  
delay.sh           → Runs the 100ms delay + 10ms jitter test  
loss.sh        → Runs the 5% packet loss test  
sensor_values.txt  → Sample sensor readings used by the client  
logs/              → Folder where CSV log files and test outputs are stored  
```

---

## Features

- Custom 9-byte UDP header with fields:
  - Device ID  
  - Batch count  
  - Sequence number  
  - Timestamp and milliseconds  
  - Protocol version  
  - Message type (INIT / DATA / HEARTBEAT)

- **Client (udpclnt.py):**
  - Reads sensor readings from `sensor_values.txt`
  - Sends sensor data packets to the server at set intervals
  - Sends heartbeat packets periodically (every 10 seconds)
  - Supports multiple test intervals for 60-second test rounds

- **Server (udpsrv.py):**
  - Receives and parses packets
  - Logs packet details with timestamps
  - Detects duplicates, missing packets, and delay
  - Saves structured CSV output and prints test summary

- **Automated test scripts:**
  - `run_baseline.sh` → normal condition
  - `delay.sh` → adds 100 ms delay and ±10 ms jitter
  - `run_loss.sh` → simulates 5% random packet loss

---

## Requirements

- Python 3.8 or later  
- Linux / WSL / macOS terminal  
- `tc` (Traffic Control) tool for simulating delay or loss  
  Install using:  
  ```bash
  sudo apt install iproute2
  ```
- `sensor_values.txt` file containing comma-separated sensor data, for example:
  ```
  22.5,23.0,21.8
  24.1,24.3,24.0
  25.2,25.4,25.1
  ```

---

## How to Run Tests

## Linux/macOS
### 1. Baseline Test (no delay/loss)
```bash
sudo ./baseline.sh
```
Runs the system under normal network conditions.  
Results saved in `logs/baseline.csv`.

### 2. Delay and Jitter Test
```bash
sudo ./delay.sh
```
Adds 100 ms delay and ±10 ms jitter to network traffic.  
Results saved in `logs/delay_100ms.csv`.

### 3. Packet Loss Test
```bash
sudo ./run_loss.sh
```
Simulates 5% packet loss.  
Results saved in `logs/loss_5percent.csv`.


## Windows (Using Git Bash)
### 1. Baseline Test (no delay/loss)
# Run Git Bash as Administrator, then:
```bash
./baseline.sh
```
---

## Output Files

Each experiment generates the following files:

- `server_*.log` — Server log with all received packets and detected issues  
- `client_*.log` — Client log with sent packets and timing information  
- `logs/*.csv` — CSV files with structured test results  

CSV columns:
| Column | Description |
|--------|-------------|
| server_timestamp | Timestamp when the server received the packet |
| device_id | Device identifier |
| batch_count | Number of readings per packet |
| sequence_number | Sequential number for order tracking |
| device_timestamp | Original timestamp from sender |
| message_type | INIT / DATA / HEARTBEAT |
| delay_seconds | Measured packet delay |
| duplicate_flag | 1 if packet was duplicate |
| gap_flag | 1 if a packet gap was detected |

---

## Example Summary Output

When you stop the server (Ctrl + C), a summary is printed:
```
=== Baseline Test Summary ===
Total received: 180
Missing packets: 3
Duplicate packets: 2
Delivery rate: 98.33%
```

---

## Troubleshooting

- **Permission denied**:  
  Run scripts using `sudo` (required for `tc` commands).  
  Example:
  ```bash
  sudo ./delay.sh
  ```

- **No CSV output**:  
  Ensure `sensor_values.txt` exists and contains valid readings.

- **Port conflict**:  
  If UDP port 12000 is in use, change the `SERVER_PORT` in `udpsrv.py`.

- **To reset network delay/loss rules manually:**
  ```bash
  sudo tc qdisc del dev lo root
  ```

---

## Architecture Overview

```
       +-----------------------+
       |    UDP Client (IoT)   |
       |-----------------------|
       |  • Sends INIT         |
       |  • Sends DATA         |
       |  • Sends HEARTBEAT    |
       +-----------+-----------+
                   |
                   | UDP Packets
                   v
       +-----------+-----------+
       |     UDP Server        |
       |-----------------------|
       |  • Receives packets   |
       |  • Logs CSV data      |
       |  • Detects loss/gaps  |
       +-----------+-----------+
                   |
                   v
        +---------------------- +
        |  Logs / CSV Outputs  |
        +---------------------- +
```

---

## License

This project is provided for **educational and experimental use** to support IoT protocol design and performance testing coursework.
