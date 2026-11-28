"""The ledger: its items, its movement history and its JSON file."""

import json
import os
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from stockroom.errors import StockError
from stockroom.model import Item, Movement

FORMAT = 1


class Ledger:
    """Items by SKU and the movements applied to them, in order."""

    def __init__(self) -> None:
        """Start empty."""
        self._items: dict[str, Item] = {}
        self._history: list[Movement] = []

    def add_item(self, item: Item) -> None:
        """Add a new item.

        Raises:
            StockError: For an empty or duplicate SKU, or a negative number.
        """
        if not item.sku or item.sku != item.sku.strip():
            raise StockError("a SKU is non-empty and has no surrounding space")
        if item.sku in self._items:
            raise StockError(f"{item.sku}: already in the ledger")
        if item.quantity < 0 or item.reorder_level < 0:
            raise StockError(f"{item.sku}: quantity and reorder level cannot be negative")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        """Return one item.

        Raises:
            StockError: When the SKU is unknown.
        """
        try:
            return self._items[sku]
        except KeyError:
            raise StockError(f"{sku}: no such item") from None

    def items(self) -> list[Item]:
        """Return every item, sorted by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def history(self) -> list[Movement]:
        """Return the applied movements, oldest first."""
        return list(self._history)

    def apply(self, movement: Movement) -> Item:
        """Apply one movement and return the changed item.

        Raises:
            StockError: For an unknown SKU, a zero change, or a quantity that would go negative.
        """
        item = self.get(movement.sku)
        if movement.delta == 0:
            raise StockError(f"{movement.sku}: a movement changes the quantity")
        quantity = item.quantity + movement.delta
        if quantity < 0:
            raise StockError(
                f"{movement.sku}: only {item.quantity} in stock, cannot take {-movement.delta}"
            )
        changed = replace(item, quantity=quantity)
        self._items[item.sku] = changed
        self._history.append(movement)
        return changed

    def copy(self) -> "Ledger":
        """Return an independent copy."""
        other = Ledger()
        other._items = dict(self._items)
        other._history = list(self._history)
        return other

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        """Read a ledger file; a missing file is an empty ledger.

        Raises:
            StockError: When the file is not a ledger.
        """
        ledger = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ledger
        except (OSError, ValueError) as error:
            raise StockError(f"cannot read {path}: {error}") from None
        try:
            if data["format"] != FORMAT:
                raise StockError(f"{path}: unknown format {data['format']}")
            for record in data["items"]:
                ledger.add_item(Item(**record))
            ledger._history = [Movement(**record) for record in data["history"]]
        except (KeyError, TypeError) as error:
            raise StockError(f"{path}: not a ledger file ({error})") from None
        return ledger

    def save(self, path: Path) -> None:
        """Write the ledger, replacing the file in one step."""
        path = Path(path)
        data = {
            "format": FORMAT,
            "items": [asdict(item) for item in self.items()],
            "history": [asdict(movement) for movement in self._history],
        }
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(data, out, indent=2)
                out.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
