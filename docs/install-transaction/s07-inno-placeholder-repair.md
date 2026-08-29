# S07 Inno placeholder and uninstall metadata repair

Status: verified by bounded local bootstrap tests; exact packaged-chain proof is
deferred to S09 on a GitHub-hosted ephemeral Windows runner.

## Failure evidence

GitHub Actions run `33238152320`, Windows job `99062698922`, built the uniquely
hashed `2.20.0` Inno candidate and entered the exact packaged production chain.
The clean Setup process returned `1` before `request.json` existed. Its Inno log
showed that Setup had already created `unins000.exe` and `unins000.dat` under the
target Skill root before invoking `packaging/windows/install.ps1`.

The outer wrapper accepted only an absent target or a complete installed Skill
root. It therefore rejected Inno's legitimate clean-install placeholder before
the canonical transaction controller could prepare a request. If that check had
merely been removed, the generation switch would have moved Inno's uninstall
metadata into the previous-generation backup and omitted it from the committed
candidate projection.

## Minimal repair

Only `packaging/windows/install.ps1` changes in production:

- accept an existing incomplete target only when its path is exactly under
  `.codex/skills/memory-wuxian` and every direct entry is an Inno uninstall
  metadata file;
- recognize only `unins[0-9]+.(exe|dat|msg)` regular files;
- copy only those files into the extracted candidate before manifest creation,
  so the existing single transaction Owner carries them through the generation
  switch;
- preserve the existing explicit-root and service-SID fallback behavior.

No transaction resource, mutation order, task, Run value, shortcut, archive
pointer, runtime activation, broker, controller, or rollback implementation was
changed. The S06 ownership decision remains valid. No redundant production path
was deleted because the failure was a missing outer-boundary case, not duplicate
ownership.

## Bounded verification

`tests/test_windows_inno_bootstrap.py` executes the official PowerShell wrapper
against disposable temporary roots and intentionally stops at the missing
sessions prerequisite, before runtime activation or any product mutation. It
proves both:

1. a clean Inno-only placeholder is admitted and its exact uninstall bytes are
   copied into the candidate without changing the source files;
2. an existing valid installation copies only the allowlisted Inno files and
   does not copy an unrelated file.

The new tests plus the existing dashboard/explicit-root contracts passed 15/15.
PowerShell AST parsing, workflow pre-edit/post-edit hooks, and diff whitespace
checks also passed. The complete Python suite and exact Setup behavior remain
S08/S09 evidence and are not inferred from these bounded tests.
