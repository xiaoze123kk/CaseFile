"""Run the independent CaseFile TaskRun worker process."""

import os

from casefile.agent_runtime import FakeProvider
from casefile.data_postgres.session import create_database_engine, create_session_factory
from casefile.worker import Worker, WorkerConfig


def main() -> None:
    engine = create_database_engine()
    factory = create_session_factory(engine)
    provider_mode = os.environ.get("CASEFILE_PROVIDER_MODE", "openai")
    if provider_mode not in {"openai", "fake"}:
        raise SystemExit("CASEFILE_PROVIDER_MODE must be 'openai' or 'fake'")
    provider_factory = (lambda _task: FakeProvider()) if provider_mode == "fake" else None
    Worker(
        factory,
        config=WorkerConfig.from_environment(),
        provider_factory=provider_factory,
    ).run_forever()


if __name__ == "__main__":
    main()
