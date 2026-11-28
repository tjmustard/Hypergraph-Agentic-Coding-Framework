"""The command line."""

from stockroom.cli import main


def test_add_move_show(tmp_path, capsys) -> None:
    """The three verbs work on one file."""
    file = ["--file", str(tmp_path / "stock.json")]
    assert main([*file, "add", "BOLT", "M4 bolt", "--quantity", "5"]) == 0
    assert main([*file, "move", "BOLT", "-2"]) == 0
    assert main([*file, "show"]) == 0
    out = capsys.readouterr().out
    assert "BOLT: 3" in out
    assert "M4 bolt" in out


def test_refusal_exits_1(tmp_path, capsys) -> None:
    """A refused command says why on stderr."""
    assert main(["--file", str(tmp_path / "stock.json"), "move", "NUT", "1"]) == 1
    assert "no such item" in capsys.readouterr().err
