# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Standalone configuration loading for calendaring-jmap.

Resolves connection parameters in priority order:

1. Explicit keyword arguments
2. Environment variables (``JMAP_URL``, ``JMAP_USERNAME``, ``JMAP_PASSWORD``,
   ``JMAP_AUTH_TYPE``, ``JMAP_TIMEOUT``)
3. A YAML config file (``JMAP_CONFIG_FILE`` env var, or
   ``~/.config/calendaring-jmap/calendar.yaml``)

Returns ``None`` if none of the three sources yields a URL, rather than
raising. Callers decide whether "no configuration" is an error.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_CONN_KEYS = {"url", "username", "password", "auth_type", "timeout"}
_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "calendaring-jmap" / "calendar.yaml"


_ENV_VARS = {
    "url": "JMAP_URL",
    "username": "JMAP_USERNAME",
    "password": "JMAP_PASSWORD",
    "auth_type": "JMAP_AUTH_TYPE",
    "timeout": "JMAP_TIMEOUT",
}


def _from_environment() -> dict[str, Any]:
    env: dict[str, Any] = {}
    for key, var in _ENV_VARS.items():
        value = os.environ.get(var)
        if value:
            env[key] = int(value) if key == "timeout" else value
    return env


def _from_config_file(config_file: str | Path | None) -> dict[str, Any]:
    path = Path(config_file) if config_file else _DEFAULT_CONFIG_PATH
    if not path.is_file():
        return {}

    import yaml

    with path.open("rb") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in _CONN_KEYS}


def get_connection_params(
    config_file: str | Path | None = None,
    **explicit_params: Any,
) -> dict[str, Any] | None:
    """Resolve JMAP connection parameters from kwargs, env vars, or a config file.

    Args:
        config_file: Explicit path to a YAML config file. Defaults to the
            ``JMAP_CONFIG_FILE`` environment variable, then
            ``~/.config/calendaring-jmap/calendar.yaml``.
        **explicit_params: ``url``, ``username``, ``password``, ``auth_type``,
            ``timeout``. A value of ``None`` is treated as "not supplied",
            not "unset": this lets a thin CLI wrapper pass every option
            through unconditionally without wiping out env vars for the
            ones the user left off.

    Returns:
        A dict of connection parameters, or ``None`` if no source yields a URL.
    """
    explicit = {k: v for k, v in explicit_params.items() if k in _CONN_KEYS and v is not None}
    if explicit.get("url"):
        return explicit

    env = _from_environment()
    if env.get("url"):
        return {**env, **explicit}

    file_config = _from_config_file(config_file or os.environ.get("JMAP_CONFIG_FILE"))
    if file_config.get("url"):
        return {**file_config, **env, **explicit}

    return None
