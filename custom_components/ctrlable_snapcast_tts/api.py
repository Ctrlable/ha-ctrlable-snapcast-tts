"""Async HTTP client for the Ctrlable Snapcast Streamer add-on."""
from __future__ import annotations

import httpx

# announce() now blocks for the length of the clip -- it returns when playback
# actually ends, which is the whole point. 30s would time out on any long reply.
ANNOUNCE_TIMEOUT = 180


class CannotConnectError(Exception):
    pass


class InvalidAuthError(Exception):
    pass


class SatelliteNotMappedError(Exception):
    pass


class NoMatchingMappingError(Exception):
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
            async with httpx.AsyncClient(verify=False, timeout=ANNOUNCE_TIMEOUT) as client:
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

    async def push_satellites(self, satellites: list[dict]) -> dict:
        """Tell the streamer which satellites Home Assistant knows about.

        Exists so the streamer can label and pre-populate its mapping page
        without holding any Home Assistant credential. It used to read the
        entity registry itself, but that only worked while it ran as an add-on
        with a Supervisor-injected token; moved out of HA, that access is gone.
        Pushing over the token the integration ALREADY holds keeps the trust
        one-directional -- HA can call the streamer, the streamer holds nothing
        of HA's.
        """
        return await self._post("/satellites", {"satellites": satellites})  # type: ignore[return-value]

    async def get_clients(self) -> list[dict]:
        result = await self._get("/snapcast/clients")
        return result if isinstance(result, list) else []

    async def announce(
        self, client_id: str, url: str, source_host: str, volume: int | None = None
    ) -> dict:
        result = await self._post(
            "/announce",
            {"client_id": client_id, "url": url, "source_host": source_host, "volume": volume},
        )
        return result if isinstance(result, dict) else {}

    async def announce_multi(
        self, client_ids: list[str], url: str, source_host: str, volume: int | None = None
    ) -> list[dict]:
        result = await self._post(
            "/announce/multi",
            {"client_ids": client_ids, "url": url, "source_host": source_host,
             "volume": volume},
        )
        return result if isinstance(result, list) else []

    async def announce_by_satellite(
        self, satellite_id: str, wake_word: str | None, url: str, source_host: str,
        volume: int | None = None,
    ) -> list[dict]:
        try:
            async with httpx.AsyncClient(verify=False, timeout=ANNOUNCE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._url}/announce/by_satellite",
                    json={"satellite_id": satellite_id, "wake_word": wake_word, "url": url,
                          "source_host": source_host, "volume": volume},
                    headers=self._headers,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise CannotConnectError from exc
        if resp.status_code == 401:
            raise InvalidAuthError
        if resp.status_code == 404:
            raise SatelliteNotMappedError(satellite_id)
        if resp.status_code == 422:
            raise NoMatchingMappingError(satellite_id, wake_word)
        resp.raise_for_status()
        result = resp.json()
        return result if isinstance(result, list) else []

    async def announce_chime(
        self, satellite_id: str, wake_word: str | None, chime: str,
        volume: int | None = None,
    ) -> list[dict]:
        """Play a chime bundled in the add-on on this satellite's zone."""
        try:
            async with httpx.AsyncClient(verify=False, timeout=ANNOUNCE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._url}/announce/chime",
                    json={"satellite_id": satellite_id, "wake_word": wake_word,
                          "chime": chime, "volume": volume},
                    headers=self._headers,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise CannotConnectError from exc
        if resp.status_code == 401:
            raise InvalidAuthError
        if resp.status_code == 404:
            raise SatelliteNotMappedError(satellite_id)
        if resp.status_code == 422:
            raise NoMatchingMappingError(satellite_id, wake_word)
        resp.raise_for_status()
        result = resp.json()
        return result if isinstance(result, list) else []

    async def hold(self, satellite_id: str, wake_word: str | None) -> list[str]:
        """Duck this satellite's zone while it listens, without playing audio."""
        result = await self._post(
            "/hold/by_satellite",
            {"satellite_id": satellite_id, "wake_word": wake_word},
        )
        return (result or {}).get("held", []) if isinstance(result, dict) else []

    async def release(self, satellite_id: str, wake_word: str | None) -> list[str]:
        """Tell the add-on this satellite's exchange ended with no answer."""
        result = await self._post(
            "/release/by_satellite",
            {"satellite_id": satellite_id, "wake_word": wake_word},
        )
        return (result or {}).get("released", []) if isinstance(result, dict) else []

    async def get_mappings(self) -> list[dict]:
        result = await self._get("/mappings")
        return result if isinstance(result, list) else []

    async def upsert_mapping(
        self, satellite_id: str, wake_word: str, target_ids: list[str], notes: str = ""
    ) -> dict:
        result = await self._post(
            "/mappings",
            {"satellite_id": satellite_id, "wake_word": wake_word, "target_snapclient_ids": target_ids, "notes": notes},
        )
        return result if isinstance(result, dict) else {}

    async def delete_mapping(self, satellite_id: str, wake_word: str) -> dict:
        try:
            async with httpx.AsyncClient(verify=False, timeout=10) as client:
                resp = await client.request(
                    "DELETE",
                    f"{self._url}/mappings",
                    json={"satellite_id": satellite_id, "wake_word": wake_word},
                    headers=self._headers,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise CannotConnectError from exc
        if resp.status_code == 401:
            raise InvalidAuthError
        resp.raise_for_status()
        result = resp.json()
        return result if isinstance(result, dict) else {}
