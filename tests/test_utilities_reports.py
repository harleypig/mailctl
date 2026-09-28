"""The utilities for probing the account, driven as any front-end would.

No argparse and no stdout here: every test builds plain inputs, calls
the utility, and asserts on what comes back and on what the fakes were
asked to do.

The Sieve side is a session-level fake (``FakeSieveSession``); the IMAP
side is the real ``ImapSession`` over the ``FakeIMAPClient`` double from
conftest, so folder normalization and planning run for real.
"""

from mailctl import utilities

# ############################################################################
# Reading the account
# ############################################################################


# ----------------------------------------------------------------------------
def test_the_mail_probe_reads_capabilities_off_the_server(sessions, fake_imap):
    """What each capability means is the provider's to say, as facts."""
    fake_imap.caps = {"UIDPLUS", "FILTER=SIEVE"}

    probe = utilities.reports.probe_mail(sessions)
    facts = {fact.label: fact.text for fact in probe.facts}

    assert list(facts) == ["MOVE", "UIDPLUS", "FILTER=SIEVE"]
    assert facts["FILTER=SIEVE"].startswith("yes -- ")
    assert facts["UIDPLUS"] == "yes"
    assert facts["MOVE"] == "no (COPY+EXPUNGE)"
    assert probe.delimiter == "."
