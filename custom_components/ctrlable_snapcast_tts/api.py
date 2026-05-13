"""Async HTTP client for the Ctrlable Snapcast Streamer add-on."""
from __future__ import annotations

import httpx


class CannotConnectError(Exception):
    pass


class InvalidAuthError(Exception):
    pass


class AddonApiClient:
    def __init__(self, addon_url: str, bearer_token: str) -> None:
        self._url = addon_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {bearer_token}"}

    async def _get(self, path: str) -> dict | list:
        try:
            async with httpx.AsyncClient(verify=False, timeout=10) as client:
                resp = await client.get(f"{self._url}{path}", headers=self._headers)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise CannotConnectError from exc
        if resp.status_code == 401:
            raise InvalidAuthError
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, body: dict) -> dict | list:
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                resp = await client.post(
                    f"{self._url}{path}", json=body, headers=self._headers
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise CannotConnectError from exc
        if resp.status_code == 401:
            raise InvalidAuthError
        resp.raise_for_status()
        return resp.json()

    async def get_health(self) -> dict:
        result = await self._get("/health")
        return result if isinstance(result, dict) else {}

    async def get_clients(self) -> list[dict]:
        result = await self._get("/snapcast/clients")
        return result if isinstance(result, list) else []

    async def announce(self, client_id: str, url: str, source_host: str) -> dict:
        result = await self._post(
            "/announce",
            {"client_id": client_id, "url": url, "source_host": source_host},
        )
        return result if isinstance(result, dict) else {}

    async def announce_multi(
        self, client_ids: list[str], url: str, source_host: str
    ) -> list[dict]:
        result = await self._post(
            "/announce/multi",
            {"client_ids": client_ids, "url": url, "source_host": source_host},
        )
        return result if isinstance(result, list) else []
