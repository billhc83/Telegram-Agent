#!/usr/bin/env bash
# Install systemd services for telegram-agent and ngrok tunnel.
# Run as: sudo bash scripts/install-services.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── 1. telegram-agent docker compose service ─────────────────────────────────
cat > /etc/systemd/system/telegram-agent.service <<EOF
[Unit]
Description=Telegram Agent (Docker Compose)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${PROJECT_DIR}
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
Restart=no
User=billhc83

[Install]
WantedBy=multi-user.target
EOF

echo "Written: /etc/systemd/system/telegram-agent.service"

# ── 2. ngrok tunnel service ───────────────────────────────────────────────────
cat > /etc/systemd/system/ngrok-telegram.service <<EOF
[Unit]
Description=ngrok tunnel for Telegram Agent (n8n → dispatch-tribunal-nature.ngrok-free.dev)
After=network-online.target telegram-agent.service
Wants=network-online.target
Requires=telegram-agent.service

[Service]
Type=simple
ExecStart=/snap/bin/ngrok http 5678 --domain=dispatch-tribunal-nature.ngrok-free.dev --log=stdout
Restart=always
RestartSec=10
User=billhc83
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "Written: /etc/systemd/system/ngrok-telegram.service"

# ── 3. Reload, enable, and start ─────────────────────────────────────────────
systemctl daemon-reload
systemctl enable telegram-agent.service
systemctl enable ngrok-telegram.service
echo ""
echo "Services enabled. They will start automatically on next reboot."
echo ""
echo "To start them now (without rebooting):"
echo "  systemctl start telegram-agent"
echo "  systemctl start ngrok-telegram"
echo ""
echo "To check status:"
echo "  systemctl status telegram-agent"
echo "  systemctl status ngrok-telegram"
echo "  journalctl -u ngrok-telegram -f"
