from pathlib import Path

from casefile.benchmark.general_mutation_lineage import general_mutation_runtime_fingerprint


def test_general_mutation_runtime_fingerprint_is_stable() -> None:
    root = Path(__file__).resolve().parents[3]
    first = general_mutation_runtime_fingerprint(root)
    assert len(first) == 64
    assert first == general_mutation_runtime_fingerprint(root)
