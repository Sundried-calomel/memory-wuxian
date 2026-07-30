---
name: memory-wuxian
description: Persist, summarize, index, retrieve, restore, and verify conversation history beyond the active context window. Use when Codex needs to preserve exact dialogue records, generate count-triggered hierarchical summaries, recover an earlier discussion by time or concept, verify recollection against raw text, run memory maintenance, or diagnose the file-based Memory無限 archive.
---

# Memory無限

Build effectively unbounded, retrievable conversation memory from immutable source records and persistent indexes.

## Core invariants

1. Persist the exact source message before allowing runtime compression.
2. Treat raw records as append-only historical authority.
3. Use summaries only as routing indexes; verify historical claims against raw text.
4. Build deterministic Level-1 indexes after the configured completed-round or visible-character threshold, whichever occurs first.
5. Generate each higher level after a configured number of ungrouped child summaries.
6. Persist every summary level and every parent-child relationship.
7. Record only explicit source information. Do not infer hidden preferences, motives, or importance.
8. Keep runtime compression separate from permanent memory.
9. Verify SHA-256 source integrity before summary ingestion.
10. Rebuild only derived state and indexes; never repair integrity failures by rewriting history.
11. When Codex integration is enabled, import user-visible dialogue, lightweight tool activity visible in the task timeline, and successful structured file-change events. Preserve file paths, change types, line ranges, addition/deletion counts, and exact unified diffs. Exclude general tool output and hidden reasoning.
12. Complete the primary archive write before creating its external backup snapshot.
13. Maintain one complete transcript per conversation; never place records from different conversation IDs in the same transcript.
14. Use the native event-driven collector for high-frequency Codex capture on macOS and Windows; keep Python outside the continuous capture loop.
15. Preserve transaction consistency by holding `memory/.locks/archive.lock` for each native event batch and Python maintenance command.
16. Keep summary source ranges, parent-child groups, and derived indexes scoped to one conversation ID.
17. Exclude native Codex subagent sessions; archive only top-level user-visible conversations.
18. Keep only the configured number of newest complete external snapshots; the default is one.
19. Keep only the configured number of newest workspace recovery backups under `memory/archive/`; the default is one.
20. Do not keep an AI conversation active. Let scripts detect completed-round or character thresholds, then run one ephemeral AI process only to generate the due semantic summary.
21. Treat dashboard snapshots as disposable derived caches. Render the last persisted snapshot immediately, validate and rebuild it in a background thread for automatic refresh, and reserve synchronous validation for explicit manual refresh.
22. Keep each node's local archive exclusively writable by that node. Store imported peer history only in read-only replicas under the federation cache.
23. Qualify federated message, conversation, and summary identities by origin node. Never merge remote records into local counters or authoritative raw files.
24. Export only locally originated artifacts. Verify artifact SHA-256, event-sequence continuity, and predecessor bundle SHA-256 before committing an import.
25. Treat `.mwxb` as a compressed integrity-checked container, not as an encrypted or signed message. Transfer it only through SSH or another trusted channel.
26. Keep federation identity separate from OpenAI sessions and exclude reconstructible peer replicas from primary-archive desktop backups.
27. Keep SSH and encrypted cloud-folder exchange as parallel transports over the same `.mwxb` import contract.
28. Sign every cloud-bound delta with the origin device identity and encrypt it to the target device before it enters a synchronized folder.
29. Keep cloud private keys on their owning devices and never store cloud-account credentials in Memory無限.
30. Run cloud synchronization as a low-frequency, short-lived, model-free task. Do not place cloud polling in the native collector.
31. Treat ChatGPT export import as an explicit local operation. Never upload the selected export, and label the adapter experimental until a real official user export has been validated.
32. Keep `README.md`, `README.zh-CN.md`, and `README.ja.md` semantically synchronized whenever documented features, installation, commands, limitations, privacy boundaries, or release behavior change.
33. Record explicit operational-rule changes as append-only Level-1 policy events. Require exact prior-statement linkage before a revision, withdrawal, or reaffirmation changes current validity.
34. Use current-policy retrieval for rules or strategies that may have changed. Recency alone must never supersede an earlier policy.
35. Treat the desktop dashboard as a required release artifact. Every installer
    or update that changes dashboard code, runtime paths, or installation
    behavior must replace the platform launcher, preserve the active archive,
    bind the launcher version to the package version, and pass a live
    post-install self-check before the release is considered complete.
36. Keep `local-hash-v1` as the no-download semantic default. The optional
    `multilingual-e5-small` provider must use the pinned installer, immutable
    model revision, exact artifact SHA-256 verification, an isolated runtime,
    offline inference, and disabled remote model code. Semantic vectors remain
    disposable derived data and every returned hit must be verified against raw
    history.
37. Rebuild the Windows native-dashboard shortcut after every installer upgrade, using the preserved active archive root and the Python runtime validated by the current bootstrap.
38. Persist top-level Codex `token_count` telemetry in a separate derived
    per-conversation ledger. Call it Codex-reported model usage, not billing
    usage. Treat cached input and reasoning output as included subfields, detect
    cumulative-counter resets, exclude subagents, and never place telemetry in
    raw dialogue or semantic summaries.
39. Keep Environment Registry authority, locks, cursors, staging, and receipts
    independent from the 1.x conversation archive. Environment operations must
    never rewrite raw dialogue, summaries, or local archive ownership.
40. Represent global Rules, project Rules, global Skills, and project Skills as
    immutable, content-addressed revisions with explicit node-local bindings.
    Synchronize managed rule blocks only; preserve all bytes outside those
    blocks.
41. Exchange environment revisions through a separate signed and
    target-encrypted `environment-v1` stream. Each device keeps its own writable
    local state and receives remote conversation history only as read-only
    replicas.
42. Let the five-minute cloud task validate and stage incoming Environment
    updates without AI. No-change must create no decision, receipt, backup, or
    model call.
43. Never auto-install a Skill or project-scoped artifact. Divergence, identity
    changes, permission expansion, persistent-component expansion, and
    incompatible runtimes require explicit review and fail closed.
44. Install only a registered immutable revision through a verified binding.
    Validate package contents, platform and runtime contracts, preserve a
    rollback object before mutation, atomically switch, self-check, and append a
    receipt.
45. Promote reusable project capability to global scope only through a separate
    evidence-bearing proposal, complete platform matrix, and explicit approval.
46. Transport governance-insight proposals only as immutable, source-bound
    evidence. Keep imported proposals in read-only peer replicas; transfer,
    repetition, or arrival from another device never constitutes semantic
    review, acceptance, Rule registration, or Skill installation.
47. Keep governance-AI orchestration disabled until explicitly enabled.
    Five-minute checks are model-free; invoke at most one ephemeral Codex
    worker only when a bounded compatible batch is due.
48. Keep product work on its source device and run cross-device governance
    classification only on the explicitly configured coordinator.
49. Treat every AI result as a schema-validated, reviewable draft. It must
    never accept a Rule, install a Skill, remediate a product, rewrite history,
    or mark itself human-reviewed.
50. Validate evidence hashes before invocation. Retry a failed item at most
    twice, then isolate it without blocking unrelated queue work.
51. Assign every exported Environment Registry item a stable identity derived
    from its transaction, operation, and immutable object identity. Batch
    registration must export every artifact and project exactly once.
52. Parse Skill metadata with a safe full YAML loader. Permit legal nested
    mappings, lists, and block scalars; reject unsafe tags and duplicate keys.
    Project registrations received from peers remain read-only replicas and
    never create or activate a local project automatically.
53. The native dashboard must bind an operating-system-assigned loopback port
    and open the server's actual port. Never share or assume another local
    application's fixed port.
54. Treat `PRODUCT_ARCHITECTURE.md` as the canonical module-boundary owner and
    `docs/module-architecture.json` as its machine-readable source-ownership
    registry. Register each new production file under exactly one owner and
    pass `scripts/check_architecture_contract.py` for every product change.
    Unowned files, overlapping owners, and prohibited dependencies fail closed.
55. Treat a visible cloud-provider placeholder that is not locally readable as
    transient. Trigger bounded hydration before decrypting it and never
    quarantine a Files On-Demand availability error as cryptographic damage.
56. Recover an overlapping `environment-v1` range only after every persisted
    prefix event matches exactly. Prefer the widest newest valid candidate for
    one expected start, preserve exact replay for lost acknowledgements, and
    keep archive and Environment status histories independent.
57. Preserve stable executable entry paths in macOS background definitions.
    Never resolve a Homebrew or managed-runtime symlink into a version-specific
    installation directory, because a runtime upgrade would create a new macOS
    privacy identity and can trigger repeated Desktop or Documents prompts.
58. Apply an existing macOS installation only through the user-space candidate
    transaction. Probe exact-message capture in an isolated archive before
    cutover; after cutover verify a new live collector and the current
    dashboard; on failure restore the previous Skill, plist, and collector.
59. Publish collector telemetry on every monitoring interval, including idle
    intervals. Keep source and archive watermarks separate, expose stale
    telemetry and archive lag, and never infer archive freshness only from a
    running PID.
60. Before generating a report for a historical cutoff, run the deterministic
    archive-waterline preflight. If retained source bytes through the cutoff
    are not covered by persisted cursors, stop or perform the explicit bounded
    backfill and verify again before using Memory無限 evidence.
61. Synchronize semantic capability through an immutable
    `global-runtime-contract`, not by copying a platform virtual environment.
    Pin the model revision, artifact hashes, runtime packages, embedding
    interface, and platform-neutral installer entry. Receiving or accepting a
    contract never installs or downloads it; local realization requires an
    explicit reviewed `--apply`, and semantic indexes remain device-local.
62. On an existing macOS installation, routine stable-release updates must
    verify the PKG checksum, extract only its Skill payload in a temporary
    directory, and invoke `install_macos_transaction.py`. Do not open the
    platform installer or request administrator credentials unless this is a
    first install, recovery, or declared privileged-component migration.

## Operating workflow

1. On Windows, run `powershell -ExecutionPolicy Bypass -File scripts/bootstrap_windows.ps1` before the first archive operation. Pass `-AgentsPath <workspace AGENTS.md>` to install or deterministically update the canonical workspace rules. If it reports `missing-runtime`, rerun with `-InstallMissing` after user approval. Reuse Codex-bundled Python and CLI when available; do not install Rust or MSVC unless rebuilding the collector.
2. Run `python3 scripts/memory_cli.py init` for a new memory root. The Windows collector installer records its `--archive-root` as the active archive, so later CLI calls can omit `--root`. An explicit `--root` or `MEMORY_WUXIAN_ROOT` still overrides that pointer.
3. Append each user and assistant message with `append`; one user message plus its assistant response forms a completed round.
4. Let the native collector mark a summary due after 5 completed rounds or 20,000 visible characters. A character threshold reached during an answer is acted on only after that answer's `final_answer` closes the round.
5. Let the one-shot semantic worker generate and ingest the AI summary, then exit. Use `make-summary-job` and [summary prompt](prompts/summarize.md) for manual recovery.
6. Use `retrieve` for earlier topics. Let it search indexes first and raw records second. Retrieval is read-only and does not require the archive write lock; query logging is skipped automatically when the caller lacks write permission.
7. Base answers on the recovered raw segment and report the returned verification level.
8. Run `heartbeat` for validation and recovery. Keep count-based events as primary triggers.
9. Preview `rebuild-state`, `rebuild-conversations`, or `rebuild-indexes` before applying a recovery operation.
10. Use the native collector for automatic Codex import. Use `sync-codex` only as a manual compatibility and recovery adapter. Both paths must remain idempotent and storage-compatible.
11. Use `import-chatgpt` for an official ChatGPT data-export ZIP, extracted directory, or `conversations.json`. It is incremental and idempotent, but it is not a real-time ChatGPT listener. The same experimental adapter is available under Dashboard > Settings; current automated coverage uses synthetic exports and does not constitute validation against a real user export.
12. When desktop backup is configured, confirm the returned snapshot path after each successful mutation.
13. Use `backup` to create a verified recovery snapshot on demand and prune snapshots beyond configured retention.
14. Before editing this Skill, refresh one replaceable workspace code backup instead of adding timestamped copies. Never place a full live archive in development outputs.
15. At the start of each user turn, run `context-refresh-status`. When due, load `context-capsule` into the current reasoning context and run `ack-context-refresh` only after the capsule was read. Do not quote the capsule to the user unless requested, and never archive it as a source message.
16. When the user names another or historical Codex conversation and asks to continue it or restore its latest messages, run `conversation-tail --title "..." --exclude-conversation-id "codex:<active-task-id>" --messages N`. Resolve the title after excluding the active task and before selecting messages. Never substitute the latest conversation when the title is missing or ambiguous. When the user confirms a title-to-task relationship, persist it with `register-title` so later retrieval does not depend on mutable client title metadata.
17. Let the dashboard render its last successful browser-local response immediately. Serve `memory/dashboard/status-snapshot.json` without blocking the first paint, rebuild it from authoritative records in the background, and animate changed values when the refreshed snapshot arrives. On Windows, dashboard status reads must not create visible console subprocesses.
18. For federation, run `init-node` once, register only explicitly trusted peers, and use `export-delta`, `inspect-bundle`, and `import-delta` for offline exchange.
19. Use `sync-peer` only after SSH host identity is present in the local known-hosts trust store. Select `posix` or `powershell` to match the remote shell.
20. Use `retrieve-global` for cross-device history. Treat a peer result as verified only after its imported artifact hash has been checked.
21. Use `revoke-peer` to reject future imports and SSH pulls from a device. Revocation does not silently delete previously imported history.
22. Use `cloud-configure`, `cloud-pair-export`, and `cloud-pair-import` to prepare an explicitly selected iCloud Drive, OneDrive, or compatible synchronized directory.
23. Let users manage routine cloud synchronization from Dashboard > Settings. The cloud switch must enable or disable both transport configuration and its background scheduler, the status view must expose the configured directory and scheduler state, and the manual sync command must run one encrypted exchange pass without requiring an AI conversation.
24. Keep `cloud-enable`, `cloud-disable`, and `cloud-sync` as equivalent CLI and recovery controls. The scheduled task wakes every five minutes, while ordinary exports are coalesced and empty checks create no files.
25. Treat all three localized README files as one documentation contract. Update and verify English, Simplified Chinese, and Japanese in the same change.
26. On macOS, let the package installer rebuild
    `~/Desktop/Memory無限操作台.app` from the packaged application, refresh its
    configuration from the current Python, Skill, and active archive paths,
    then verify its version, code signature, executable hash, and self-check.
27. On Windows installation or upgrade, preserve the active archive pointer and recreate `Memory无限状态台.lnk` by default; never retain an absolute Python path from an older Codex runtime. A bare Skill copy has no traditional installer UI, so first activation must run the supplied bootstrap and shortcut installer.
28. Let the native collector update each conversation's Token ledger
    incrementally. Use `token-usage-backfill` in preview mode before `--apply`
    when retained historical Codex rollout files need to be measured. Do not
    infer missing usage from archived text or claim that ChatGPT exports expose
    model-consumption telemetry.
29. Use `environment-init`, explicit root and project bindings, and
    `environment-register` to establish Environment Registry state. Preview
    every registration or install before `--apply`.
30. Let `cloud-sync` process both archive and Environment streams. Incoming
    Environment material is validated into staging first; use
    `environment-incoming-status` and the dashboard Environment view before an
    explicit acceptance or installation.
31. Use `environment-conflicts` and `environment-promotions` for current
    governance state. Resolve a conflict or advance a promotion only with
    explicit reviewer and evidence fields; never infer approval from recency or
    successful transfer.
32. Validate a governance insight with `work-system-governor`, then use
    `environment-governance-propose` to preview and persist the immutable local
    envelope. Use `environment-governance-proposals` to inspect local and peer
    proposals; acceptance remains a separate global Owner workflow.
33. Validate product evolution reports with `work-system-governor`, then use
    `environment-product-evolution-record` to preserve and exchange the
    immutable evidence record. Peer records stay read-only and never trigger
    product remediation or governance acceptance.
34. Use `environment-governance-ai-discover` for model-free discovery,
    `environment-governance-ai-status` for inspection, and
    `environment-governance-ai-tick` for a bounded one-shot review. Configure
    or enqueue only through preview-first CLI commands. Human review remains
    mandatory before any downstream acceptance or installation.
35. Before implementing a product change, identify its canonical owner in
    `PRODUCT_ARCHITECTURE.md`. If it adds or relocates production code, update
    `docs/module-architecture.json` first, then run the architecture contract
    before focused behavioral tests.
36. On macOS, run `scripts/install_macos_transaction.py` for an existing
    installation. Treat its isolated candidate probe, live PID replacement,
    telemetry freshness, dashboard self-check, and rollback proof as one
    indivisible update contract.
37. Before a time-bounded report reads Memory無限, run
    `scripts/archive_waterline.py --cutoff <ISO-8601>`. Use `--backfill` only
    for the exact lagging retained rollout files and require a final `covered`
    result.
38. During collector startup, persist any due semantic job but defer AI
    execution to the independent semantic-backfill scheduler. Do not hold
    collector readiness or update cutover open while a Codex CLI summary runs.
39. Let native capture atomically record coalescing backup debt instead of
    copying the complete archive inline. Let the maintenance worker create one
    complete snapshot for all pending mutations, and clear the debt only after
    that snapshot succeeds.
40. Register the bundled E5 interface with
    `environment-register-semantic-runtime --origin-node-id <node>`, let the
    existing `environment-v1` stream transport it, and use
    `environment-realize-semantic-runtime --apply` only after explicit review
    on the receiving device. Use `semantic-runtime-status` to verify the
    bundled contract, registered revision, model artifacts, and local runtime.
41. Treat `configuration-compile`, `configuration-explain`, and
    `environment-capability-status` as stateless read-only diagnostics. They
    must not construct `MemoryStore`, initialize an archive, take an archive
    lock, alter trust, grant permissions, install capabilities, or start
    synchronization.
42. Keep the v2.5 effective-configuration contract closed and deterministic.
    Unknown or duplicate keys and invalid values fail closed. Preserve
    `--root`, `MEMORY_WUXIAN_ROOT`, active-root pointer, then configured-root
    precedence.
43. Memory-sharing scopes are design-only until a separately approved
    multi-user, third-party-write, partial-sharing, hosted-service,
    non-shareable-data, or cross-identity requirement activates the review.
    Do not add runtime scope fields or controls before that decision.

## Commands

```bash
python3 scripts/memory_cli.py init
python3 scripts/memory_cli.py append --speaker user --text "..."
python3 scripts/memory_cli.py append --speaker assistant --text "..."
python3 scripts/memory_cli.py sync-codex --session-file ~/.codex/sessions/YYYY/MM/DD/rollout-....jsonl
python3 scripts/memory_cli.py token-usage-backfill
python3 scripts/memory_cli.py token-usage-backfill --apply
python3 scripts/memory_cli.py import-chatgpt --export /path/to/chatgpt-export.zip
python3 scripts/memory_cli.py status
python3 scripts/memory_cli.py context-refresh-status
python3 scripts/memory_cli.py context-capsule
python3 scripts/memory_cli.py ack-context-refresh
python3 scripts/memory_dashboard.py --root /path/to/archive --config /path/to/config.yaml --window
python3 scripts/memory_cli.py backup
python3 scripts/memory_cli.py make-summary-job
python3 scripts/semantic_worker.py --root memory --config config.yaml --job memory/pending/<job>.json
python3 scripts/semantic_backfill.py --root memory --config config.yaml --max-jobs 20
python3 scripts/memory_cli.py ingest-summary --job memory/pending/<job>.json --summary-json <summary>.json
python3 scripts/memory_cli.py retrieve --query "..."
python3 scripts/memory_cli.py retrieve --query "..." --mode current-policy
python3 scripts/memory_cli.py semantic-runtime-status
python3 scripts/memory_cli.py environment-register-semantic-runtime --origin-node-id <node>
python3 scripts/memory_cli.py environment-register-semantic-runtime --origin-node-id <node> --apply
python3 scripts/memory_cli.py environment-realize-semantic-runtime
python3 scripts/memory_cli.py environment-realize-semantic-runtime --apply
python3 scripts/memory_cli.py conversation-tail --title "Codex conversation title" --exclude-conversation-id "codex:<active-task-id>" --messages 20
python3 scripts/memory_cli.py register-title --conversation-id "codex:<task-id>" --title "Confirmed title"
python3 scripts/memory_cli.py rebuild-state
python3 scripts/memory_cli.py rebuild-state --apply
python3 scripts/memory_cli.py rebuild-conversations
python3 scripts/memory_cli.py rebuild-conversations --apply
python3 scripts/memory_cli.py rebuild-indexes
python3 scripts/memory_cli.py rebuild-indexes --apply
python3 scripts/memory_cli.py rebuild-deterministic-indexes
python3 scripts/memory_cli.py heartbeat --check-only
python3 scripts/memory_cli.py heartbeat
python3 scripts/memory_cli.py heartbeat --repair
python3 scripts/memory_cli.py init-node --display-name "This computer"
python3 scripts/memory_cli.py add-peer --node-id <peer-node-id>
python3 scripts/memory_cli.py export-delta --output /trusted/path/update.mwxb --target-node-id <peer-node-id>
python3 scripts/memory_cli.py inspect-bundle --bundle /trusted/path/update.mwxb
python3 scripts/memory_cli.py import-delta --bundle /trusted/path/update.mwxb --expected-node-id <peer-node-id>
python3 scripts/memory_cli.py rebuild-global-index
python3 scripts/memory_cli.py retrieve-global --query "..."
python3 scripts/memory_cli.py federation-status
python3 scripts/memory_cli.py sync-peer --node-id <peer-node-id>
python3 scripts/memory_cli.py revoke-peer --node-id <peer-node-id>
python3 scripts/memory_cli.py cloud-configure --directory /path/to/synchronized/MemoryWuxianExchange
python3 scripts/memory_cli.py cloud-pair-export
python3 scripts/memory_cli.py cloud-pair-import --pairing-file /trusted/path/peer.json
python3 scripts/memory_cli.py cloud-enable
python3 scripts/memory_cli.py cloud-disable
python3 scripts/memory_cli.py cloud-sync
python3 scripts/memory_cli.py cloud-sync --force
python3 scripts/memory_cli.py cloud-status
python3 scripts/memory_cli.py environment-init
python3 scripts/memory_cli.py environment-status
python3 scripts/memory_cli.py environment-incoming-status
python3 scripts/memory_cli.py environment-process-incoming
python3 scripts/memory_cli.py environment-conflicts
python3 scripts/memory_cli.py environment-promotions
python3 scripts/memory_cli.py environment-governance-propose --proposal-json /path/to/proposal.json
python3 scripts/memory_cli.py environment-governance-proposals
python3 scripts/memory_cli.py environment-product-evolution-record --record-json /path/to/product-evolution.json
python3 scripts/memory_cli.py environment-product-evolution-records
python3 scripts/memory_cli.py environment-governance-ai-discover
python3 scripts/memory_cli.py environment-governance-ai-status
python3 scripts/memory_cli.py environment-governance-ai-enqueue --item-json /path/to/item.json
python3 scripts/memory_cli.py environment-governance-ai-configure --policy-json /path/to/policy.json
python3 scripts/memory_cli.py environment-governance-ai-tick --run-ai --maximum-batches 1
python3 scripts/memory_cli.py configuration-compile
python3 scripts/memory_cli.py configuration-explain
python3 scripts/memory_cli.py environment-capability-status
python3 scripts/memory_cli.py environment-capability-status --peer-offer /path/to/peer-offer.json
python3 scripts/install_governance_ai.py --archive-root /path/to/memory --skill-root /path/to/memory-wuxian --python-executable /path/to/python --load
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py --archive-root /path/to/memory --load
powershell -ExecutionPolicy Bypass -File scripts/build_native_collector.ps1
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_windows.ps1
python scripts/install_agent_rules.py --agents-file /path/to/workspace/AGENTS.md
python scripts/install_codex_autosync_windows.py --archive-root C:\path\to\memory --load
python scripts/install_auto_update.py --skill-root /path/to/memory-wuxian
python scripts/auto_update.py --check-only --force
powershell -ExecutionPolicy Bypass -File scripts/install_dashboard_shortcut_windows.ps1 -SkillRoot C:\path\to\memory-wuxian -ArchiveRoot C:\path\to\memory -PythonExecutable C:\path\to\python.exe
```

Use `semantic_backfill.py` for historical summary debt. It processes higher-level
parent jobs before Level-1 jobs, is safe to rerun, and creates one recovery snapshot
after the batch instead of copying the complete archive after every summary. Keep
`--max-jobs` bounded for routine maintenance; `--max-jobs 0` drains all due work.

Pass `--root <memory-directory>` before the subcommand to use a memory archive outside this skill folder.

## Load supporting material selectively

- Read [implementation.md](references/implementation.md) before changing storage formats, counters, summary hierarchy, retrieval behavior, state recovery, locking, privacy behavior, or client integration.
- Read [schemas.md](references/schemas.md) when constructing or validating raw records, summary JSON, indexes, state, or retrieval output.
- Read [decisions.md](references/decisions.md) before changing architectural behavior.
- Read [deferred-memory-scope-design.md](references/deferred-memory-scope-design.md)
  before proposing memory-sharing boundaries. It is a design trigger, not an
  implemented privacy feature.
- Read [release-rehearsal.md](references/release-rehearsal.md) before release
  claims. Run `scripts/run_release_rehearsal.py`; never describe unrun or
  evidence-free scenarios as passed. During candidate CI, run the full
  unittest suite once and pass its retained log with
  `--reuse-unittest-evidence`; the rehearsal then emits one hashed reference
  log per covered contract without executing those modules again. Feature
  branches use PR CI only, `main` uses one complete same-SHA candidate gate,
  and the release workflow builds installers from that proof.
- Read [AGENTS.md](AGENTS.md) when integrating this skill into an Agent's persistent operating rules.
- Use files in `templates/` as output contracts and files in `prompts/` as Agent prompts.

## Client integration boundary

Installing the Skill alone does not intercept Codex events. Automatic capture requires the supplied macOS LaunchAgent or Windows scheduled task. Both keep only the Rust collector alive, use immediate native filesystem events plus an adaptive 5-second, 30-second, and 5-minute metadata fallback, and share the same archive contract. They import user messages, visible assistant commentary/final answers, lightweight task-timeline tool activity, and successful structured file-change diffs from top-level sessions; they exclude subagent sessions, system prompts, hidden reasoning, and general tool output. When a complete-round boundary makes a summary due, the collector runs one ephemeral Codex CLI summary worker and waits for it to exit. Python remains available for low-frequency maintenance, retrieval, reconstruction, and summary ingestion.

Federation is a separate low-frequency layer and does not change collector
ownership of the local archive. By default, imported replicas live in the
sibling `<archive>-federation-cache`, remain read-only, and are omitted from the
desktop primary-archive backup. SSH protects and authenticates the transport;
the offline `.mwxb` bundle itself is neither encrypted nor signed. Federation
does not use OpenAI login sessions and does not provide automatic public address
discovery, NAT traversal, or mobile access. The optional cloud-folder transport
uses the user's existing filesystem synchronization client without receiving
its account credentials. It signs and encrypts each target-specific envelope
before publication and keeps the five-second local collector path unchanged.
