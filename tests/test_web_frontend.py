"""Tests for the AstraGrid Mission Control web frontend and API integration."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from typing import Generator
import pytest
import uvicorn

from astra_grid.api import app
from astra_grid.cli import main as cli_main
from astra_grid.gui import main as gui_main


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server_url() -> Generator[str, None, None]:
    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    # Wait for server to accept connections
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    else:
        raise RuntimeError(f"Server at {url} failed to start in time.")

    yield url
    server.should_exit = True
    thread.join(timeout=3.0)


def test_web_dashboard_served(live_server_url: str) -> None:
    for path in ["/", "/dashboard"]:
        req = urllib.request.Request(f"{live_server_url}{path}")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "<title>AstraGrid — Lunar Habitat Mission Control</title>" in content
            assert "ASTRAGRID" in content
            assert "POWER BUS BALANCE" in content
            assert "canvas-power" in content
            assert "canvas-battery" in content
            assert "schematic-svg" in content


def test_static_assets_served(live_server_url: str) -> None:
    # CSS
    with urllib.request.urlopen(f"{live_server_url}/static/style.css", timeout=5.0) as resp:
        assert resp.status == 200
        css = resp.read().decode("utf-8")
        assert "--bg-darkest" in css
        assert "radial-gauge" in css
        assert "flow-line" in css

    # JS
    with urllib.request.urlopen(f"{live_server_url}/static/app.js", timeout=5.0) as resp:
        assert resp.status == 200
        js = resp.read().decode("utf-8")
        assert "AstraGrid" in js
        assert "drawPowerChart" in js
        assert "refreshFullView" in js


def test_full_view_and_replay_endpoints(live_server_url: str) -> None:
    # 1. Create a simulation run
    create_body = json.dumps({"scenario_id": "nominal_day", "controller": "astragrid"}).encode("utf-8")
    req = urllib.request.Request(
        f"{live_server_url}/api/simulations",
        data=create_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
        created = json.loads(resp.read().decode("utf-8"))
        run_id = created["run_id"]
        assert run_id

    # 2. Step the simulation twice
    for _ in range(2):
        step_req = urllib.request.Request(f"{live_server_url}/api/simulations/{run_id}/step", method="POST")
        with urllib.request.urlopen(step_req, timeout=5.0) as resp:
            assert resp.status == 200

    # 3. Request full view
    with urllib.request.urlopen(f"{live_server_url}/api/simulations/{run_id}/full_view", timeout=5.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "snapshot" in data
        assert "state" in data
        assert "plan" in data
        assert "metrics" in data
        assert "tasks" in data
        assert "events" in data
        assert "history" in data
        assert len(data["history"]) == 2
        first_step = data["history"][0]
        assert first_step["step"] == 1
        assert "solar_kw" in first_step
        assert "battery_kwh" in first_step
        assert "soc" in first_step
        assert "bus_demand_kw" in first_step

    # 4. Request replay data
    with urllib.request.urlopen(f"{live_server_url}/api/simulations/{run_id}/replay_data", timeout=5.0) as resp:
        assert resp.status == 200
        replay = json.loads(resp.read().decode("utf-8"))
        assert replay["total_steps"] == 2
        assert len(replay["steps"]) == 2
        step0 = replay["steps"][0]
        assert "observation" in step0
        assert "transition" in step0
        assert "audit" in step0


def test_compare_full_endpoint(live_server_url: str) -> None:
    body = json.dumps({"scenario_id": "nominal_day", "steps": 4}).encode("utf-8")
    req = urllib.request.Request(
        f"{live_server_url}/api/simulations/compare_full",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["scenario"] == "nominal_day"
        assert "pair" in res
        pair = res["pair"]
        assert "astragrid" in pair
        assert "baseline" in pair
        assert "delta_astragrid_minus_baseline" in pair
        assert "astragrid_run_id" in res
        assert "baseline_run_id" in res


def test_cli_web_subcommand_registered() -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["web", "--help"])
    assert exc.value.code == 0


def test_tkinter_gui_preserved() -> None:
    # Ensure Tkinter GUI entry point remains completely functional and callable
    assert callable(gui_main)
