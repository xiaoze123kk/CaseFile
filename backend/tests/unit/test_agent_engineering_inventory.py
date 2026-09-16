"""Detect new runtime client constructors that bypass the audited factories."""

from casefile.benchmark.agent_engineering_inventory import inventory


def test_runtime_client_sites_are_accounted_for() -> None:
    result = inventory()
    assert len(result["agents"]) == 41
    assert result["client_sites"]
    for site in result["client_sites"]:
        if site["factory"].startswith("casefile.agent_runtime.deepseek_transport."):
            continue
        assert site["file"] in {"deepseek_transport.py", "structured_output.py"}, site
        assert site["explicit_http_client"], site
