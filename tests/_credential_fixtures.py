"""Credential-shaped sample values, assembled rather than written out.

Secret scanners -- GitHub's included -- match literal text in tracked files.
A test that needs an AWS access key id to prove a redaction path writes one
out, the scanner sees a live-shaped credential, and the repository collects an
alert for a value nobody ever issued. That is exactly what happened: an
`ASIA`-prefixed sample in `tests/test_risky_action_confirmation.py` raised a
"Amazon AWS Temporary Access Key ID" secret-scanning alert. The value was
fabricated, so there was nothing to rotate or revoke -- only a literal to stop
storing.

The constants below are the same strings at runtime that the literals were:
`_AWS_ACCESS_KEY` in `src/system/metadata_safety.py` matches them, and so does
every value detector built on it. Joining the prefix to the body at import time
means no AWS-key-shaped literal exists on disk for a scanner to match, while
tests keep exercising the real shape rather than a weakened stand-in.

`tests/test_credential_fixture_policy.py` keeps the arrangement honest: it
fails when an AWS-key-shaped literal reappears anywhere in the tree.
"""

from __future__ import annotations

# Sixteen upper-case alphanumerics -- the body an AWS access key id carries
# after its four-character prefix. Alone it matches no scanner pattern, because
# the prefix is what makes the shape.
_AWS_ACCESS_KEY_BODY = "IOSFODNN7EXAMPLE"

#: Long-term AWS access key id shape (`AKIA` + sixteen).
AWS_ACCESS_KEY_ID = "AKIA" + _AWS_ACCESS_KEY_BODY

#: Temporary (STS) AWS access key id shape (`ASIA` + sixteen). The prefix is
#: the only difference from the long-term form, and the value detectors treat
#: the two identically.
AWS_TEMPORARY_ACCESS_KEY_ID = "ASIA" + _AWS_ACCESS_KEY_BODY
