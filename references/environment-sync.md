# Memory無限 Environment Synchronization

## Purpose

Memory無限 2.0 synchronizes global rules, project rules, global Skills, and
project Skills. It also provides a reviewed path for promoting
project-independent capability cores into global Skills while retaining
project-specific adapters.

## Non-goals

- A shared writable cloud archive
- Project discovery from similar folder names
- Complete-file overwrite of mixed-ownership rules
- Automatic capability promotion
- New OpenAI account or device authorization
- Replacing conversation memory, summaries, or token telemetry

## Ownership

| Object | Logical owner | Local binding | Default install policy |
| --- | --- | --- | --- |
| Global rule | user environment | managed block or owned file | review core rules |
| Project rule | registered project | project-relative path | safe update by class |
| Global Skill | global Skill owner | Codex Skill directory | verified no-conflict |
| Project Skill | project Skill owner | Skill directory + project binding | require project |

## Promotion lifecycle

```text
discovered -> review -> classified -> deprojectized -> validating
-> promotable -> accepted -> installed -> project migration verified
```

Rejected, project-only, and conflicting candidates remain durable records. They
are not repeatedly proposed unless their source evidence changes.

## Governance insight exchange

Local product architecture lessons use an independent proposal contract:

```text
local evidence -> immutable proposal -> encrypted transport -> peer replica
-> work-system-governor review -> explicit acceptance -> Environment revision
```

Memory無限 verifies proposal identity, origin, size, encoding, and content
hash. It does not decide whether the proposal is useful, general, duplicate,
conflicting, or accepted. Imported proposals never enter Rule or Skill install
staging and never mutate a global Owner automatically.

## Product evolution exchange

Validated product evolution records use the same Environment stream as
immutable, read-only evidence. They may describe bounded development history,
current state, corrected future development flow, and reusable lesson
candidates. Receiving a record does not remediate the source product or promote
any lesson into global governance. Full raw logs and project data remain local
unless a separate transport allowlist explicitly includes them.

## Defaults

- automatic download: enabled
- ordinary verified nonconflicting auto-install: enabled
- automatic core-rule overwrite: disabled
- automatic conflict resolution: disabled
- automatic unknown-project creation: disabled
- automatic capability promotion: disabled
- promotion candidate suggestions: enabled

## Release sequence

1. Contract and schema freeze
2. Read-only Environment Registry and dashboard inventory
3. Content-addressed objects and version/base tracking
4. Global and project rule installers
5. Skill package installer and rollback
6. Capability discovery and promotion
7. Three-way conflict handling
8. Signed encrypted cross-device exchange
9. Model-free installation scheduling
10. Complete dashboard controls
11. macOS and Windows bidirectional rehearsal
12. 2.0 installers, migration guidance, and verified release
