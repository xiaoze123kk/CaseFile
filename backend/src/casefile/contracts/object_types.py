"""Ordered CaseFile object collections shared by validation and domain consumers."""

COLLECTION_TYPES: tuple[tuple[str, str], ...] = (
    ("resolution_specs", "resolution_spec"),
    ("entities", "entity"),
    ("relationships", "relationship"),
    ("locations", "location"),
    ("events", "event"),
    ("information_units", "information_unit"),
    ("claims", "claim"),
    ("hypotheses", "hypothesis"),
    ("reasoning_paths", "reasoning_path"),
    ("constraints", "constraint"),
    ("structure_locks", "structure_lock"),
)

COLLECTION_OBJECT_TYPES = dict(COLLECTION_TYPES)
COLLECTION_BY_TYPE = {kind: collection for collection, kind in COLLECTION_TYPES}
