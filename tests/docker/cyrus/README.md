<!--- SPDX-FileCopyrightText: 2026 calendaring-jmap contributors -->
<!--- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Testing with Cyrus IMAP

Docker setup for running integration tests against a real Cyrus IMAP JMAP server.

## Requirements

Docker and docker-compose. If Docker isn't available, Cyrus-dependent tests are skipped automatically.

## Usage

```bash
cd tests/docker/cyrus
./start.sh
```

This starts the container, waits for it to become ready, and creates the scheduling ACLs the integration tests need. Cyrus is then available at `http://localhost:8802`.

Run the integration tests from the repo root:

```bash
pytest src/calendaring_jmap/tests/test_jmap_integration.py
```

To stop: `./stop.sh` (also removes the container's volumes, so the next start is a fresh instance).

## Configuration

Pre-created test users: `user1` through `user5`, password `x` for all.

JMAP session URL: `http://localhost:8802/.well-known/jmap`

## Image

Pinned by digest in `docker-compose.yml` rather than `:latest`: this image only publishes rolling tags (`latest`/`bookworm`/`master`, all identical), no versioned releases, so pinning a tag wouldn't actually give reproducibility. Bump the digest deliberately when picking up a newer Cyrus build.

## Troubleshooting

```bash
docker-compose logs -f cyrus   # view logs
docker-compose restart cyrus   # restart
docker-compose down -v         # stop and wipe all data
```
