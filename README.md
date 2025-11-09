# Hyperliquid Trading Tools

Collection of tools for monitoring and interacting with Hyperliquid trading platform.

## Tools

### 📊 fetch_assets.py
Fetch and display assets for any Hyperliquid address.
- Shows spot token balances with USD values
- Shows futures positions with PNL, leverage, and liquidation prices
- Quick one-time snapshot

**Usage:**
```bash
python fetch_assets.py <address>

# Example
python fetch_assets.py 0x42b9493c505adf4b37dda028b6b47f7b5e8a5d1f
```

### 👁️ position_monitor.py
Real-time monitoring of position changes for a target address.
- Live dashboard with position tracking
- Detects opened, closed, increased, decreased positions
- Historical view of all position changes

**Usage:**
```bash
export TARGET_ADDRESS='0x...'
python position_monitor.py
```

### 📍 address_tracker.py
Interactive address tracker with futures positions, spot holdings, and trade history.
- Account value and PNL tracking
- Recent trades history
- Prompts for address interactively

**Usage:**
```bash
python address_tracker.py
```

### 🎯 hyperliquid_monitor.py
Market-wide trade monitoring for big moves.
- Tracks all trades across all coins
- Alerts on trades above threshold ($50K default)
- Shows biggest trades and recent activity

**Usage:**
```bash
python hyperliquid_monitor.py
```

### 🔄 position_copier.py
Copy trading bot that replicates positions from a target wallet.
- Real-time position mirroring
- Automatic trade execution
- Requires agent wallet with funds

**Usage:**
```bash
export AGENT_PRIVATE_KEY='0x...'
export MAIN_ACCOUNT_ADDRESS='0x...'
export TARGET_ADDRESS='0x...'
python position_copier.py
```

## Setup

Environment variables (required):
```bash
AGENT_PRIVATE_KEY=0x...
MAIN_ACCOUNT_ADDRESS=0x...
TARGET_ADDRESS=0x...
```

## Run Locally

```bash
# Clone repository
git clone https://github.com/vkovic/hyperliquid-copy-bot.git
cd hyperliquid-copy-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env  # Edit with your credentials

# Run
python position_copier.py
```

## Deploy to DigitalOcean Droplet

### Quick Setup

1. **SSH and Setup**:
   ```bash
   ssh root@your_droplet_ip
   
   # Install dependencies
   apt update && apt upgrade -y
   apt install -y python3 python3-pip python3-venv git tmux
   
   # Clone repo
   git clone https://github.com/vkovic/hyperliquid-copy-bot.git
   cd hyperliquid-copy-bot
   
   # Setup Python environment
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   
   # Create .env file
   nano .env
   # Paste your credentials and save (Ctrl+X, Y, Enter)
   ```

2. **Run Script**:
   ```bash
   # Direct run (stops when you disconnect)
   python position_copier.py
   
   # Or run in tmux (recommended - keeps running)
   tmux new -s copier
   python position_copier.py
   # Detach: Ctrl+B, then D
   # Reattach anytime: tmux attach -t copier
   ```