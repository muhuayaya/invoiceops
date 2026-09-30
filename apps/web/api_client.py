"""HTTP boundary for the Streamlit workbench."""

from __future__ import annotations

import os
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class ApiClient:
    def __init__(self, base_url: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or os.getenv("INVOICEOPS_API_URL", "http://api:8000")).rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=30, transport=transport)

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self._client.request(method, path, headers=headers, json=json, files=files)
        except httpx.RequestError as exc:
            raise ApiError(0, "无法连接分类服务，请稍后重试。") from exc
        if response.is_error:
            try:
                error = response.json()
                detail = error.get("detail", response.text) if isinstance(error, dict) else error
            except ValueError:
                detail = response.text
            if isinstance(detail, list):
                detail = "；".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
            raise ApiError(response.status_code, str(detail or f"HTTP {response.status_code}"))
        if "application/json" in response.headers.get("content-type", ""):
            return response.json()
        return response.content


LABELS = (
    "DUPLICATE_INVOICE",
    "MISSING_PO_OR_RECEIPT",
    "OTHER_REVIEW",
    "PAYMENT_STATUS",
    "PRICE_VARIANCE",
    "QUANTITY_RECEIPT_VARIANCE",
    "SUPPLIER_MASTER_CHANGE",
    "TAX_CURRENCY_AMOUNT",
)
