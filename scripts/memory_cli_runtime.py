"""Shell runtime owner for the Memory Wuxian CLI compatibility facade."""

from __future__ import annotations

from types import ModuleType
from typing import Optional, Sequence


def run_cli(
    argv: Optional[Sequence[str]],
    *,
    cli_module: ModuleType,
) -> int:
    """Run through dependencies exposed by the public compatibility module."""
    parser = cli_module.build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        if args.command == "readonly-query":
            exc = cli_module.ReadRequestError(
                "malformed-request",
                "unknown read-only CLI parameter",
            )
            print(
                cli_module.json.dumps(
                    cli_module.error_payload(exc),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=cli_module.sys.stderr,
            )
            return 1
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")
    try:
        stateless_result = cli_module.dispatch_stateless_read_only_command(args)
        if stateless_result is not None:
            return stateless_result
        config = cli_module.resolve_config(cli_module.Path(args.config))
        store = cli_module.MemoryStore(
            cli_module.resolve_root(args.root, config),
            config,
        )
        lock_path = cli_module.command_lock_path(
            cli_module.command_spec(args.command),
            args,
            store.root,
        )
        if lock_path is None:
            return cli_module.dispatch_command(args, parser, store)
        with cli_module.exclusive_lock(lock_path):
            return cli_module.dispatch_command(args, parser, store)
    except cli_module.ReadRequestError as exc:
        print(
            cli_module.json.dumps(
                cli_module.error_payload(exc),
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=cli_module.sys.stderr,
        )
        return 1
    except (
        OSError,
        ValueError,
        RuntimeError,
        cli_module.json.JSONDecodeError,
    ) as exc:
        print(f"memory-wuxian: {exc}", file=cli_module.sys.stderr)
        return 1
