## Compact core

This release replaces the old Python cloud-sync runtime with the direct core-v1 implementation and publishes the compact runtime source and an upgrade package.

Included: archival/summary services, dashboard and MCP retrieval, independent peer indexing, authenticated memory and environment exchange, explicit local bindings, backup/restore and changed-file update support. Large legacy summaries retain their exact content through bounded compression; file publications are ordered and acknowledged after application.

Windows live cutover exercised real memory publication, scheduled maintenance, backup and installed MCP retrieval. The release CI checks portable Python behavior on Windows, macOS and Linux. **Mac live activation and real cross-device ACK are not verified.** Queue publication must not be presented as remote delivery.

The ZIP is a **core upgrade package**, not a standalone installer. An existing native collector/envelope installation and paired device identities are required. Both peers must activate this core for core-v1 synchronization. The old v1 exchange and original histories are preserved; the previous stable release remains available. Device configuration, identities, memory contents and experimental installers are excluded.

See README.md and docs/PEER-SETUP.md for activation and compatibility details. SHA256SUMS.txt identifies the published package.
