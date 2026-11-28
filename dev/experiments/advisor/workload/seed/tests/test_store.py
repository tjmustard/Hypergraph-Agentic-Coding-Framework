"""The ledger's rules and its file."""

import pytest

from stockroom import Item, Ledger, Movement, StockError


@pytest.fixture
def ledger() -> Ledger:
    """A ledger with one item."""
    made = Ledger()
    made.add_item(Item("BOLT", "M4 bolt", quantity=10, reorder_level=4))
    return made


def test_apply_changes_quantity_and_history(ledger: Ledger) -> None:
    """A withdrawal lowers the quantity and is recorded."""
    assert ledger.apply(Movement("BOLT", -3)).quantity == 7
    assert ledger.history() == [Movement("BOLT", -3)]


@pytest.mark.parametrize("movement", [Movement("BOLT", -11), Movement("BOLT", 0)])
def test_apply_refuses_and_changes_nothing(ledger: Ledger, movement: Movement) -> None:
    """A refused movement leaves the ledger as it was."""
    with pytest.raises(StockError):
        ledger.apply(movement)
    assert ledger.get("BOLT").quantity == 10
    assert ledger.history() == []


def test_unknown_and_duplicate_items_are_refused(ledger: Ledger) -> None:
    """The SKU is the item's identity."""
    with pytest.raises(StockError):
        ledger.get("NUT")
    with pytest.raises(StockError):
        ledger.add_item(Item("BOLT", "again"))


def test_copy_is_independent(ledger: Ledger) -> None:
    """Changing a copy leaves the original alone."""
    other = ledger.copy()
    other.apply(Movement("BOLT", 5))
    assert ledger.get("BOLT").quantity == 10


def test_save_and_load(ledger: Ledger, tmp_path) -> None:
    """A saved ledger loads as it was."""
    ledger.apply(Movement("BOLT", -1, "job 12"))
    ledger.save(tmp_path / "stock.json")
    loaded = Ledger.load(tmp_path / "stock.json")
    assert loaded.items() == ledger.items()
    assert loaded.history() == ledger.history()


def test_load_refuses_a_foreign_file(tmp_path) -> None:
    """A file that is not a ledger is refused, not guessed at."""
    path = tmp_path / "stock.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(StockError):
        Ledger.load(path)
