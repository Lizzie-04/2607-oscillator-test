import socket
import threading
import unittest

from system_console_transport import (
    ADDR_LAST_ACCEPTED_ID,
    ADDR_LAST_COMPLETED_ID,
    ADDR_LAST_RESULT,
    ADDR_REQUEST_ID,
    ADDR_STATISTICS_CAPTURE_ID,
    ADDR_STATISTICS_RAM,
    ADDR_STATISTICS_READY,
    SystemConsoleAvalonTransport,
)


class ChunkedSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    def recv(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class TimeoutSocket:
    def __init__(self) -> None:
        self.timeout = 30.0

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout) -> None:
        self.timeout = timeout


class SystemConsoleTransportTest(unittest.TestCase):
    def test_line_reader_handles_tcp_fragmentation_and_coalescing(self) -> None:
        transport = object.__new__(SystemConsoleAvalonTransport)
        transport._socket = ChunkedSocket(
            [b"RES 7 O", b"K 2 0x1", b" 0x2\nRES 8 OK 0\n"]
        )
        transport._receive_buffer = bytearray()
        self.assertEqual(transport._read_line(), "RES 7 OK 2 0x1 0x2")
        self.assertEqual(transport._read_line(), "RES 8 OK 0")

    def test_receive_response_rejects_wrong_tcp_id(self) -> None:
        transport = object.__new__(SystemConsoleAvalonTransport)
        transport._socket = ChunkedSocket([b"RES 9 OK 0\n"])
        transport._receive_buffer = bytearray()
        with self.assertRaisesRegex(RuntimeError, "事务ID不匹配"):
            transport._receive_response(8)

    def test_action_writes_request_id_and_checks_completion(self) -> None:
        transport = object.__new__(SystemConsoleAvalonTransport)
        transport._fpga_request_id = 41
        transport._log_callback = None
        writes: list[tuple[int, list[int]]] = []
        transport._write32 = (
            lambda address, values: writes.append((address, list(values)))
        )

        def read32(address, count=1, **_kwargs):
            if address == ADDR_LAST_ACCEPTED_ID:
                return [41, 41]
            if address == ADDR_LAST_RESULT:
                return [0]
            return [0] * count

        transport._read32 = read32
        action_id = transport._write_action(0x0058, 1)
        self.assertEqual(action_id, 41)
        self.assertEqual(
            writes,
            [(ADDR_REQUEST_ID, [41]), (0x0058, [1])],
        )
        self.assertEqual(transport._fpga_request_id, 42)

    def test_statistics_download_is_one_200_word_block_and_releases(self) -> None:
        transport = object.__new__(SystemConsoleAvalonTransport)
        transport._transaction_lock = threading.RLock()
        transport._log_callback = None
        actions: list[tuple[int, int]] = []
        reads: list[tuple[int, int]] = []
        read_timeouts: list[tuple[int, float | None]] = []
        start_id = 17

        def action(address, data=0, **_kwargs):
            actions.append((address, data))
            if address == 0x0064:
                return start_id
            return len(actions) + 100

        def read32(address, count=1, **_kwargs):
            reads.append((address, count))
            read_timeouts.append((address, _kwargs.get("timeout")))
            if address == ADDR_STATISTICS_READY and count == 2:
                if len([item for item in reads if item == (address, count)]) == 1:
                    return [0, 0]
                return [1, start_id]
            if address == ADDR_STATISTICS_READY and count == 3:
                return [1, start_id, 100]
            if address == ADDR_STATISTICS_RAM and count == 200:
                return [value for index in range(100) for value in (index, index + 1000)]
            return [0] * count

        transport._write_action = action
        transport._read32 = read32
        samples = transport.capture_statistics_batch(capture_timeout=0.1)
        self.assertEqual(len(samples), 100)
        self.assertEqual((samples[0].mu1_raw, samples[0].mu2_raw), (0, 1000))
        self.assertEqual((samples[99].mu1_raw, samples[99].mu2_raw), (99, 1099))
        self.assertEqual(reads.count((ADDR_STATISTICS_RAM, 200)), 1)
        self.assertIn((ADDR_STATISTICS_RAM, 60.0), read_timeouts)
        self.assertIn((0x0070, start_id), actions)

    def test_rpc_rejects_transfer_past_4k_avalon_span(self) -> None:
        transport = object.__new__(SystemConsoleAvalonTransport)
        transport._tcp_id = 1
        with self.assertRaisesRegex(ValueError, "4 KB"):
            transport._rpc_once("READ32", 0x0FFC, 2)

    def test_rpc_restarts_twice_then_replays_same_request(self) -> None:
        transport = object.__new__(SystemConsoleAvalonTransport)
        transport._socket = TimeoutSocket()
        starts = []
        restarts = []
        calls = []
        transport.start = lambda: starts.append(True) or transport
        transport._restart_unlocked = lambda delay: restarts.append(delay)

        def rpc_once(operation, address, values_or_count):
            calls.append((operation, address, values_or_count))
            if len(calls) < 3:
                raise ConnectionError("link dropped")
            return [0x1234]

        transport._rpc_once = rpc_once
        result = transport._rpc("READ32", 0x0010, 1)
        self.assertEqual(result, [0x1234])
        self.assertEqual(restarts, [1.0, 3.0])
        self.assertEqual(calls, [("READ32", 0x0010, 1)] * 3)

    def test_request_id_wrap_skips_zero(self) -> None:
        transport = object.__new__(SystemConsoleAvalonTransport)
        transport._fpga_request_id = 0xFFFFFFFF
        self.assertEqual(transport._allocate_request_id(), 0xFFFFFFFF)
        self.assertEqual(transport._fpga_request_id, 1)

    def test_loopback_socket_round_trip_uses_lines(self) -> None:
        left, right = socket.socketpair()
        try:
            transport = object.__new__(SystemConsoleAvalonTransport)
            transport._socket = left
            transport._receive_buffer = bytearray()
            transport._send_line("HELLO token 1")
            self.assertEqual(right.recv(64), b"HELLO token 1\n")
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
