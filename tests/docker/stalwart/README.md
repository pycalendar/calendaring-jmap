<!--- SPDX-FileCopyrightText: 2026 calendaring-jmap contributors -->
<!--- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Testing with Stalwart

Docker setup for running integration tests against a real Stalwart JMAP server. Cyrus doesn't implement `PushSubscription/get`/`/set` at all, so push notification integration tests run only against Stalwart. Most other integration tests run against both servers. Neither server implements the JMAP Tasks draft, so there are no task integration tests against either.

## Requirements

Docker and docker-compose. If Docker isn't available, Stalwart-dependent tests are skipped automatically.

## Usage

```bash
cd tests/docker/stalwart
./start.sh
```

This starts the container, waits for it to become ready, and creates the test domain and users via Stalwart's JMAP management API. Stalwart is then available at `http://localhost:8809`.

Run the integration tests from the repo root:

```bash
pytest src/calendaring_jmap/tests/test_jmap_integration.py
```

To stop: `./stop.sh`. Stalwart's data directory is a `tmpfs` mount, not a named volume, so the next `./start.sh` is always a fresh instance regardless.

## Configuration

Test user: `testuser@example.org`, password `testcaldav`. Additional users `user1`/`user2`/`user3` (passwords `caldavtest1`/`caldavtest2`/`caldavtest3`) exist for multi-account scheduling scenarios.

Admin: `admin` / `adminpass`, web UI at `http://localhost:8809/admin/`.

JMAP session URL: `http://localhost:8809/.well-known/jmap`

## Image

Pinned to a specific released version (`v0.16.21` as of writing) rather than `:latest`, so the server's behavior doesn't drift out from under the tests. Bump deliberately when picking up a newer Stalwart release.

## Troubleshooting

```bash
docker-compose logs -f stalwart   # view logs
docker-compose restart stalwart   # restart
docker-compose down -v            # stop (-v is a no-op here; the data directory is a tmpfs mount, not a named volume)
```
