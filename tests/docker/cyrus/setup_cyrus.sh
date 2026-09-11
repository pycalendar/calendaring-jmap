#!/bin/bash
# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Setup script for Cyrus IMAP test server with JMAP support

set -e

CONTAINER_NAME="cyrus-test"
TEST_USER="user1"  # Use pre-created user (user1-user5 are created automatically)
TEST_PASSWORD="x"  # Cyrus test server uses 'x' as default password

echo "Waiting for Cyrus server to be ready..."
max_attempts=30
for i in $(seq 1 $max_attempts); do
    # Accept any HTTP response (including 404) as a sign that the server is up
    if curl -s http://localhost:8802/ 2>/dev/null | grep -q .; then
        echo "✓ Cyrus HTTP server is ready"
        break
    fi
    if [ $i -eq $max_attempts ]; then
        echo "✗ Cyrus server did not become ready in time"
        exit 1
    fi
    echo -n "."
    sleep 2
done

echo ""
echo "Verifying JMAP access..."
# Cyrus's JMAP session endpoint can take additional time to initialize after HTTP is ready
max_jmap_attempts=60  # 2 minutes at 2s intervals
for i in $(seq 1 $max_jmap_attempts); do
    if curl -s -u ${TEST_USER}:${TEST_PASSWORD} -L http://localhost:8802/.well-known/jmap 2>/dev/null | grep -q "urn:ietf:params:jmap:calendars"; then
        echo "✓ JMAP is accessible"
        break
    fi
    if [ $i -eq $max_jmap_attempts ]; then
        echo "Warning: JMAP access test failed after ${max_jmap_attempts} attempts, but continuing..."
        break
    fi
    echo -n "."
    sleep 2
done

echo ""
echo "Granting scheduling ACL rights to pre-provisioned users..."
# A user's #calendars mailboxes are created lazily on first access, so touch
# each user's JMAP session before cyradm can set ACLs on their Outbox.
for user in user1 user2 user3 user4 user5; do
    curl -s -u "${user}:${TEST_PASSWORD}" -L "http://localhost:8802/.well-known/jmap" > /dev/null 2>&1
done

# cyradm lives inside the container, not on the host running this script.
# MSYS2_ARG_CONV_EXCL stops Git Bash on Windows from mangling the container's
# /usr/cyrus/... path into a host path; it's a no-op on Linux and macOS.
for user in user1 user2 user3 user4 user5; do
    printf 'sam user.%s.#calendars.Outbox %s lrswipkxtecdan789\r\n' "$user" "$user"
done | MSYS2_ARG_CONV_EXCL="*" docker exec -i "$CONTAINER_NAME" sh -c '/usr/cyrus/bin/cyradm --auth PLAIN -u admin -w admin --notls --port 8143 localhost' 2>/dev/null || \
    echo "Warning: could not set scheduling ACL rights (cyradm failed)"

echo ""
echo "✓ Cyrus setup complete!"
echo ""
echo "Credentials:"
echo "  Test user: ${TEST_USER} / ${TEST_PASSWORD}"
echo "  JMAP session URL: http://localhost:8802/.well-known/jmap"
echo ""
