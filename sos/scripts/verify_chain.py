"""sos.scripts.verify_chain — CLI wrapper around sos.kernel.audit_chain.verify_chain.

Usage
-----
    python -m sos.scripts.verify_chain --stream kernel
    python -m sos.scripts.verify_chain --stream mirror --from-seq 1 --to-seq 500
    python -m sos.scripts.verify_chain --all            # verify every stream
    python -m sos.scripts.verify_chain --all --json     # machine-readable output

Exit codes
----------
    0  — all chains intact
    1  — at least one broken chain
    2  — usage error / environment problem
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

logger = logging.getLogger("sos.scripts.verify_chain")


async def _list_streams() -> list[str]:
    """Return all stream_ids from audit_stream_seqs."""
    import asyncpg

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch("SELECT stream_id FROM audit_stream_seqs ORDER BY stream_id")
        return [row["stream_id"] for row in rows]
    finally:
        await conn.close()


async def _verify(
    stream_id: str,
    from_seq: int,
    to_seq: int | None,
) -> dict:
    from sos.kernel.audit_chain import verify_chain  # local import keeps startup fast

    return await verify_chain(stream_id, from_seq=from_seq, to_seq=to_seq)


async def _run(args: argparse.Namespace) -> int:
    """Core async runner.  Returns process exit code."""
    if args.all:
        try:
            streams = await _list_streams()
        except Exception as exc:
            _emit({"ok": False, "error": f"cannot list streams: {exc}"}, args.json)
            return 2
        if not streams:
            _emit({"ok": True, "message": "no streams found"}, args.json)
            return 0
    else:
        streams = [args.stream]

    overall_ok = True
    results: list[dict] = []

    for stream_id in streams:
        result = await _verify(stream_id, args.from_seq, args.to_seq)
        result["stream_id"] = stream_id
        results.append(result)
        if not result["ok"]:
            overall_ok = False

    if args.json:
        print(json.dumps({"ok": overall_ok, "results": results}, indent=2))
    else:
        for r in results:
            stream = r["stream_id"]
            if r["ok"]:
                checked = r.get("checked", 0)
                print(f"  OK  {stream}  ({checked} events checked)")
            else:
                seq = r.get("broken_at_seq", "?")
                reason = r.get("reason", "unknown")
                print(f"  FAIL  {stream}  broken at seq={seq}: {reason}")

        if overall_ok:
            print("\nAll chains intact.")
        else:
            print("\nChain integrity failure — see FAIL lines above.")

    return 0 if overall_ok else 1


def _emit(obj: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2))
    else:
        print(obj)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify SOS audit hash-chain integrity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stream", metavar="STREAM_ID", help="Single stream to verify")
    group.add_argument("--all", action="store_true", help="Verify all streams")

    parser.add_argument(
        "--from-seq",
        type=int,
        default=1,
        dest="from_seq",
        metavar="N",
        help="Start from seq N (default: 1)",
    )
    parser.add_argument(
        "--to-seq",
        type=int,
        default=None,
        dest="to_seq",
        metavar="N",
        help="Stop at seq N (default: latest)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
