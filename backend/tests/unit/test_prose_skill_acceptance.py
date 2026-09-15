"""Quality acceptance must use readable, reproducibly compiled public inputs."""

import json

from casefile.benchmark.prose_skill_acceptance import load_cases


def test_clean_cases_keep_bound_references_and_authored_faults():
    suite, cases = load_cases()
    assert len(cases) == 3
    assert "\ufffd" not in json.dumps(suite, ensure_ascii=False)
    assert cases[0]["common"]["previous_scene_render"] is None
    assert cases[1]["common"]["previous_scene_render"]["scene_id"] == "scene_1"
    assert cases[2]["common"]["previous_scene_render"]["scene_id"] == "scene_2"
    assert all(c["gold"].consensus["scene_verdict"] == "fail" for c in cases)
    assert all(c["gold"].consensus["render_hash"] for c in cases)
