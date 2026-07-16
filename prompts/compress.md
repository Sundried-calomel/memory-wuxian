# Runtime Compression Prompt

Compress only the active runtime context after all source messages have been successfully persisted.

The compressed result is temporary working state. Do not write it to `raw/`, replace persistent summaries with it, describe it as verbatim history, or allow it to become the only remaining representation of a conversation.
