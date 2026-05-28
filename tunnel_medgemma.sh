#!/bin/bash
# Persistent SSH tunnel: remote server → MedGemma server (213.181.122.239)
# Uses ubuntu_01.pem for authentication
# Ports forwarded: 8010, 8020, 8030 (MedGemma API is at 8030)

MEDGEMMA_HOST="213.181.122.239"
PEM="/home/ubuntu/.ssh/ubuntu_01.pem"

exec autossh -M 0   -i "$PEM"   -o StrictHostKeyChecking=no   -o ServerAliveInterval=30   -o ServerAliveCountMax=5   -o ExitOnForwardFailure=yes   -N   -L 8010:127.0.0.1:8010   -L 8020:127.0.0.1:8020   -L 8030:127.0.0.1:8030   ubuntu@"$MEDGEMMA_HOST"
