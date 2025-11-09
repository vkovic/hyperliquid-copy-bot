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

## Deploy to DigitalOcean App Platform

1. Push to GitHub:
   ```bash
   git push origin master
   ```

2. Create app in [DigitalOcean](https://cloud.digitalocean.com/apps):
   - Connect your repository
   - Will auto-detect `Dockerfile`

3. Add environment variables in DO UI:
   - `AGENT_PRIVATE_KEY` (encrypted)
   - `MAIN_ACCOUNT_ADDRESS` (encrypted)
   - `TARGET_ADDRESS` (encrypted)

4. Deploy

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

