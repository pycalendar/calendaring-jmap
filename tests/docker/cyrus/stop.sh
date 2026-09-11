#!/bin/bash
# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Stop script for Cyrus IMAP test server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping Cyrus and removing volumes..."
docker-compose down -v

echo "✓ Cyrus stopped and volumes removed"
