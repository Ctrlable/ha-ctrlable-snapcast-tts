"""Snapcast JSON-RPC client and topology manager."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

RPC_TIMEOUT = 5.0
_rpc_id = 0


def _next_id() -> int:
    global _rpc_id
    _rpc_id += 1
    return _rpc_id


class SnapcastTimeoutError(Exception):
    pass


class SnapcastRPCError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Snapcast RPC error {code}: {message}")
        self.code = code


@dataclass
class SnapClient:
    id: str
    name: str
    connected: bool
    current_group_id: str
    host_ip: str
    volume_percent: int
    muted: bool


@dataclass
class SnapGroup:
    id: str
    name: str
    stream_id: str
    client_ids: list[str]
    muted: bool


@dataclass
class SnapStream:
    id: str
    uri: str
    status: str


class SnapcastClient:
    """Async JSON-RPC client for a single Snapcast server."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._event_callback: Any = None
        self._lock = asyncio.Lock()
        self._connected = False

    # ── Connection ────────────────────────────────────────────────

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=RPC_TIMEOUT,
        )
        self._connected = True
        asyncio.get_event_loop().create_task(self._read_loop())
        _LOGGER.info("Connected to Snapcast at %s:%d", self._host, self._port)

    async def disconnect(self) -> None:
        self._connected = False
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while self._connected:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        if "error" in msg:
                            err = msg["error"]
                            fut.set_exception(SnapcastRPCError(err.get("code", -1), err.get("message", "")))
                        else:
                            fut.set_result(msg.get("result"))
                elif "method" in msg and self._event_callback:
                    asyncio.get_event_loop().create_task(self._event_callback(msg))
        except Exception:
            _LOGGER.debug("Snapcast read loop ended", exc_info=True)
        finally:
            self._connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(SnapcastTimeoutError("Connection closed"))
            self._pending.clear()

    # ── RPC call ─────────────────────────────────────────────────

    async def _call(self, method: str, params: dict | None = None) -> Any:
        if not self._connected:
            raise SnapcastRPCError(-1, "Not connected")
        msg_id = _next_id()
        payload: dict = {"id": msg_id, "jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut
        assert self._writer is not None
        async with self._lock:
            self._writer.write(json.dumps(payload).encode() + b"\n")
            await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=RPC_TIMEOUT)
        except TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise SnapcastTimeoutError(f"RPC timeout: {method}") from exc

    # ── Topology queries ──────────────────────────────────────────

    async def get_status(self) -> dict:
        return await self._call("Server.GetStatus")

    async def list_clients(self) -> list[SnapClient]:
        status = await self.get_status()
        clients: list[SnapClient] = []
        for group in status["server"]["groups"]:
            for c in group["clients"]:
                clients.append(SnapClient(
                    id=c["id"],
                    name=c["config"].get("name") or c["host"].get("name", ""),
                    connected=c["connected"],
                    current_group_id=group["id"],
                    host_ip=c["host"].get("ip", ""),
                    volume_percent=c["config"]["volume"]["percent"],
                    muted=c["config"]["volume"]["muted"],
                ))
        return clients

    async def list_groups(self) -> list[SnapGroup]:
        status = await self.get_status()
        return [
            SnapGroup(
                id=g["id"],
                name=g.get("name", ""),
                stream_id=g.get("stream_id", ""),
                client_ids=[c["id"] for c in g["clients"]],
                muted=g.get("muted", False),
            )
            for g in status["server"]["groups"]
        ]

    async def list_streams(self) -> list[SnapStream]:
        status = await self.get_status()
        return [
            SnapStream(
                id=s["id"],
                uri=s["uri"]["raw"],
                status=s.get("status", ""),
            )
            for s in status["server"]["streams"]
        ]

    # ── Client/group mutations ────────────────────────────────────

    async def move_client_to_group(self, client_id: str, target_group_id: str) -> None:
        """Move client into target group (append; does not remove from other groups)."""
        groups = await self.list_groups()
        target = next((g for g in groups if g.id == target_group_id), None)
        if target is None:
            raise SnapcastRPCError(-1, f"Group {target_group_id!r} not found")
        new_clients = list(dict.fromkeys(target.client_ids + [client_id]))
        await self._call("Group.SetClients", {"id": target_group_id, "clients": new_clients})

    async def remove_client_from_group(self, client_id: str, group_id: str) -> None:
        groups = await self.list_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if group is None:
            return
        new_clients = [c for c in group.client_ids if c != client_id]
        await self._call("Group.SetClients", {"id": group_id, "clients": new_clients})

    async def add_stream(self, stream_uri: str) -> str:
        """Add a dynamic stream; returns the stream ID assigned by Snapcast."""
        result = await self._call("Stream.AddStream", {"streamUri": stream_uri})
        return result["id"]

    async def remove_stream(self, stream_id: str) -> None:
        await self._call("Stream.RemoveStream", {"id": stream_id})

    # ── Event subscription ────────────────────────────────────────

    def subscribe_events(self, callback: Any) -> None:
        """Register an async callback for server-pushed events."""
        self._event_callback = callback

    @property
    def is_connected(self) -> bool:
        return self._connected


# ── Module-level singleton ────────────────────────────────────────

_client: SnapcastClient | None = None


def get_client() -> SnapcastClient:
    if _client is None:
        raise RuntimeError("Snapcast client not initialised")
    return _client


async def init_client(host: str, port: int) -> SnapcastClient:
    global _client
    _client = SnapcastClient(host, port)
    await _client.connect()
    return _client
