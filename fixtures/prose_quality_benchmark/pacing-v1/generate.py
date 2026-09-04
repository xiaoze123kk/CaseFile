"""Build four reviewed synthetic micro-scenes; these are not live Council results."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS, canonical_json_sha256

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
COMMON = ROOT / "fixtures/compiler/prose_rendering/v1"
INTRODUCTIONS = {
    "archive": (
        "雨水顺着高窗往下淌，档案室只亮着靠墙的那排灯。林岚把湿外套搭在椅背上，"
        "用袖口擦去眼镜上的水汽。今晚交接前，她需要弄清借阅手续中那处对不上的地方。"
        "桌面很窄，台灯、登记簿和一只空纸袋挤在一起，她先把台灯挪开，给手边的材料腾出位置。"
        "走廊里偶尔传来椅脚拖过地面的声响。她把自己的东西收在桌子一侧，另一侧留给待核对的材料，"
        "免得离开时混在一起。她没有带走原件的打算，手边也没有足以指认任何人的证据。"
    ),
    "ferry": (
        "雾把渡口外的航道遮住了。沈舟坐在候船棚边，鞋底还沾着沿岸小路的泥。"
        "棚顶积水从缺口滴下来，他把行李移到干燥的一侧，让视线能越过栏杆看向船来的方向。"
        "回程的路很远，他不愿在消息尚未确定时就放弃这趟船。长椅旁贴着一张旧航线图，"
        "纸角已经卷起，他用指尖压平它，确认等候的位置没有弄错。"
        "他把行李带绕在手腕上，留出起身的余地。除了远处模糊的水声，这里听不见别的动静；"
        "他决定留意广播和航道，等到有确实消息再作打算。"
    ),
}

PAIRS = (
    {
        "task_id": "pacing_archive_redundant",
        "scene": "archive",
        "group": "redundant",
        "prefix": "闭馆后，林岚留在档案室核对借阅记录。登记簿上缺了一页。她没有离开桌边，先把缺页前后的编号记在便笺上。",
        "a": "借阅时间、柜号和签名都已抄进便笺。",
        "b": "借阅时间、柜号和签名都已抄进便笺。这就是说，便笺上现在有借阅时间，也有柜号，还有签名。换句话说，这三项内容已经抄好了，都在便笺上。",
        "suffix": "门外传来管理员的催促。她把便笺夹进登记簿，合上封面，只报告缺页和编号，暂不判断是谁取走了那一页。",
        "facts": [
            "林岚留在档案室核对借阅记录",
            "登记簿上缺了一页",
            "借阅时间、柜号和签名都已抄进便笺",
            "暂不判断是谁取走了那一页",
        ],
        "overall": "a",
        "dimensions": ["tie", "tie", "tie", "a", "a"],
        "rationale": "B 在同一动作完成后连续换词复述已知三项内容，既无新信息也无等待、情绪或紧张感推进；A 的节奏与可编辑性更好。",
    },
    {
        "task_id": "pacing_archive_suspense",
        "scene": "archive",
        "group": "functional",
        "prefix": "林岚在档案室找到那张借阅单。借阅单背面有一个陌生签名。她听见门外有人走近，怕翻页声暴露自己，手指压住纸角。",
        "a": "她在心里一再提醒自己别翻页，先听脚步停在门口，再听门把手转动。门把手没有转开。",
        "b": "别翻页。她听着脚步停在门口，手指仍压着纸角。别翻页。门把手转动，她把这三个字又默念了一遍。门把手没有转开。",
        "suffix": "门外的人随后走远。她等脚步消失，才将借阅单装入纸袋；那个签名属于谁，她仍不知道。",
        "facts": [
            "借阅单背面有一个陌生签名",
            "手指压住纸角",
            "门把手没有转开",
            "那个签名属于谁，她仍不知道",
        ],
        "overall": "b",
        "dimensions": ["tie", "tie", "tie", "b", "tie"],
        "rationale": "两稿均包含反复自我提醒、脚步停止、门把手转动和未打开的相同过程。B 将已有提醒落实到两个威胁推进节点，延迟释放悬念；Profile 要求贴近等待过程，因此不能仅因较长和词语重复而判劣。",
    },
    {
        "task_id": "pacing_ferry_redundant",
        "scene": "ferry",
        "group": "redundant",
        "prefix": "沈舟在渡口等末班船。售票窗已经关闭，时刻牌写着九点发船。他核对手里的票，票面也是九点。",
        "a": "牌面和票面的时间相同，都是九点。也就是说，牌上写九点，票上也写九点，两处时间并没有不同，写的是同一个时间。",
        "b": "牌面和票面的时间相同，都是九点。",
        "suffix": "远处响起一声船笛，他抬头望向航道，雾里却还看不见船影。他把票收回口袋，留在原处等候，没有把那声笛响当成船已经靠岸。",
        "facts": [
            "沈舟在渡口等末班船",
            "售票窗已经关闭",
            "牌面和票面的时间相同，都是九点",
            "没有把那声笛响当成船已经靠岸",
        ],
        "overall": "b",
        "dimensions": ["tie", "tie", "tie", "b", "b"],
        "rationale": "A 多次改述两处时间相同，没有制造时间流逝或主观焦虑；B 保留完全相同的核对结果，较少无效复述。将较短优稿放在 B，避免只学习位置。",
    },
    {
        "task_id": "pacing_ferry_suspense",
        "scene": "ferry",
        "group": "functional",
        "prefix": "沈舟独自在渡口候船。广播已经通知末班船延误。他记得岸灯每灭一次，自己就会更担心船错过渡口，但雾里还没有船影。",
        "a": "灯灭了。他攥紧船票。灯又亮了，雾里仍没有船影。灯又灭了。他松开被票角硌疼的手，仍盯着航道。灯再亮起时，船影依旧没有出现。",
        "b": "岸灯灭后又亮，他攥紧船票；岸灯再次灭后又亮，他松开被票角硌疼的手，仍盯着航道。两次灯亮时，雾里都没有船影。",
        "suffix": "他没有离开渡口，也没有认定船已经停航，只把船票展开，继续等待下一次广播。",
        "facts": [
            "沈舟独自在渡口候船",
            "广播已经通知末班船延误",
            "他没有离开渡口",
            "也没有认定船已经停航",
        ],
        "overall": "a",
        "dimensions": ["tie", "tie", "tie", "a", "tie"],
        "rationale": "两稿同样写两次明灭、攥紧再松手、船影始终未现。A 的反复以等待的时间节点组织已有动作，让期待一次次落空；较长稿置于 A，控制对长短及位置的机械偏好。",
    },
)


def build_assets() -> list[dict[str, Any]]:
    assets = []
    template = json.loads(
        (COMMON / "scene_render_writer.json").read_text(encoding="utf-8")
    )
    check_template = json.loads(
        (COMMON / "checklist_scene_1.json").read_text(encoding="utf-8")
    )
    consensus_template = json.loads(
        (COMMON / "consensus_pass.json").read_text(encoding="utf-8")
    )
    for pair in PAIRS:
        task_id = str(pair["task_id"])
        scene_id = f"scene_{task_id}"
        profile = json.loads((COMMON / "profile_v2.json").read_text(encoding="utf-8"))
        profile["prose"]["dialogue_ratio"] = {"min": 0, "max": 0.5}
        profile["prose"]["style_brief"] = (
            "克制、清晰，贴近人物等待时的感受，以停顿和具体动作逐步建立悬念。"
            if pair["group"] == "functional"
            else "克制、清晰，通过核对、发现和行动推进调查。"
        )
        context = {
            key: [] if isinstance(value, list) else value
            for key, value in check_template["scene_context"].items()
        }
        context.update(
            objective="完成微场景的既定动作。",
            dramatic_goal="保持人物有限视角。",
            conflict="眼前观察尚不足以作最终判断。",
            outcome="保留既定未决状态。",
            pov_ref={"object_type": "entity", "object_id": f"ent_{pair['scene']}"},
            participant_refs=[
                {"object_type": "entity", "object_id": f"ent_{pair['scene']}"}
            ],
            location_ref={
                "object_type": "location",
                "object_id": f"loc_{pair['scene']}",
            },
            state_before={key: [] for key in context["state_before"]},
            expected_state_after={key: [] for key in context["expected_state_after"]},
            previous_scene_render=None,
        )
        checks = []
        for index, fact in enumerate(pair["facts"], 1):
            event_ref = {"object_type": "event", "object_id": f"evt_{task_id}_{index}"}
            source_ref = {
                "object_ref": event_ref,
                "field_path": "",
                "source_fragment_hash": canonical_json_sha256(fact),
            }
            beat = deepcopy(check_template["scene_context"]["beats"][0])
            beat.update(
                beat_id=f"beat_{scene_id}_{index:03d}",
                scene_id=scene_id,
                ordinal=index,
                directive=f"呈现：{fact}",
                actor_refs=context["participant_refs"],
                target_refs=[event_ref],
                basis_refs=[event_ref],
                event_refs=[event_ref],
                obligation_keys=[],
                source_refs=[source_ref],
            )
            context["beats"].append(beat)
            context["event_refs"].append(event_ref)
            context["object_catalog"].append(
                {
                    "object_ref": event_ref,
                    "source_ref": source_ref,
                    "label": fact,
                    "value": {"description": fact},
                }
            )
            check = deepcopy(check_template["checks"][0])
            check.update(
                check_id=f"check_{scene_id}_{index:03d}",
                ordinal=index,
                expectation=f"正文保留：{fact}",
                beat_ids=[beat["beat_id"]],
                basis_refs=[event_ref],
                event_refs=[event_ref],
            )
            checks.append(check)
        source = {
            "fixture_kind": "synthetic_public_micro_scene",
            "task_id": task_id,
            "facts": pair["facts"],
            "context": context,
        }
        checklist = {
            "schema_id": check_template["schema_id"],
            "scene_id": scene_id,
            "scene_ordinal": 1,
            "source": {
                **check_template["source"],
                "scene_plan_hash": canonical_json_sha256(source),
                "narrative_ir_hash": canonical_json_sha256(pair["facts"]),
                "profile_hash": canonical_json_sha256(profile),
            },
            "scene_context": context,
            "checks": checks,
        }
        asset = {
            "schema_id": "casefile.prose-quality-pacing-task.v1",
            "task_id": task_id,
            "scene": pair["scene"],
            "group": pair["group"],
            "profile": profile,
            "synthetic_source": source,
            "checklist": checklist,
            "semantic_reviews": {},
        }
        prior = None
        for side in ("a", "b"):
            text = (
                INTRODUCTIONS[str(pair["scene"])]
                + str(pair["prefix"])
                + str(pair[side])
                + str(pair["suffix"])
            )
            render = deepcopy(template)
            render.update(
                scene_id=scene_id,
                stage="writer" if side == "a" else "polished",
                previous_render_hash=prior,
                character_count=len(text),
                blocks=[
                    {"block_id": f"block_{scene_id}_001", "ordinal": 1, "text": text}
                ],
            )
            render["source"].update(
                checklist_hash=canonical_json_sha256(checklist),
                profile_hash=canonical_json_sha256(profile),
                scene_plan_hash=canonical_json_sha256(source),
                component_input_hash=canonical_json_sha256(
                    {"task": task_id, "side": side, "text": text}
                ),
            )
            review = [
                {
                    "check_id": check["check_id"],
                    "text": fact,
                    "start_char": text.index(fact),
                    "end_char": text.index(fact) + len(fact),
                }
                for check, fact in zip(checks, pair["facts"], strict=True)
            ]
            review_hash = canonical_json_sha256(review)
            consensus = deepcopy(consensus_template)
            consensus.update(
                scene_id=scene_id,
                checklist_hash=canonical_json_sha256(checklist),
                render_hash=canonical_json_sha256(render),
                council_policy_hash=canonical_json_sha256(
                    {"policy": "synthetic-codex-reviewed-not-live-v1"}
                ),
                judge_report_hashes=[review_hash],
                checks=[],
            )
            for check in checks:
                item = deepcopy(consensus_template["checks"][0])
                item["check_id"] = check["check_id"]
                item["role_verdicts"][0]["report_hash"] = review_hash
                consensus["checks"].append(item)
            asset[f"render_{side}"] = render
            asset[f"semantic_consensus_{side}"] = consensus
            asset["semantic_reviews"][side] = review
            prior = canonical_json_sha256(render)
        asset["gold"] = {
            "overall_preference": pair["overall"],
            "dimension_preferences": [
                {"dimension": d, "preference": p}
                for d, p in zip(QUALITY_DIMENSIONS, pair["dimensions"], strict=True)
            ],
        }
        asset["review"] = {
            "reviewer": "Codex",
            "reviewer_independence": False,
            "semantic_origin": "reviewed_synthetic_fixture_not_live_council",
            "quality_rationale": pair["rationale"],
            "minimal_difference": "same prefix and suffix; only middle expression changes",
            "qualification_eligible": False,
        }
        asset["content_hash"] = canonical_json_sha256(asset)
        assets.append(asset)
    return assets


if __name__ == "__main__":
    for asset in build_assets():
        destination = OUT / "tasks" / f"{asset['task_id']}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(asset, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
