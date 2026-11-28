"""The ledger's records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    """One stock item. A reorder level of 0 means the item is not reordered."""

    sku: str
    name: str
    quantity: int = 0
    reorder_level: int = 0


@dataclass(frozen=True)
class Movement:
    """A change of one item's quantity: positive for receipts, negative for withdrawals."""

    sku: str
    delta: int
    note: str = ""
