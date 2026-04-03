#!/bin/bash
set -e

echo "Starting Freqtrade paper trading bot..."

# Write .env values into a file the update script can read
# (Railway injects secrets as env vars, not a .env file)
cat > /freqtrade/.env <<EOF
DISCORD_WEBHOOK_TRADING=${DISCORD_WEBHOOK_TRADING}
DISCORD_WEBHOOK_ALERTS=${DISCORD_WEBHOOK_ALERTS}
DISCORD_WEBHOOK_TRADE_ALERTS=${DISCORD_WEBHOOK_TRADE_ALERTS}
FREQTRADE_API_USER=${FREQTRADE_API_USER:-admin}
FREQTRADE_API_PASSWORD=${FREQTRADE_API_PASSWORD}
EOF

# Override PROJECT_ROOT for the update script to find .env
export PROJECT_ROOT=/freqtrade

# Start Freqtrade in background
freqtrade trade \
  --config /freqtrade/user_data/config_paper.json \
  --strategy RSI_MA_Strategy \
  --logfile /freqtrade/user_data/logs/freqtrade.log &

FT_PID=$!
echo "Freqtrade started (PID $FT_PID)"

# Wait for API to come up
echo "Waiting for API..."
sleep 15

# Start hourly update loop in foreground (keeps container alive)
echo "Starting Discord update loop..."
python /freqtrade/execution/crypto_hourly_update.py --loop
