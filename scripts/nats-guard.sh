#!/bin/bash
NATS_BIN="/usr/local/Cellar/nats-server/2.11.3/bin/nats-server"
CONFIG="/Users/yangzs/.openclaw/config/nats-server.conf"
LOG="/Users/yangzs/.openclaw/logs/nats-guard.log"
PORT=4222

echo "[$(date '+%H:%M:%S')] nats-guard starting" >> "$LOG"

# Kill zombies
ZOMBIE=$(lsof -ti :$PORT 2>/dev/null)
if [ -n "$ZOMBIE" ]; then
    echo "[$(date '+%H:%M:%S')] zombie on $PORT, killing PID $ZOMBIE" >> "$LOG"
    kill -9 $ZOMBIE 2>/dev/null
    sleep 2
fi

echo "[$(date '+%H:%M:%S')] starting nats-server" >> "$LOG"
"$NATS_BIN" -c "$CONFIG"
EXIT=$?
echo "[$(date '+%H:%M:%S')] nats-server exited with $EXIT" >> "$LOG"
exit $EXIT
