"""Explicit immutable Quality experiment configurations; production stays on v2."""

from dataclasses import asdict, dataclass

from casefile.domain.narrative_compiler import canonical_json_sha256


@dataclass(frozen=True, slots=True)
class ProseQualityConfig:
    config_id: str
    findings_model: str
    pairwise_model: str

    @property
    def fingerprint(self) -> str:
        return canonical_json_sha256(asdict(self))


QUALITY_V2 = ProseQualityConfig("v2-flash", "deepseek-v4-flash", "deepseek-v4-flash")
QUALITY_PRO_DIAGNOSTIC = ProseQualityConfig(
    "diagnostic-pro-pairwise-v1", "deepseek-v4-flash", "deepseek-v4-pro"
)


def validate_quality_config(config: ProseQualityConfig) -> None:
    if config not in (QUALITY_V2, QUALITY_PRO_DIAGNOSTIC):
        raise ValueError("prose_quality_config_not_frozen")


def quality_config(config_id: str) -> ProseQualityConfig:
    for config in (QUALITY_V2, QUALITY_PRO_DIAGNOSTIC):
        if config.config_id == config_id:
            return config
    raise ValueError("prose_quality_config_not_frozen")
