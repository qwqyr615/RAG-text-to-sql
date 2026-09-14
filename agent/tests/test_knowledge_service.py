"""``knowledge/knowledge_service`` 测试：业务口径到真实字段的映射。"""

from __future__ import annotations

import json
from typing import Any

from core.config import BASE_DIR
from knowledge.knowledge_base import (
    ANALYSIS_THEMES,
    BUSINESS_OBJECTS,
    BUSINESS_RULES,
)
from knowledge.knowledge_service import export_knowledge_json, resolve_knowledge

SCRATCH_DIR = BASE_DIR / "sessions"

ALL_COLUMNS = [
    "production_line",
    "machine_id",
    "product_type",
    "batch_id",
    "shift",
    "defect_rate",
    "first_pass_yield",
    "quality_score",
    "downtime_minutes",
    "fault_event_count",
    "production_volume",
    "machine_utilization",
]


def _metadata(columns: list[str]) -> dict[str, Any]:
    return {
        "tables": [
            {
                "table_name": "fact_production_record",
                "columns": [{"name": name} for name in columns],
            }
        ]
    }


def test_all_rules_map_when_columns_exist() -> None:
    knowledge = resolve_knowledge(_metadata(ALL_COLUMNS))
    rules = {rule["name"]: rule for rule in knowledge["rules"]}

    assert rules["缺陷率"]["mapped_field"] == "defect_rate"
    assert rules["良率"]["mapped_field"] == "first_pass_yield"
    assert rules["设备利用率"]["mapped_field"] == "machine_utilization"
    assert rules["缺陷率"]["mapped_table"] == "fact_production_record"
    assert "AVG(defect_rate)" in rules["缺陷率"]["resolved_calculation"]


def test_missing_metric_is_marked_unavailable() -> None:
    knowledge = resolve_knowledge(_metadata(["defect_rate"]))
    rules = {rule["name"]: rule for rule in knowledge["rules"]}

    assert rules["缺陷率"]["mapped_field"] == "defect_rate"
    assert rules["产量"]["mapped_field"] is None
    assert rules["产量"]["resolved_calculation"] == "当前数据源缺少该指标字段"


def test_rule_prefers_configured_table() -> None:
    """candidate_fields 命中多张表时，prefer_table 优先。"""
    metadata = {
        "tables": [
            {"table_name": "other_table", "columns": [{"name": "defect_rate"}]},
            {
                "table_name": "fact_production_record",
                "columns": [{"name": "defect_rate"}],
            },
        ]
    }
    knowledge = resolve_knowledge(metadata)
    defect_rule = next(r for r in knowledge["rules"] if r["name"] == "缺陷率")
    assert defect_rule["mapped_table"] == "fact_production_record"


def test_themes_keep_only_existing_tables() -> None:
    knowledge = resolve_knowledge(_metadata(ALL_COLUMNS))
    production = next(
        theme for theme in knowledge["themes"] if theme["code"] == "production_analysis"
    )
    inventory = next(
        theme for theme in knowledge["themes"] if theme["code"] == "inventory_analysis"
    )

    assert production["related_tables"] == ["fact_production_record"]
    assert inventory["related_tables"] == []
    assert len(knowledge["themes"]) == len(ANALYSIS_THEMES)


def test_objects_dropped_when_default_table_missing() -> None:
    knowledge = resolve_knowledge(
        {"tables": [{"table_name": "some_other_table", "columns": []}]}
    )
    assert knowledge["objects"] == []
    assert len(BUSINESS_OBJECTS) == 5


def test_every_rule_declares_candidate_fields_and_calculation() -> None:
    """配置自检：每条业务规则都要有候选字段与计算口径。"""
    for rule in BUSINESS_RULES:
        assert rule.get("candidate_fields"), rule["name"]
        assert "{field}" in rule.get("calculation", ""), rule["name"]
        assert rule.get("prefer_table"), rule["name"]


def test_export_knowledge_json_writes_file() -> None:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_DIR / "knowledge_unit_test.json"
    try:
        result = export_knowledge_json(path=path, metadata=_metadata(ALL_COLUMNS))
        assert result == path
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "1.0"
        assert len(payload["rules"]) == len(BUSINESS_RULES)
    finally:
        path.unlink(missing_ok=True)
