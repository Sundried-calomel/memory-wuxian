#!/usr/bin/env python3
"""Non-persisting coordination for Environment installation lifecycles."""

from __future__ import annotations

from typing import Generic, Optional, Protocol, TypeVar


PreparedT = TypeVar("PreparedT")
ResultT = TypeVar("ResultT")


class InstallLifecycleAdapter(Protocol[PreparedT, ResultT]):
    """Domain adapter; all validation, persistence, and rollback stay here."""

    def recover(self) -> None:
        """Run the domain's existing pre-install recovery policy."""

    def prepare(self) -> PreparedT:
        """Validate inputs and produce the domain-owned prepared state."""

    def no_change_result(self, prepared: PreparedT) -> Optional[ResultT]:
        """Return the domain's terminal no-change result, if applicable."""

    def preview_result(self, prepared: PreparedT) -> ResultT:
        """Render the domain's public preview result."""

    def apply_prepared(self, prepared: PreparedT) -> ResultT:
        """Execute the domain-owned durable transaction."""


class InstallLifecycleCoordinator(Generic[PreparedT, ResultT]):
    """Order common lifecycle stages without owning any durable state."""

    __slots__ = ()

    def run(
        self,
        adapter: InstallLifecycleAdapter[PreparedT, ResultT],
        *,
        apply: bool,
    ) -> ResultT:
        adapter.recover()
        prepared = adapter.prepare()
        no_change = adapter.no_change_result(prepared)
        if no_change is not None:
            return no_change
        if not apply:
            return adapter.preview_result(prepared)
        return adapter.apply_prepared(prepared)
