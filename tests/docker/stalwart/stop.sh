#!/bin/bash
# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Stop script for Stalwart test server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping Stalwart and removing volumes..."
docker-compose down -v

echo "Stalwart stopped and volumes removed"
