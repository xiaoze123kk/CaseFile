from casefile.application.chat_public_events import public_feedback_events
from casefile.application.workflow.goal_session import _goal_actions
from casefile_contracts import PublicAgentRun


def test_follow_up_actions_match_the_write_boundary() -> None:
    assert not _goal_actions("running", 1)["can_follow_up"]
    assert not _goal_actions("interpreting", 0)["can_follow_up"]
    assert not _goal_actions("completed", 0)["can_follow_up"]
    assert _goal_actions("completed", 1)["can_follow_up"]


def test_activity_pairs_stable_ids_and_exposes_only_confirmed_references() -> None:
    run = PublicAgentRun.model_validate(
        {
            "run_id": 1,
            "status": "running",
            "activity": "reading",
            "cancellable": True,
            "failure": None,
        }
    )
    events = [
        {
            "sequence_no": 1,
            "event_type": "tool.started",
            "stage": "responding",
            "payload": {
                "tool": "search_casefile",
                "query": "SECRET",
                "object_ids": ["unconfirmed"],
            },
        },
        {
            "sequence_no": 2,
            "event_type": "tool.completed",
            "stage": "responding",
            "payload": {
                "tool": "search_casefile",
                "valid": True,
                "object_ids": ["object-a"],
                "query": "SECRET",
            },
        },
    ]
    public = [
        event.model_dump(mode="json")
        for event in public_feedback_events(events, run, draft_id=3, draft_revision=4)
    ]
    assert public[0]["activity_id"] == public[1]["activity_id"] == 1
    assert public[0]["object_ids"] == []
    assert public[1]["object_ids"] == ["object-a"]
    assert public[1]["draft_revision"] == 4
    assert "SECRET" not in str(public)


def test_invalidated_preview_is_not_returned_during_replay() -> None:
    run = PublicAgentRun.model_validate(
        {"run_id": 1, "status": "failed", "activity": None, "cancellable": False, "failure": None}
    )
    events = [
        {"sequence_no": 1, "event_type": "message.preview_started", "payload": {}},
        {
            "sequence_no": 2,
            "event_type": "message.preview_delta",
            "payload": {"preview_sequence": 1, "offset": 0, "text": "撤回预览"},
        },
        {
            "sequence_no": 3,
            "event_type": "message.preview_invalidated",
            "payload": {"discard": True},
        },
    ]
    public = public_feedback_events(events, run, draft_id=3, draft_revision=4)
    assert all(event.root.event != "message.preview_delta" for event in public)
