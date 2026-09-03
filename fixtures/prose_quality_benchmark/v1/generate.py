"""Build the frozen public N4.5 B3 Quality development preference set."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import rfc8785
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PROSE_QUALITY_FINDINGS_PROMPT_VERSION,
    PROSE_QUALITY_MODEL_ID,
    PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
)
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS

ROOT: Final = Path(__file__).resolve().parents[3]
OUT: Final = ROOT / "fixtures/prose_quality_benchmark/v1"
COMMON: Final = ROOT / "fixtures/compiler/prose_rendering/v1"
COMMON_ENDING: Final = (
    "离开控制台前，她再次确认封条完整，将纸质表格与现场编号交叉核对。"
    "确认已记录的信息没有遗漏后，她和同伴退到隔离门外，等待下一次核验；"
    "对任何尚未得到证据支持的解释，她都暂不采纳。"
)

TEXT_PAIRS: Final = (
    (
        "readability_clearer",
        "研究员推开实验室隔离门。备用控制系统的灯在第七次重启记录旁闪烁，这个现象被她看见之后，她开始进行核对，而核对所针对的是控制台留下的人工操作痕迹，随后时间、温度以及登录记录都被逐项抄录进纸质表格。系统发出蜂鸣，这使她要求同伴保持距离，然后沿线路检查保险模块。恢复程序显示重启由安全规则自动触发，不过控制台边的新鲜指纹造成了疑点。她只通报已确认的现象，没有透露后续日志。她封存操作面板，又核验封条编号，让同伴复述操作顺序。记录相互吻合，但按钮旁多了一道新划痕。她将差异单独标记，没有把猜测写成事实，调查因此取得人工干预的明确痕迹，并留下完整的后续核验记录。",
        "研究员推开实验室隔离门，备用控制系统的指示灯正贴着第七次重启记录闪烁。她先核对控制台上的人工操作痕迹，再把时间、温度和登录记录逐项抄进纸质表格。系统短促地蜂鸣了一声。她抬手拦住同伴，独自沿线路检查保险模块。屏幕声称恢复程序由安全规则自动触发，控制台边缘的新鲜指纹却让这句话变得可疑。她只说出已经确认的现象，避开尚未出现的后续日志。封存面板后，她核验封条编号，又让同伴复述刚才的操作顺序。两份记录完全吻合，唯独重启按钮旁多出一道新划痕。她把这处差异单独标记，不让猜测冒充事实。至此，调查取得了人工干预的明确痕迹，也为下一次核验留下完整记录。",
        "b",
        ("tie", "b", "b", "b", "b"),
    ),
    (
        "pacing_original",
        "研究员推开实验室隔离门，备用控制系统的指示灯在第七次重启记录旁闪烁。她没有急着下结论，先核对控制台留下的人工操作痕迹，把时间、温度和登录记录逐项抄进纸质表格。系统突然蜂鸣，她让同伴退到门边，自己沿线路检查保险模块。屏幕显示恢复程序由安全规则自动触发，可控制台边缘的新鲜指纹让这行说明显得可疑。她只报告已确认的现象，不触碰尚未出现的后续日志。封存面板时，她发现重启按钮旁多了一道新划痕。她核验封条编号，让同伴复述操作顺序；两份记录相互吻合。她把划痕单独标记，没有把猜测写成事实。人工干预的痕迹已经明确，下一次核验也有了完整记录。",
        "研究员推开实验室隔离门，看见备用控制系统的指示灯在第七次重启记录旁闪烁。她看了一会儿，又看了一会儿，随后核对控制台留下的人工操作痕迹。她把时间抄进纸质表格，又把温度抄进去，最后把登录记录也抄进去。系统发出蜂鸣，她让同伴保持距离，然后沿着线路检查保险模块。恢复程序说一切由安全规则自动触发，但控制台边缘有新鲜指纹。她反复确认指纹仍在那里，才把已确认的现象告诉同伴，没有提到后续日志。她封存操作面板，检查封条编号，再让同伴复述操作顺序。记录相互吻合。她又看见重启按钮旁的新划痕，把它单独标记，再次提醒自己不要把猜测写成事实。最后，调查取得人工干预的明确痕迹，并为下一次核验保留完整记录。",
        "a",
        ("tie", "a", "tie", "a", "a"),
    ),
    (
        "specificity_polished",
        "研究员进入实验室，注意到备用控制系统仍在运行，第七次重启记录就在附近。她检查控制台，发现有人操作过，于是记录了时间、温度和登录情况。系统响了一声，她让同伴别靠近，自己检查线路和保险模块。屏幕说明恢复程序是由安全规则自动触发的，不过旁边留下的指纹让她产生怀疑。她只把已经确认的情况告诉同伴，没有谈论后续日志。随后她封存操作面板，检查封条，让同伴复述操作过程。两人的记录一致，只是重启按钮附近出现一道新的划痕。她将这个差异单独记下，没有将推测当成事实。到场景结束时，人工干预的痕迹已经明确，调查材料也足以支持下一次核验。",
        "研究员推开实验室的灰色隔离门，备用控制系统的琥珀灯正贴着第七次重启记录闪烁。她用铅笔把控制台上的人工操作痕迹、时间、温度和登录记录逐项抄进纸质表格。三声短促蜂鸣逼得同伴后退，她则沿蓝色线槽检查保险模块。屏幕声称恢复程序由安全规则自动触发，触控板边缘一枚新鲜指纹却破坏了这份笃定。她只通报已确认的现象，避开尚未出现的后续日志。封存面板后，她逐位核对红色封条编号，并让同伴复述操作顺序。两份记录吻合，唯独重启按钮右侧多出一道银白划痕。她把差异圈起，不让猜测冒充事实。人工干预的痕迹已经明确，完整记录也被留给下一次核验。",
        "b",
        ("b", "b", "tie", "b", "b"),
    ),
    (
        "voice_consistency",
        "研究员推开实验室隔离门。你会看见备用控制系统的指示灯在第七次重启记录旁闪烁，而我认为她首先核对控制台上的人工操作痕迹是谨慎的。她把时间、温度和登录记录抄进纸质表格，系统随即发出蜂鸣。此时，同伴被要求保持距离，她沿线路检查保险模块。屏幕显示恢复程序由安全规则自动触发，但控制台边缘的新鲜指纹让这项判断显得可疑。研究员只讲述已确认的现象，没有泄露后续日志。我们随后看到她封存面板、核验封条编号，并要求同伴复述操作顺序。记录吻合，重启按钮旁却多了一道新划痕。她将其单独标记，没有把猜测写成事实，于是人工干预的痕迹得到确认，后续核验也有完整记录可循。",
        "研究员推开实验室隔离门，备用控制系统的指示灯在第七次重启记录旁闪烁。她压下判断，先核对控制台上的人工操作痕迹，再将时间、温度和登录记录逐项抄进纸质表格。系统忽然蜂鸣，她让同伴留在门外，自己沿线路检查保险模块。屏幕显示恢复程序由安全规则自动触发，控制台边缘的新鲜指纹却让她无法采信。她只向同伴说明已经确认的现象，对尚未出现的后续日志只字不提。封存面板后，她核验封条编号，并让同伴复述操作顺序。两份记录吻合，唯独重启按钮旁多出一道新划痕。她把差异单独标记，提醒自己不能把猜测当作事实。人工干预的痕迹已经明确，下一次核验所需的记录也完整保留下来。",
        "b",
        ("b", "tie", "b", "b", "b"),
    ),
    (
        "dialogue_naturalness",
        "研究员推开实验室隔离门，备用控制系统的指示灯在第七次重启记录旁闪烁。她说道：\"我现在要核对控制台留下的人工操作痕迹，并把时间、温度和登录记录逐项抄进纸质表格。\"系统发出蜂鸣。她又说道：\"请你保持距离，我要沿着线路检查保险模块。\"屏幕显示恢复程序由安全规则自动触发，但控制台边缘的新鲜指纹令她怀疑。她解释自己只会报告已确认的现象，不会泄露尚未出现的后续日志。封存面板并核验封条编号后，她让同伴完整复述操作顺序。同伴照做，两份记录吻合，只有重启按钮旁多出一道新划痕。她说明会将差异单独标记，不把猜测写成事实。人工干预的痕迹已经明确，完整记录也留给了下一次核验。",
        "研究员推开实验室隔离门，备用控制系统的灯在第七次重启记录旁急闪。她先核对控制台上的人工操作痕迹，把时间、温度和登录记录抄进纸质表格。蜂鸣声骤然响起。\"退到门边。\"她拦住同伴，独自沿线路检查保险模块。屏幕声称恢复程序由安全规则自动触发，触控板边缘的新鲜指纹却令她停住。\"这部分能确认，别的先不说。\"她指了指记录，避开尚未出现的后续日志。封存面板后，她核验封条编号。\"把刚才的顺序复述一遍。\"同伴说完，两份记录完全吻合，唯独重启按钮旁多出一道新划痕。她把差异单独圈起，没有让猜测变成事实。人工干预的痕迹至此明确，下一次核验也有了完整记录。",
        "b",
        ("tie", "b", "b", "b", "b"),
    ),
    (
        "balanced_paraphrase_tie",
        "研究员穿过实验室隔离门，备用控制系统的指示灯贴着第七次重启记录闪动。她暂不判断，先核对控制台上的人工操作痕迹，将时间、温度和登录记录逐项写入纸质表格。蜂鸣声响起时，她让同伴站远些，自己沿线路检查保险模块。屏幕声称恢复由安全规则自动触发，边缘的新鲜指纹却让说法生疑。她只传达已确认的现象，对尚未出现的后续日志保持沉默。封存面板后，她核验封条编号，并请同伴复述操作顺序。双方记录一致，只有重启按钮旁新添一道划痕。她把差异独立标记，不把推测当事实。人工干预的痕迹已经明确，也为下一次核验留下完整记录。",
        "备用控制系统的灯在第七次重启记录旁闪着，研究员推开实验室隔离门后先停住了。她没有下结论，而是核对控制台上的人工操作痕迹，把时间、温度与登录记录逐项抄入纸质表格。系统一声蜂鸣，她示意同伴退开，独自沿线路检查保险模块。恢复程序宣称由安全规则自动触发，可边缘的新鲜指纹使这份说明变得可疑。她只说已经确认的现象，没有涉及尚未出现的后续日志。面板封存后，她核验封条编号，让同伴复述操作顺序。两份记录吻合，唯独重启按钮旁出现一道新划痕。她单独标记差异，不让猜测成为事实。至此，人工干预痕迹明确，后续核验所需记录也完整保留。",
        "tie",
        ("tie", "tie", "tie", "tie", "tie"),
    ),
    (
        "tradeoff_tie",
        "研究员推开实验室隔离门，备用控制系统的指示灯在第七次重启记录旁闪烁。她先核对控制台上的人工操作痕迹，迅速把时间、温度和登录记录抄进纸质表格。系统蜂鸣，她叫同伴退开，沿线路查完保险模块。屏幕说恢复程序由安全规则自动触发，旁边的新鲜指纹却令人起疑。她只说已确认的现象，没有谈及后续日志。她封存面板、核验封条编号，再让同伴复述操作顺序。记录吻合，重启按钮旁却多出一道新划痕。她单独记下差异，不把猜测当事实。人工干预痕迹已经明确，完整记录也留待下一次核验。为了确认没有遗漏，她回到门边重看纸质表格，所有项目仍与控制台显示一致。",
        "研究员推开带磨砂玻璃的实验室隔离门，备用控制系统的琥珀灯在第七次重启记录旁一明一灭。她核对触控台边缘的人工操作痕迹，用削尖的铅笔把时间、温度和登录记录逐项抄进纸质表格。蜂鸣响起，她让同伴后退，沿蓝色线槽检查保险模块。恢复程序声称由安全规则自动触发，新鲜指纹却让说明显得可疑。她只通报已确认的现象，避开后续日志。封存面板并核验红色封条编号后，她让同伴复述操作顺序。两份记录吻合，重启按钮右边却多出一道银白划痕。她圈起差异，不让猜测成为事实。人工干预痕迹已经明确，记录也足以支持下一次核验。她在门边再看一遍表格，所列项目仍与控制台一致。",
        "tie",
        ("tie", "b", "tie", "a", "a"),
    ),
    (
        "original_more_vivid",
        "研究员推开实验室隔离门，备用控制系统的指示灯在第七次重启记录旁急促闪烁。她没有立刻解释，而是贴近控制台核对人工操作痕迹，把时间、温度和登录记录逐项抄进纸质表格。蜂鸣划破安静，她一把拦住同伴，独自沿线路检查保险模块。屏幕冷静地宣称恢复程序由安全规则自动触发，边缘的新鲜指纹却让这句话站不住脚。她只交代已经确认的现象，没有泄露尚未出现的后续日志。封存面板后，她逐位核验封条编号，又让同伴复述操作顺序。两份记录严丝合缝，唯独重启按钮旁多出一道新划痕。她把这处差异单独圈起，不让猜测越过事实。人工干预的痕迹已经明确，下一次核验也有了完整记录。",
        "研究员进入实验室。备用控制系统的灯在第七次重启记录旁闪烁。她检查控制台的人工操作痕迹。她记录时间。她记录温度。她记录登录记录。系统发出蜂鸣。她让同伴保持距离。她沿着线路检查保险模块。屏幕显示恢复程序由安全规则自动触发。控制台边缘有新鲜指纹，所以这件事可疑。她把已经确认的现象告诉同伴。她没有告诉同伴后续日志。她封存操作面板。她检查封条编号。她让同伴复述操作顺序。两份记录吻合。重启按钮旁多出一道新划痕。她把差异单独标记。她没有把猜测写成事实。调查取得人工干预的明确痕迹。她保留完整记录，等待下一次核验。",
        "a",
        ("a", "a", "a", "a", "a"),
    ),
)


def canonical_hash(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def _read(name: str) -> dict[str, Any]:
    return json.loads((COMMON / name).read_text(encoding="utf-8"))


def _render(
    template: dict[str, Any], text: str, *, stage: str, previous_hash: str | None, seed: str
) -> dict[str, Any]:
    value = deepcopy(template)
    text = text + COMMON_ENDING
    value["stage"] = stage
    value["previous_render_hash"] = previous_hash
    value["blocks"] = [{**value["blocks"][0], "text": text}]
    value["character_count"] = len(text)
    value["selection_reason"] = None
    value["source"]["component_input_hash"] = canonical_hash({"seed": seed})
    return value


def _consensus(template: dict[str, Any], render: dict[str, Any], seed: str) -> dict[str, Any]:
    value = deepcopy(template)
    value["round"] = render["round"]
    value["render_hash"] = canonical_hash(render)
    value["council_policy_hash"] = canonical_hash({"policy": seed})
    return value


def build_suite() -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    profile = _read("profile_v2.json")
    checklist = _read("checklist_scene_1.json")
    render_template = _read("scene_render_writer.json")
    consensus_template = _read("consensus_pass.json")
    assets: dict[str, dict[str, Any]] = {}
    descriptors: list[dict[str, Any]] = []
    for index, (focus, text_a, text_b, overall, preferences) in enumerate(
        TEXT_PAIRS, start=1
    ):
        task_id = f"quality_dev_{index:02d}_{focus}"
        render_a = _render(
            render_template, text_a, stage="writer", previous_hash=None, seed=f"{task_id}:a"
        )
        render_b = _render(
            render_template,
            text_b,
            stage="polished",
            previous_hash=canonical_hash(render_a),
            seed=f"{task_id}:b",
        )
        consensus_a = _consensus(consensus_template, render_a, f"{task_id}:a")
        consensus_b = _consensus(consensus_template, render_b, f"{task_id}:b")
        gold = {
            "overall_preference": overall,
            "dimension_preferences": [
                {"dimension": dimension, "preference": preference}
                for dimension, preference in zip(
                    QUALITY_DIMENSIONS, preferences, strict=True
                )
            ],
        }
        asset = {
            "schema_id": "casefile.prose-quality-dev-task.v1",
            "task_id": task_id,
            "profile": profile,
            "checklist": checklist,
            "render_a": render_a,
            "semantic_consensus_a": consensus_a,
            "render_b": render_b,
            "semantic_consensus_b": consensus_b,
            "gold": gold,
            "review_notes": {
                "focus": focus,
                "semantic_review": "both_candidates_pass_same_checklist",
                "quality_review": "five_dimensions_and_overall_reviewed",
            },
        }
        asset["content_hash"] = canonical_hash(asset)
        asset_path = Path(f"fixtures/prose_quality_benchmark/v1/tasks/{task_id}.json")
        descriptor = {
            "task_id": task_id,
            "focus": focus,
            "task_asset": {"path": asset_path.as_posix(), "hash": canonical_hash(asset)},
            "pair_fingerprint": canonical_hash(
                {
                    "render_a_hash": canonical_hash(render_a),
                    "render_b_hash": canonical_hash(render_b),
                    "gold": gold,
                }
            ),
        }
        descriptor["content_hash"] = canonical_hash(descriptor)
        assets[task_id] = asset
        descriptors.append(descriptor)
    suite = {
        "schema_id": "casefile.prose-quality-dev-suite.v1",
        "suite_id": "n4.5-b3-quality-public-development-v1",
        "suite_role": "development",
        "task_count": 8,
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "preference_distribution": {"a": 2, "b": 4, "tie": 2},
        "quality_model_id": PROSE_QUALITY_MODEL_ID,
        "findings_prompt_version": PROSE_QUALITY_FINDINGS_PROMPT_VERSION,
        "pairwise_prompt_version": PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
        "quality_component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "gate_thresholds": {
            "overall_accuracy_min": 8,
            "mirrored_consistency_min": 8,
            "dimension_accuracy_min": 40,
            "semantic_invalid_max": 0,
            "protocol_failure_max": 0,
            "infrastructure_failure_max": 0,
        },
        "qualification": {
            "qualified": False,
            "qualification_eligible": False,
            "development_baseline": True,
        },
        "tasks": descriptors,
    }
    suite["suite_hash"] = canonical_hash(suite)
    attestation = {
        "schema_id": "casefile.prose-quality-dev-attestation.v1",
        "suite_hash": suite["suite_hash"],
        "reviewer": "Codex",
        "reviewer_independence": False,
        "reviewed_task_count": 8,
        "passes": [
            "semantic_acceptance",
            "pair_quality_gold",
            "position_symmetry",
            "development_only",
        ],
        "allowed_use": "public_quality_development_only",
        "qualification": False,
        "unresolved_findings": [],
    }
    attestation["attestation_hash"] = canonical_hash(attestation)
    return suite, attestation, assets


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    suite, attestation, assets = build_suite()
    for task_id, asset in assets.items():
        _write(OUT / "tasks" / f"{task_id}.json", asset)
    _write(OUT / "suite.json", suite)
    _write(OUT / "review-attestation.json", attestation)


if __name__ == "__main__":
    main()
