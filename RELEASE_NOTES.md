# v2.20.7 — dashboard and runtime repairs

- Restore a native WebView dashboard entry (`core/window.py`; requires pywebview).
- Show one activity mode; hide zero error and pending counts; move usage timestamp to footer.
- Adaptive worker (`core/live.py --loop`): 5 seconds active, 60 seconds idle after one quiet minute, 300 seconds deep idle after ten quiet minutes. Work is serialized; intervals start after a tick completes.
- Restore peer daily totals, deduplicating old and current replicas by device/message identity.
- Persist incremental model-reported usage in the new token-usage directory; unavailable historical usage remains unknown.
- Resolve rotated Codex executable paths and constrain summary source references. Summaries use GPT 6 Luna with medium reasoning in the configured installation.
- A receive gap no longer blocks independent sending. Keep the gap visible until the missing authenticated package arrives.
- Accept already-identical environment files and process the newest full selected revision.

Installer stays at 1.1.0. Complete ZIPs include it; existing installations use the regular update payload.
Known live boundary: the Windows device still awaits a missing Mac package and peer ACKs; this release does not assert remote-device recovery.
