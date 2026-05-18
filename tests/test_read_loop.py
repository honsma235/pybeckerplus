# ruff: noqa: S101, D100, D102, D107, D205, D400, D401, D415, E501, SLF001, INP001, FBT001, TRY003, EM101
# ty:ignore[invalid-assignment, unresolved-attribute]

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pybeckerplus.client import BeckerClient
from pybeckerplus.constants import ETX, STICK_ACK, STX
from pybeckerplus.exceptions import (
    BeckerConnectionError,
    BeckerError,
    BeckerTimeoutError,
)


class MockReader:
    """Helper to simulate an asyncio.StreamReader with Hypothesis-generated chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.index = 0

    async def read(self, n: int) -> bytes:
        if self.index >= len(self.chunks):
            return b""  # Signal EOF to terminate the loop
        chunk = self.chunks[self.index]
        self.index += 1
        return chunk[:n]


@st.composite
def chaos_stream_strategy(draw: Any) -> tuple[bytes, list[str], bool]:
    """
    Generates a truly messy byte stream containing:
    - Valid packets
    - Valid ACKs
    - Stray STX/ETX (unmatched)
    - Random noise
    - ACKs embedded inside packet frames
    """
    atoms = draw(
        st.lists(
            st.one_of(
                # A valid ACK
                st.just({"type": "ACK", "bytes": STICK_ACK}),
                # A valid packet
                st.builds(
                    lambda d: {
                        "type": "PACKET",
                        "bytes": STX + d.encode() + ETX,
                        "val": d,
                    },
                    st.text(alphabet="0123456789ABCDEF", min_size=4, max_size=15),
                ),
                # Pure noise (can include random bytes that might look like STX/ETX)
                st.builds(
                    lambda b: {"type": "NOISE", "bytes": b},
                    st.binary(min_size=1, max_size=10),
                ),
                # Stray STX (Start without End)
                st.builds(
                    lambda d: {"type": "STRAY", "bytes": STX + d.encode()},
                    st.text(alphabet="0123456789ABCDEF", min_size=1, max_size=5),
                ),
                # Stray ETX (End without Start)
                st.builds(
                    lambda d: {"type": "STRAY", "bytes": d.encode() + ETX},
                    st.text(alphabet="0123456789ABCDEF", min_size=1, max_size=5),
                ),
                # Embedded ACK: STX + DATA + ACK + DATA + ETX
                st.builds(
                    lambda d1, d2: {
                        "type": "PACKET",
                        "bytes": STX + d1.encode() + STICK_ACK + d2.encode() + ETX,
                        "val": d1
                        + d2,  # We assume the client logic strips the ACK byte from the buffer
                    },
                    st.text(alphabet="0123456789ABCDEF", min_size=2, max_size=5),
                    st.text(alphabet="0123456789ABCDEF", min_size=2, max_size=5),
                ),
            ),
            min_size=3,
            max_size=15,
        )
    )

    full_stream = b"".join(atom["bytes"] for atom in atoms)

    # We determine expectations by mirroring the client's buffer processing logic.
    # 1. ACKs have high priority and are stripped from the stream.
    # 2. Packets are extracted from the remaining bytes.
    expected_packets: list[str] = []
    has_ack = False
    temp_buf = full_stream

    while temp_buf:
        ack_pos = temp_buf.find(STICK_ACK)
        stx_pos = temp_buf.find(STX)

        if ack_pos != -1:
            has_ack = True
            # Remove ACK byte and continue (Interleaved ACK handling)
            temp_buf = temp_buf[:ack_pos] + temp_buf[ack_pos + len(STICK_ACK) :]
            continue

        if stx_pos != -1:
            if stx_pos > 0:
                temp_buf = temp_buf[stx_pos:]
                continue
            etx_pos = temp_buf.find(ETX)
            if etx_pos != -1:
                # Resync logic: check for a later STX before this ETX
                last_stx = temp_buf.rfind(STX, 0, etx_pos)
                if last_stx > 0:
                    temp_buf = temp_buf[last_stx:]
                    continue
                with contextlib.suppress(UnicodeDecodeError, ValueError):
                    expected_packets.append(temp_buf[1:etx_pos].decode("ascii"))
                temp_buf = temp_buf[etx_pos + 1 :]
                continue
            # Orphaned STX check
            if temp_buf.find(STX, 1) != -1:
                temp_buf = temp_buf[1:]
                continue
            break
        break

    return full_stream, expected_packets, has_ack


@pytest.mark.asyncio
@settings(max_examples=500)
@given(
    chunks=st.lists(st.binary(min_size=0, max_size=20), min_size=1, max_size=10),
    include_ack=st.booleans(),
    packet_data=st.text(alphabet="0123456789ABCDEF", min_size=4, max_size=20),
)
async def test_read_loop_fragmentation(
    chunks: list[bytes], include_ack: bool, packet_data: str
) -> None:
    """
    Test that the read loop correctly processes ACKs and packets even when
    fragmented across multiple reads or surrounded by random noise.
    """
    # Construct a stream that includes our targets
    full_stream = b"".join(chunks)

    expected_packet = None
    if include_ack:
        full_stream += STICK_ACK

    # Add a valid framed packet
    expected_packet = packet_data
    full_stream += STX + expected_packet.encode("ascii") + ETX

    # Add some trailing junk
    full_stream += b"JUNK"

    # Re-fragment the stream into random sizes to simulate serial jitter
    stream_len = len(full_stream)
    fragmented_chunks = []
    curr = 0
    while curr < stream_len:
        # Take a random slice between 1 and 15 bytes
        size = min(stream_len - curr, 15)
        fragmented_chunks.append(full_stream[curr : curr + size])
        curr += size

    # Setup Client
    client = BeckerClient(port="LOOPBACK")
    client._reader = MockReader(fragmented_chunks)
    client._ack_waiter = asyncio.get_running_loop().create_future()

    # Mock handlers
    client._handle_packet = MagicMock()
    client._handle_disconnect = MagicMock()

    # Run the loop. It will terminate when MockReader returns b""
    await client._read_loop()

    # Assertions

    # 1. If we sent an ACK, the future should be resolved
    if include_ack:
        assert client._ack_waiter.done()
        assert client._ack_waiter.result() is True

    # 2. The framed packet should have been extracted regardless of fragmentation
    # We look for the call matching our packet_data
    client._handle_packet.assert_any_call(expected_packet)

    # 3. The loop should have called disconnect once upon reaching EOF
    assert client._handle_disconnect.call_count == 1


@pytest.mark.asyncio
@settings(max_examples=500)
@given(stream_data=chaos_stream_strategy())
async def test_read_loop_chaos(stream_data: tuple[bytes, list[str], bool]) -> None:
    """Test the read loop with randomized sequences of valid, invalid, and messy data."""
    full_stream, expected_packets, expect_ack = stream_data

    # Fragment into tiny pieces (1-5 bytes) to force the loop to reconstruct everything
    fragmented_chunks = []
    curr = 0
    while curr < len(full_stream):
        # Using 1-8 byte chunks for extreme fragmentation
        size = min(len(full_stream) - curr, 8)
        fragmented_chunks.append(full_stream[curr : curr + size])
        curr += size

    client = BeckerClient(port="LOOPBACK")
    client._reader = MockReader(fragmented_chunks)
    client._ack_waiter = asyncio.get_running_loop().create_future()
    client._handle_packet = MagicMock()
    client._handle_disconnect = MagicMock()

    await client._read_loop()

    # Assertions
    if expect_ack:
        assert client._ack_waiter.done(), "Expected an ACK to be processed"

    # Check that every valid packet we injected was caught
    assert client._handle_packet.call_count == len(expected_packets)
    for packet in expected_packets:
        # We check if the packet was handled.
        # Note: If the client doesn't strip STICK_ACK from the buffer, this might fail,
        # which is exactly what we want to find out!
        client._handle_packet.assert_any_call(packet)

    assert client._handle_disconnect.call_count == 1


@pytest.mark.asyncio
async def test_read_loop_exception_mid_stream() -> None:
    """
    Test that the read loop correctly handles exceptions that occur mid-stream
    by failing pending waiters and notifying the disconnect callback.
    """

    class FaultyReader:
        """A reader that fails after the first successful read."""

        def __init__(self) -> None:
            self.count = 0

        async def read(self, n: int) -> bytes:  # noqa: ARG002
            self.count += 1
            if self.count == 1:
                # Return some initial data (a partial packet)
                return STX + b"B0"
            # Simulate a hardware failure or USB disconnect
            raise ConnectionResetError("USB stick was unplugged")

    on_disconnect = MagicMock()
    client = BeckerClient(port="LOOPBACK", on_disconnect=on_disconnect)
    client._reader = FaultyReader()
    # Create a waiter to ensure it gets failed
    client._ack_waiter = asyncio.get_running_loop().create_future()

    # This should not raise an exception itself as the loop has its own try/except
    await client._read_loop()

    # 1. Verify the disconnect callback was notified of the specific error
    on_disconnect.assert_called_once()
    assert isinstance(on_disconnect.call_args[0][0], ConnectionResetError)

    # 2. Verify that pending waiters were failed with the same exception
    assert client._ack_waiter.done()
    with pytest.raises(ConnectionResetError, match="USB stick was unplugged"):
        await client._ack_waiter

    # 3. Verify internal connection error state
    assert isinstance(client._connection_error, ConnectionResetError)


@pytest.mark.asyncio
async def test_send_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a command fails if the stick never sends an ACK."""
    # Shorten the timeout for the test
    monkeypatch.setattr("pybeckerplus.client.ACK_TIMEOUT", 0.01)

    client = BeckerClient(port="LOOPBACK")
    mock_writer = MagicMock()
    mock_writer.drain = AsyncMock()
    client._writer = mock_writer

    # We never resolve the _ack_waiter, so it should hit the timeout
    with pytest.raises(BeckerTimeoutError, match="Stick did not acknowledge"):
        await client.send("010203")


@pytest.mark.asyncio
async def test_send_fails_when_disconnected() -> None:
    """Test that send() raises appropriate errors when not connected."""
    client = BeckerClient(port="LOOPBACK")

    # 1. Test "Not connected" (Initial state)
    with pytest.raises(BeckerError, match="Not connected"):
        await client.send("010203")

    # 2. Test "Connection lost" (After an error)
    client._connection_error = ConnectionResetError("Lost")
    with pytest.raises(BeckerConnectionError, match="Connection lost") as exc:
        await client.send("010203")

    # Ensure the original exception is chained (from ... self._connection_error)
    assert isinstance(exc.value.__cause__, ConnectionResetError)


@pytest.mark.asyncio
async def test_close_fails_pending_waiters() -> None:
    """Test that calling close() fails any active command waiters immediately."""
    client = BeckerClient(port="LOOPBACK")
    # Mock enough of the connection to make close() and wait_closed() work
    mock_writer = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    client._writer = mock_writer

    # Simulate an active command waiter
    client._ack_waiter = asyncio.get_running_loop().create_future()

    await client.close()

    assert client._ack_waiter.done()
    with pytest.raises(BeckerConnectionError, match="Disconnected"):
        await client._ack_waiter
