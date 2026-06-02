# Copyright 2026 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Per-client ``StoreManager``: data-plane creds inherit the active session.

Today the module-level ``mlrun.datastore.store_manager`` is process-global:
constructed once at import, and the datastores it creates read
``V3IO_ACCESS_KEY`` from the host process's env when no explicit ``secrets``
are passed. AIRun's source-tarball upload uses exactly that path
(``store_manager.set(None)``; ``datastore.upload(...)`` — see
``airun/service/engines/mlrun/source_upload.py``), so every per-user upload
authenticates as the service-account regardless of who made the request.

Inside a :class:`~mlrun.Client.session`, the active client's credentials
must propagate to datastores constructed via ``store_manager``. Outside any
session, the legacy env-driven path is unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

import mlrun
from mlrun import Client, Credentials


@pytest.fixture(autouse=True)
def _mock_dbpath(monkeypatch):
    monkeypatch.setattr(mlrun.mlconf, "dbpath", "https://mock-server")


@pytest.fixture
def fresh_store_manager():
    """Isolated ``StoreManager`` per test — the module-level singleton's
    cache and ``_secrets`` would otherwise leak between tests."""
    return mlrun.datastore.datastore.StoreManager()


def _store_token(store_manager, url: str) -> str | None:
    """The V3IO access key the datastore would authenticate with."""
    obj = store_manager.object(url)
    return obj._store.token


def test_session_token_propagates_to_v3io_store(monkeypatch, fresh_store_manager):
    """Inside ``Client.session()``, a V3ioStore constructed via
    ``store_manager`` must use the client's token — not the process's
    ``V3IO_ACCESS_KEY`` env (the service-account in a multi-user proxy)."""
    monkeypatch.setenv("V3IO_ACCESS_KEY", "svc-tok")

    client = Client(credentials=Credentials(token="user-token"))
    with client.session():
        token = _store_token(fresh_store_manager, "v3io://some-host/some/path")

    assert token == "user-token", (
        f"V3ioStore saw foreign token {token!r}; expected the active Client's "
        f"bearer. RED until StoreManager consults get_active_client()."
    )


def test_no_session_falls_back_to_env(monkeypatch, fresh_store_manager):
    """Outside any ``Client.session()``, behavior is unchanged: the
    datastore reads ``V3IO_ACCESS_KEY`` from env (legacy path)."""
    monkeypatch.setenv("V3IO_ACCESS_KEY", "svc-tok")

    token = _store_token(fresh_store_manager, "v3io://some-host/some/path")

    assert token == "svc-tok"


def test_concurrent_sessions_dont_cross_credentials(monkeypatch, fresh_store_manager):
    """Two concurrent ``asyncio.Task``s under two ``Client.session()``s
    each see their own credentials at the datastore layer — no cross-talk
    through ``store_manager``'s cache."""
    monkeypatch.setenv("V3IO_ACCESS_KEY", "svc-tok")

    client_a = Client(credentials=Credentials(token="user-a-token"))
    client_b = Client(credentials=Credentials(token="user-b-token"))

    async def _capture(client, barrier: asyncio.Event) -> str | None:
        with client.session():
            await barrier.wait()
            return _store_token(fresh_store_manager, "v3io://some-host/some/path")

    async def _drive() -> tuple[str | None, str | None]:
        barrier = asyncio.Event()
        task_a = asyncio.create_task(_capture(client_a, barrier))
        task_b = asyncio.create_task(_capture(client_b, barrier))
        await asyncio.sleep(0)  # let both tasks enter their sessions
        barrier.set()
        return await asyncio.gather(task_a, task_b)

    a_token, b_token = asyncio.run(_drive())
    assert a_token == "user-a-token", f"task A saw foreign token: {a_token!r}"
    assert b_token == "user-b-token", f"task B saw foreign token: {b_token!r}"


def test_session_exit_restores_env_fallback(monkeypatch, fresh_store_manager):
    """After ``Client.session()`` exits, ``store_manager`` returns to the
    legacy env-driven path; the client's token does not linger in the cache."""
    monkeypatch.setenv("V3IO_ACCESS_KEY", "svc-tok")

    client = Client(credentials=Credentials(token="user-token"))
    with client.session():
        _store_token(fresh_store_manager, "v3io://some-host/some/path")

    post_token = _store_token(fresh_store_manager, "v3io://some-host/some/path")
    assert post_token == "svc-tok"
