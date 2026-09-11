#!/bin/bash
# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Quick start script for Cyrus IMAP test server
#
# Usage: ./start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Cleaning up previous Cyrus instance..."
docker-compose down 2>/dev/null || true

echo "Starting Cyrus IMAP server..."
docker-compose up -d

echo ""
echo "Waiting for Cyrus to initialize..."
./setup_cyrus.sh

echo "To stop Cyrus: ./stop.sh"
echo "To view logs: docker-compose logs -f cyrus"
