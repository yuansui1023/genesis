from __future__ import annotations

from typing import Any
from collections.abc import Callable

from genesis.core.transport.base_transport import BaseTransport
from genesis.core.transport.dummy_test_transport import DummyTestTransport
from genesis.core.transport.tcp_jsonl_transport import TcpJsonLinesTransport
from genesis.core.transport.visa_transport import VisaTransport

TransportBuilder = Callable[[str, dict[str, Any] | None], BaseTransport]

_TRANSPORT_BUILDERS: dict[str, TransportBuilder] = {
    "visa": lambda resourceName, settings: VisaTransport(
        resourceName=resourceName,
        settings=settings,
    ),
    "dummy_test": lambda resourceName, settings: DummyTestTransport(
        resourceName=resourceName,
        settings=settings,
    ),
    "tcp_jsonl": lambda resourceName, settings: TcpJsonLinesTransport(
        resourceName=resourceName,
        settings=settings,
    ),
}


def createTransport(
    transportKey: str, resourceName: str, settings: dict[str, Any] | None = None
) -> BaseTransport:
    key = str(transportKey).strip()
    builder = _TRANSPORT_BUILDERS.get(key)
    if builder is None:
        raise ValueError(f"Unknown transport key: {transportKey!r}")
    return builder(resourceName, settings)


def getAvailableTransportKeys() -> list[str]:
    return list(_TRANSPORT_BUILDERS.keys())


def registerTransport(transportKey: str, builder: TransportBuilder) -> None:
    key = str(transportKey).strip()
    if not key:
        raise ValueError("transportKey must be a non-empty string")
    _TRANSPORT_BUILDERS[key] = builder
