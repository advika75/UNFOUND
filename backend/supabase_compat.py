from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode
from typing import Any

Client = Any


@dataclass
class SupabaseResponse:
    data: Any


class SupabaseRestRequest:
    def __init__(
        self,
        client: "SupabaseRestClient",
        method: str,
        path: str,
        payload: dict[str, Any] | list[dict[str, Any]],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.client = client
        self.method = method
        self.path = path
        self.payload = payload
        self.headers = headers or {}

    def execute(self) -> SupabaseResponse:
        import httpx

        headers = {**self.client.headers, **self.headers}
        response = httpx.request(
            self.method,
            f"{self.client.url}{self.path}",
            headers=headers,
            json=self.payload,
            timeout=120,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"Supabase REST {self.method} {self.path} failed with "
                f"{response.status_code}: {response.text}"
            ) from error
        if not response.content:
            return SupabaseResponse(data=None)
        return SupabaseResponse(data=response.json())


class SupabaseRestTable:
    def __init__(self, client: "SupabaseRestClient", table_name: str) -> None:
        self.client = client
        self.table_name = table_name

    def insert(self, payload: dict[str, Any] | list[dict[str, Any]]) -> SupabaseRestRequest:
        return SupabaseRestRequest(
            client=self.client,
            method="POST",
            path=f"/rest/v1/{self.table_name}",
            payload=payload,
        )

    def update(self, payload: dict[str, Any]) -> "SupabaseRestUpdateRequest":
        return SupabaseRestUpdateRequest(self.client, self.table_name, payload)

    def delete(self) -> "SupabaseRestDeleteRequest":
        return SupabaseRestDeleteRequest(self.client, self.table_name)

    def select(self, columns: str = "*") -> "SupabaseRestSelectRequest":
        return SupabaseRestSelectRequest(self.client, self.table_name, columns)

    def upsert(
        self,
        payload: dict[str, Any] | list[dict[str, Any]],
        on_conflict: str | None = None,
    ) -> SupabaseRestRequest:
        path = f"/rest/v1/{self.table_name}"
        if on_conflict:
            path = f"{path}?on_conflict={on_conflict}"
        return SupabaseRestRequest(
            client=self.client,
            method="POST",
            path=path,
            payload=payload,
            headers={"prefer": "resolution=merge-duplicates,return=representation"},
        )


class SupabaseRestSelectRequest:
    def __init__(self, client: "SupabaseRestClient", table_name: str, columns: str = "*") -> None:
        self.client = client
        self.table_name = table_name
        self.columns = columns
        self.row_limit: int | None = None
        self.range_start: int | None = None
        self.range_end: int | None = None
        self.filters: dict[str, str] = {}

    def limit(self, count: int) -> "SupabaseRestSelectRequest":
        self.row_limit = count
        return self

    def eq(self, column: str, value: Any) -> "SupabaseRestSelectRequest":
        self.filters[column] = f"eq.{value}"
        return self

    def in_(self, column: str, values: list[Any]) -> "SupabaseRestSelectRequest":
        encoded_values = ",".join(str(value) for value in values)
        self.filters[column] = f"in.({encoded_values})"
        return self

    def range(self, start: int, end: int) -> "SupabaseRestSelectRequest":
        self.range_start = start
        self.range_end = end
        return self

    def execute(self) -> SupabaseResponse:
        import httpx

        query: dict[str, str | int] = {"select": self.columns, **self.filters}
        if self.row_limit is not None:
            query["limit"] = self.row_limit
        headers = dict(self.client.headers)
        if self.range_start is not None and self.range_end is not None:
            headers["range"] = f"{self.range_start}-{self.range_end}"
            headers["range-unit"] = "items"
        response = httpx.request(
            "GET",
            f"{self.client.url}/rest/v1/{self.table_name}?{urlencode(query)}",
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        return SupabaseResponse(data=response.json() if response.content else None)


class SupabaseRestUpdateRequest:
    def __init__(self, client: "SupabaseRestClient", table_name: str, payload: dict[str, Any]) -> None:
        self.client, self.table_name, self.payload = client, table_name, payload
        self.filters: dict[str, str] = {}

    def eq(self, column: str, value: Any) -> "SupabaseRestUpdateRequest":
        self.filters[column] = f"eq.{value}"
        return self

    def execute(self) -> SupabaseResponse:
        import httpx
        response = httpx.patch(f"{self.client.url}/rest/v1/{self.table_name}?{urlencode(self.filters)}", headers=self.client.headers, json=self.payload, timeout=120)
        response.raise_for_status()
        return SupabaseResponse(data=response.json() if response.content else None)


class SupabaseRestDeleteRequest:
    def __init__(self, client: "SupabaseRestClient", table_name: str) -> None:
        self.client, self.table_name = client, table_name
        self.filters: dict[str, str] = {}

    def eq(self, column: str, value: Any) -> "SupabaseRestDeleteRequest":
        self.filters[column] = f"eq.{value}"
        return self

    def execute(self) -> SupabaseResponse:
        import httpx
        response = httpx.delete(
            f"{self.client.url}/rest/v1/{self.table_name}?{urlencode(self.filters)}",
            headers=self.client.headers,
            timeout=120,
        )
        response.raise_for_status()
        return SupabaseResponse(data=response.json() if response.content else None)


class SupabaseRestClient:
    """Minimal Supabase REST compatibility layer for newer publishable keys."""

    def __init__(self, url: str, key: str) -> None:
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": key,
            "authorization": f"Bearer {key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def rpc(self, function_name: str, payload: dict[str, Any]) -> SupabaseRestRequest:
        return SupabaseRestRequest(
            client=self,
            method="POST",
            path=f"/rest/v1/rpc/{function_name}",
            payload=payload,
        )

    def table(self, table_name: str) -> SupabaseRestTable:
        return SupabaseRestTable(self, table_name)


def create_supabase_client(url: str, key: str) -> Client | SupabaseRestClient:
    try:
        from supabase import create_client
    except ImportError:
        return SupabaseRestClient(url, key)
    try:
        return create_client(url, key)
    except Exception:
        if key.startswith("sb_publishable_"):
            return SupabaseRestClient(url, key)
        raise
