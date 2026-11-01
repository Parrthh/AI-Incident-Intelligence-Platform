import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.worker.runner import run_worker


async def test_worker_heartbeats_until_stopped() -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(stop, heartbeat_seconds=0.01))

    await asyncio.sleep(0.05)
    assert not task.done()

    stop.set()
    await asyncio.wait_for(task, timeout=1)  # stops promptly, doesn't wait a full interval


async def test_worker_stops_immediately_when_already_signalled() -> None:
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(run_worker(stop, heartbeat_seconds=60), timeout=1)


def test_api_runs_worker_in_process_when_enabled() -> None:
    settings = Settings(env=Environment.TEST, run_worker_in_api=True, _env_file=None)  # pyright: ignore[reportCallIssue]
    app = create_app(settings)

    with TestClient(app) as client:
        task: asyncio.Task[None] | None = app.state.worker_task
        assert task is not None
        assert not task.done()
        assert client.get("/healthz").status_code == 200

    # Leaving the block ran shutdown: the worker was stopped, not left running.
    assert task.done()
    assert not task.cancelled()  # it stopped cleanly within the grace period


def test_api_does_not_run_worker_by_default(app: FastAPI) -> None:
    with TestClient(app):
        assert app.state.worker_task is None
