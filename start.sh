#!/bin/bash
set -e

echo "Starting Freqtrade paper trading bot..."

# Write env vars into .env file (Railway injects as env vars, not a file)
cat > /freqtrade/.env <<EOF
DISCORD_WEBHOOK_TRADING=${DISCORD_WEBHOOK_TRADING}
DISCORD_WEBHOOK_ALERTS=${DISCORD_WEBHOOK_ALERTS}
DISCORD_WEBHOOK_TRADE_ALERTS=${DISCORD_WEBHOOK_TRADE_ALERTS}
FREQTRADE_API_USER=${FREQTRADE_API_USER:-admin}
FREQTRADE_API_PASSWORD=${FREQTRADE_API_PASSWORD}
EOF

export PROJECT_ROOT=/freqtrade

# Start Freqtrade in background
freqtrade trade \
  --config /freqtrade/user_data/config_paper.json \
  --strategy RSI_MA_Strategy \
  --logfile /freqtrade/user_data/logs/freqtrade.log &

FT_PID=$!
echo "Freqtrade started (PID $FT_PID)"

# Wait for Freqtrade API to be ready (poll instead of fixed sleep)
echo "Waiting for API to be ready..."
for i in $(seq 1 24); do
  if curl -sf -u "${FREQTRADE_API_USER:-admin}:${FREQTRADE_API_PASSWORD}" \
      http://127.0.0.1:8080/api/v1/ping > /dev/null 2>&1; then
    echo "API is up after ${i}*5 seconds"
    break
  fi
  echo "  Attempt $i/24 - waiting 5s..."
  sleep 5
done

# Start Discord update loop in foreground (keeps container alive)
echo "Starting Discord update loop..."
python /freqtrade/execution/crypto_hourly_update.py --loop
