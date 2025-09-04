#!/bin/bash
set -eux

# Update and upgrade packages
apt-get update && apt-get upgrade -y

# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs git build-essential

# Install PM2 globally
npm install -g pm2

# Clone terminal backend code
mkdir -p /opt/terminal-server
cd /opt/terminal-server
git clone https://github.com/getsoss/learn-kubernetes .
npm install

# Start websocket server
pm2 start websocket-server.js --name websocket-server
pm2 save
pm2 startup

# Register session with proxy
curl -X POST http://{{PROXY_IP}}:8080/register-session \
  -H "Content-Type: application/json" \
  -d '{"sessionId": "{{SESSION_ID}}", "vmIp": "'$(hostname -I | awk '{print $1}')"'}'
