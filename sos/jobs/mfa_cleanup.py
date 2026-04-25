"""
G62: MFA used-codes cleanup job.

Deletes mfa_used_codes rows older than 5 minutes. Wired via systemd timer
at *:0/5 (every 5 minutes). The 5-minute retention window exceeds the
maximum TOTP replay window (90 sec = ±1 step at 30 sec/step), so no live
replay-prevention entries are removed.

Run directly:
    python3 -m sos.jobs.mfa_cleanup
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def main() -> None:
    from sos.contracts.sso import cleanup_mfa_used_codes

    deleted = cleanup_mfa_used_codes()
    log.info('mfa_cleanup: %d expired rows deleted', deleted)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        stream=sys.stdout,
    )
    main()
