#!/bin/bash
# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Quick start script for Stalwart test server
#
# Usage: ./start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Creating and starting Stalwart container..."
docker-compose up -d

echo "Running setup (waits for HTTP readiness, creates domain and test user)..."
bash "$SCRIPT_DIR/setup_stalwart.sh"

echo ""
echo "Run tests from the repo root:"
echo "  pytest src/calendaring_jmap/tests/test_jmap_integration.py"
echo ""
echo "To stop Stalwart: ./stop.sh"
echo "To view logs: docker-compose logs -f stalwart"
