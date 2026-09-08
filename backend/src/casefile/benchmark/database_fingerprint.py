"""The existing ordered public-column fingerprint used by backend benchmarks."""

import json
from hashlib import sha256

from sqlalchemy import Engine, text


def public_schema_fingerprint(engine: Engine) -> str:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name,column_name,data_type "
                "FROM information_schema.columns WHERE table_schema='public' "
                "ORDER BY table_name,ordinal_position"
            )
        ).all()
    return sha256(
        json.dumps([list(row) for row in rows], separators=(",", ":")).encode()
    ).hexdigest()
