# Hyperliquid Position Copier

Copy trading bot for Hyperliquid. Monitors a target wallet and replicates positions in real-time.

## Setup

Environment variables (required):
```bash
AGENT_PRIVATE_KEY=0x...
MAIN_ACCOUNT_ADDRESS=0x...
TARGET_ADDRESS=0x...
```

## Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure .env
cp .env.example .env
# Edit .env with your credentials

# Run
python position_copier.py
```

## Deploy to DigitalOcean Droplet

Recommended for accessing the live interactive dashboard.

1. **Create Droplet** ($6/month Basic):
   - Ubuntu 22.04 LTS
   - Basic plan (1GB RAM sufficient)

2. **SSH into Droplet**:
   ```bash
   ssh root@your_droplet_ip
   ```

3. **Install Docker**:
   ```bash
   curl -fsSL https://get.docker.com -o get-docker.sh
   sh get-docker.sh
   apt-get install -y docker-compose
   ```

4. **Clone and Setup**:
   ```bash
   git clone https://github.com/vkovic/hyperliquid-copy-bot.git
   cd hyperliquid-copy-bot
   
   # Create .env file
   nano .env
   # Paste your credentials and save (Ctrl+X, Y, Enter)
   ```

5. **Deploy**:
   ```bash
   # Quick deploy (automated)
   ./deploy-droplet.sh
   
   # Or manual:
   docker-compose up -d
   ```

6. **Run the Script**:
   ```bash
   # The container starts but doesn't auto-run the script
   # Run manually to see live dashboard:
   docker exec -it hyperliquid-position-copier python position_copier.py
   
   # Or enter container shell first:
   docker exec -it hyperliquid-position-copier /bin/bash
   python position_copier.py
   ```

**Management Commands**:
```bash
docker-compose restart    # Restart
docker-compose logs -f    # View logs
docker-compose down       # Stop
docker-compose up -d      # Start
```

## Docker (Optional)

```bash
docker-compose up -d          # Start
docker-compose logs -f        # Logs
docker-compose down           # Stop
```

## Notes

- Position copier only copies **new** positions opened after script starts
- Uses isolated margin for safety
- Auto-calculates position size based on account balance ratio

