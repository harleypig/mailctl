"""The CLI's own presentation of failures, apart from any one command."""

from mxfilter import MxFilterError, cli


# ----------------------------------------------------------------------------
def test_a_multi_line_error_is_indented_under_the_prefix(monkeypatch, capsys):
    """Continuation lines sit two spaces in, so the error reads as one block.

    The core separates sentences with plain newlines and leaves the layout
    to the front-end; this is the CLI's half of that split.
    """

    def fail(args):
        raise MxFilterError("first line;\nsecond line.\nthird line")

    monkeypatch.setattr(cli, "cmd_test", fail)

    assert cli.main(["test"]) == 1

    assert capsys.readouterr().err == (
        "mxfilter: first line;\n  second line.\n  third line\n"
    )


# ----------------------------------------------------------------------------
def test_a_single_line_error_is_printed_unchanged(monkeypatch, capsys):
    def fail(args):
        raise MxFilterError("one line only")

    monkeypatch.setattr(cli, "cmd_test", fail)

    assert cli.main(["test"]) == 1

    assert capsys.readouterr().err == "mxfilter: one line only\n"
