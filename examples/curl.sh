#!/usr/bin/env sh
# The API with curl, against a local `tempo serve` (demo mode needs no keys).
BASE=${TEMPO_URL:-http://127.0.0.1:8000}

curl -s "$BASE/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "tempo/auto", "messages": [{"role": "user", "content": "hi"}]}'
echo

curl -s "$BASE/v1/models" | head -c 600
echo
