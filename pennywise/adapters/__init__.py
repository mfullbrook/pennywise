"""One adapter per card issuer export format. An account names its adapter."""

from types import ModuleType

from pennywise.adapters import amex, halifax, tesco
from pennywise.adapters.base import AdapterError

ADAPTERS: dict[str, ModuleType] = {amex.NAME: amex, halifax.NAME: halifax, tesco.NAME: tesco}


def get(name: str) -> ModuleType:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise AdapterError(
            f"no adapter {name!r}; available: {', '.join(sorted(ADAPTERS))}"
        ) from None
