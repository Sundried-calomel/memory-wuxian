# Deferred Memory Scope Design

## Current State

Memory Wuxian is currently a single-user system. All retained memory may be
shared among the user's explicitly trusted devices. No runtime memory scope,
filter, migration, enforcement rule, or dashboard control exists.

## Reconsideration Triggers

Reopen this design before introducing any of the following:

- multi-user or team access;
- third-party AI write access;
- partial project-memory sharing;
- an externally hosted memory service;
- explicitly non-shareable retained data; or
- shared memory across different identity or organization domains.

Before such a feature is activated, present a separate proposal covering data
classes, default scope, inheritance, encryption and key ownership, read and
write authorization, migration, deletion, audit evidence, recovery, and
cross-device compatibility.

## Non-Implementation Rule

This document reserves a future decision point only. It does not authorize
schema fields, storage partitions, filters, permissions, migration, UI, or
behavior changes. Until a later explicit approval, all memory continues to use
the existing single-user sharing model.
