# ruff: noqa: S101, D100, INP001, SLF001, PLR2004
# ty:ignore[invalid-assignment]

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybeckerplus.client import BeckerClient
from pybeckerplus.constants import Action
from pybeckerplus.device import CentronicDevice


@pytest.mark.asyncio
async def test_device_activity_polling_backoff() -> None:
    """Test that device activity polling occurs and respects backoff increments."""
    mock_client = MagicMock(spec=BeckerClient)
    mock_client.enable_polling = True
    mock_client.send = AsyncMock()
    mock_client.get_next_cnt.return_value = 1

    device = CentronicDevice("a0dc04fffe123456", mock_client)
    device.moving = True

    # Track sleep durations to verify backoff logic
    sleep_durations = []

    _original_asyncio_sleep = asyncio.sleep  # Capture original asyncio.sleep

    async def mock_sleep(delay: float) -> None:
        sleep_durations.append(delay)
        # On the 3rd poll, simulate the motor stopping to trigger loop exit
        if len(sleep_durations) == 3:
            device.moving = False
            device.available = True
        await _original_asyncio_sleep(0)  # Call original sleep to yield control

    with patch("asyncio.sleep", side_effect=mock_sleep):
        # Trigger polling via an action
        await device.action(Action.UP)

        # Wait for the background task to complete (it should exit due to moving=False)
        if device._poll_task:
            await device._poll_task

    # Assertions:
    # 1 call from action(), plus 3 iterations of the polling loop
    assert mock_client.send.call_count == 4

    # Verify backoff values: 3.5, 3.5 * 1.3, 4.55 * 1.3
    assert sleep_durations[0] == 3.5
    assert sleep_durations[1] == pytest.approx(4.55)
    assert sleep_durations[2] == pytest.approx(5.915)


@pytest.mark.asyncio
async def test_device_polling_exit_on_timeout() -> None:
    """Test that the device poll loop eventually gives up after the timeout."""
    mock_client = MagicMock(spec=BeckerClient)
    mock_client.enable_polling = True
    mock_client.send = AsyncMock()

    device = CentronicDevice("a0dc04fffe123456", mock_client)
    device.moving = True  # Keep it "moving" so it doesn't exit naturally

    # Mock time to simulate 11 minutes passing immediately
    start_time = 1000.0
    with patch("asyncio.get_running_loop") as mock_loop:
        loop = MagicMock()
        # First call for start_time, second call inside the loop to check elapsed
        loop.time.side_effect = [start_time, start_time + 700.0]
        mock_loop.return_value = loop

        with patch("asyncio.sleep", AsyncMock()):
            # Run one iteration of the loop logic manually or via the task
            # For simplicity, we just verify the logic in _run_activity_poll
            # handles the delta
            await device._run_activity_poll()

    # Loop should have exited after one check because 700 > 600
    assert mock_client.send.call_count == 1


@pytest.mark.asyncio
async def test_client_maintenance_polling_interval() -> None:
    """Test that the client maintenance loop schedules the long-term poll."""
    client = BeckerClient(port="LOOPBACK")
    client.send = AsyncMock()

    # Use a future to signal when the 30-minute sleep is reached
    reached_maintenance = asyncio.Event()

    _original_asyncio_sleep = asyncio.sleep  # Capture original asyncio.sleep

    async def mock_sleep(delay: float) -> None:
        if delay == 1800:
            reached_maintenance.set()
        await _original_asyncio_sleep(0)  # Call original sleep to yield control

    with patch("asyncio.sleep", side_effect=mock_sleep):
        # Start monitoring
        task = asyncio.create_task(client._run_monitoring())

        # Wait for the 1800s sleep to be triggered
        await asyncio.wait_for(reached_maintenance.wait(), timeout=1.0)

        task.cancel()
