#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv || exit 1
fi
source .venv/bin/activate
pip -q install -r requirements.txt || exit 1
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Vytvořil jsem .env. Otevři ho a doplň TELEGRAM_BOT_TOKEN a TELEGRAM_CHAT_ID."
  open -e .env
  exit 0
fi
python main.py
