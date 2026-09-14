"""``metadata/mapping`` 的草稿生成、CLI 与字段改写测试。

**零外部依赖**：不连数据库、不联网、不调用大模型。三处替代：

1. 数据库用临时 SQLite 文件（``tests/conftest.py`` 的 ``sqlite_path`` fixture），
   里面建一张模拟客户 MES 的表：列名是缩写、没有注释、比率型单位 —— 正是
   ``mappings/mes_prod_log.mapping.yaml`` 描述的接入场景；
2. 大模型用 :class:`_StubLLM` 顶替（只暴露 ``.invoke(messages) -> obj.content``），
   于是「模型说的对」「模型胡说」「模型挂了」三条路径都能离线测到；
3. 数据集可选的 MySQL 只用于真实环境验收，测试里一律不碰。

最不能省的是 :func:`test_rewrite_all_occurrences_and_skip_literals`：它锁住的
``metadata/mapping/columns.py`` 掩码偏移回归。这个 bug 的症状是 SQL 在运行时
报 ``Unknown column 'produc'``，而单元测试若只断言「结果里有 line_cd」完全测不出来
—— 必须逐字符比对整条 SQL。
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from core.config import BASE_DIR
from metadata.inventory import discover_tables
from metadata.mapping import cli as mapping_cli
from metadata.mapping.columns import ColumnRewriter
from metadata.mapping.draft import (
    DRAFT_HEADER,
    build_draft_prompt,
    draft_mapping,
    merge_llm_into_profile,
    parse_llm_json,
    profile_table,
    render_draft_yaml,
    write_draft,
)
from metadata.mapping.schema import (
    MappingError,
    StandardFieldBinding,
    load_profile,
    validate_profile,
)
from metadata.standard_fields import (
    AMBIGUOUS,
    MATCHED,
    UNMAPPED,
    STANDARD_FIELDS,
    StandardField,
    canonical_names,
    match_columns,
)

#: 手写的目标格式样本：草稿渲染的键序、YAML 往返都以它为准。
TEMPLATE_MAPPING = BASE_DIR / "mappings" / "mes_prod_log.mapping.yaml"

#: 客户 MES 表：列名缩写、无注释、def_rate / util 是比例型（与标准口径差 100 倍）。
#: ``odd_metric`` 是客户特有列（无标准字段认领）。
#:
#: ``util`` 与 ``res_util`` 一起出现是刻意的：前者是**歧义列**（设备利用率与资源
#: 利用率都声明了它），后者带前缀、能唯一命中。测试同时覆盖「必须交人审」与
#: 「能自动草拟」两条路径。
_DDL = """
CREATE TABLE mes_prod_log (
    rec_no INTEGER PRIMARY KEY,
    mach_no TEXT,
    line_cd TEXT,
    sft TEXT,
    def_rate REAL,
    util REAL,
    res_util REAL,
    odd_metric REAL
)
"""

_MES_COLUMNS = [
    "rec_no",
    "mach_no",
    "line_cd",
    "sft",
    "def_rate",
    "util",
    "res_util",
    "odd_metric",
]

_INSERT = (
    "INSERT INTO mes_prod_log "
    "(rec_no, mach_no, line_cd, sft, def_rate, util, res_util, odd_metric) "
    "VALUES (:rec_no, :mach_no, :line_cd, :sft, :def_rate, :util, :res_util, :odd_metric)"
)

_ROWS = [
    {
        "rec_no": 1,
        "mach_no": "M01",
        "line_cd": "Line_A",
        "sft": "Night",
        "def_rate": 0.039,
        "util": 0.70,
        "res_util": 0.61,
        "odd_metric": 5.0,
    },
    {
        "rec_no": 2,
        "mach_no": "M02",
        "line_cd": "Line_A",
        "sft": "Morning",
        "def_rate": 0.05,
        "util": 0.80,
        "res_util": 0.72,
        "odd_metric": 7.0,
    },
    {
        "rec_no": 3,
        "mach_no": "M03",
        "line_cd": "Line_B",
        "sft": "Afternoon",
        "def_rate": 0.0999,
        "util": 0.90,
        "res_util": 0.83,
        "odd_metric": 9.0,
    },
]


@pytest.fixture()
def base_dir() -> Path:
    """临时目录，建在 ``data/`` 下（与 ``sqlite_path`` 同样的理由）。

    不用 pytest 的 ``tmp_path``：它在受限环境里需要枚举临时目录编号，会直接
    抛 ``PermissionError``。这里只做「建一个目录 + 用完删掉」。
    """
    directory = BASE_DIR / "data" / f"mapping_test_{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=True)
    yield directory
    # Windows 上 SQLite 的文件句柄可能比连接晚一步释放，整目录删除会偶发失败；
    # 先逐个删文件再删目录，尽量不在 data/ 里留垃圾。
    for leftover in list(directory.rglob("*")):
        if leftover.is_file():
            try:
                leftover.unlink()
            except OSError:
                pass
    for _ in range(3):
        try:
            directory.rmdir()
            break
        except OSError:
            try:
                shutil.rmtree(directory)
                break
            except OSError:
                continue


def _slashed(directory: Path, name: str) -> Path:
    """``data/`` 下的单文件 SQLite 路径（放在原子目录里，teardown 一次删干净）。"""
    path = directory / f"{name}.db"
    if path.exists():
        path.unlink()
    return path


class _StubLLM:
    """最小可用的聊天模型替身：只实现 ``invoke(messages) -> obj``。

    真实的 LangChain ChatModel 返回 ``AIMessage``，其 ``.content`` 是字符串；
    草稿模块只依赖这一个约定，所以桩对象也只需给 ``.content``。
    """

    def __init__(self, payload: str | Exception) -> None:
        self.payload = payload
        self.calls: list[object] = []

    def invoke(self, messages: object) -> object:
        self.calls.append(messages)
        if isinstance(self.payload, Exception):
            raise self.payload

        class _Message:
            content = self.payload

        return _Message()


@pytest.fixture()
def mes_engine(base_dir: Path) -> Engine:
    """临时 SQLite 文件库 + 一张模拟客户 MES 表（列名缩写、无注释）。

    必须 ``dispose``：Windows 上连接池持有文件句柄时，teardown 删不掉 ``.db``。
    """
    engine = create_engine(f"sqlite:///{_slashed(base_dir, 'mes').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text(_DDL))
        for row in _ROWS:
            conn.execute(text(_INSERT), row)
    yield engine
    engine.dispose()


@pytest.fixture()
def mes_profile(mes_engine: Engine):
    """纯规则草稿的 profile（``use_llm=False``）：后续多个测试共用的基线。"""
    return draft_mapping(
        mes_engine, ["mes_prod_log"], profile_name="mes_prod_log_draft", use_llm=False
    )


# ----------------------------------------------------------------------
# YAML 往返
# ----------------------------------------------------------------------
def test_render_draft_yaml_round_trips_through_load_profile(base_dir) -> None:
    """``render_draft_yaml`` 的产物必须能被 ``load_profile`` 原样读回。

    这是草稿存在的意义：它最终要变成生产用的 ``mapping.yaml``。若渲染出的键名、
    键序或类型与 schema 不符，草稿在人审之前就已经是一份废文件。
    """
    original = load_profile(TEMPLATE_MAPPING)
    text_yaml = render_draft_yaml(original)

    assert text_yaml.startswith(DRAFT_HEADER), "草稿首行必须是未审核标记"
    assert "profile: mes_prod_log" in text_yaml

    path = base_dir / "roundtrip.yaml"
    path.write_text(text_yaml, encoding="utf-8")
    reloaded = load_profile(path)

    assert reloaded.profile == original.profile
    assert reloaded.schema_version == original.schema_version
    assert reloaded.description == original.description
    assert reloaded.database == original.database
    assert reloaded.discovery == original.discovery
    assert reloaded.table_names == original.table_names

    # 逐列逐绑定比对：只要渲染时漏了一个键（例如 scale），这里就会炸
    for table_name in original.table_names:
        before = original.bindings_for_table(table_name)
        after = reloaded.bindings_for_table(table_name)
        assert set(before) == set(after)
        for field_name, binding in before.items():
            other = after[field_name]
            assert other.expression == binding.expression, field_name
            assert other.scale == binding.scale, field_name
            assert other.unit == binding.unit, field_name
            assert other.enum_values == binding.enum_values, field_name
            assert other.column == binding.column, field_name
            assert other.notes == binding.notes, field_name


def test_render_draft_yaml_keeps_template_key_order() -> None:
    """键序与手写样本一致：人审 diff 时才不会满屏「假变更」。"""
    profile = load_profile(TEMPLATE_MAPPING)
    rendered = render_draft_yaml(profile)

    body_start = rendered.index("schema_version:")
    body = rendered[body_start:]
    top_keys = [
        line.split(":")[0]
        for line in body.splitlines()
        if line and not line.startswith((" ", "-", "#"))
    ]
    assert top_keys == [
        "schema_version",
        "profile",
        "description",
        "database",
        "discovery",
        "relationships",
        "tables",
    ]

    # 绑定块的键序：name 必须第一，且空值键不得出现（与手写样本同形）
    first_binding = body.split("standard_fields:", 1)[1].split("- column:", 1)[0]
    first_keys = [
        line.strip().lstrip("- ").split(":")[0]
        for line in first_binding.splitlines()
        if line.strip()
    ]
    assert first_keys == ["name", "expression", "role"]
    # 未赋值的键（scale / unit / enum_values）不该出现
    assert "scale" not in first_keys
    assert "unit" not in first_keys
    assert "enum_values" not in first_keys

    # 有值的键必须齐全且顺序固定：换算列 def_rate 带 scale / unit / notes
    converted = body.split("column: def_rate", 1)[1].split("- column:", 1)[0]
    converted_keys = [
        line.strip().lstrip("- ").split(":")[0]
        for line in converted.splitlines()
        if line.strip()
    ]
    assert converted_keys == [
        "standard_fields",
        "name",
        "expression",
        "scale",
        "unit",
        "notes",
    ]


def test_write_draft_writes_file_and_appends_commented_header(base_dir) -> None:
    profile = load_profile(TEMPLATE_MAPPING)
    target = base_dir / "sub" / "draft.yaml"

    written = write_draft(profile, target, header="审核人：张三\n第二行说明")

    assert written == target
    content = target.read_text(encoding="utf-8")
    assert content.startswith(DRAFT_HEADER)
    assert "# 审核人：张三" in content
    assert "# 第二行说明" in content


# ----------------------------------------------------------------------
# parse_llm_json
# ----------------------------------------------------------------------
def test_parse_llm_json_strips_fences_and_prose() -> None:
    fenced = '好的，结果如下：\n```json\n{"tables": [{"name": "t"}]}\n```\n以上。'
    assert parse_llm_json(fenced) == {"tables": [{"name": "t"}]}

    plain_prose = '说明文字 {"tables": []} 结尾文字'
    assert parse_llm_json(plain_prose) == {"tables": []}


def test_parse_llm_json_handles_nested_and_braces_in_strings() -> None:
    """括号配平扫描必须跳过字符串内部的 ``{`` / ``}``，否则会提前截断。

    模型在 ``notes`` 里写 SQL（``def_rate * 100``）或写 ``{...}`` 都很常见。
    """
    payload = '{"tables": [{"columns": [{"notes": "写成 {a: 1} 这样"}]}]}'
    parsed = parse_llm_json(payload)
    assert parsed["tables"][0]["columns"][0]["notes"] == "写成 {a: 1} 这样"

    two_objects = '{"a": 1}\n{"b": 2}'
    assert parse_llm_json(two_objects) == {"a": 1}


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "   ",
        "完全不是 JSON 的一段话",
        '{"tables": [{"name": "t"',  # 被截断、括号不配平
        "{不是 json}",
    ],
)
def test_parse_llm_json_raises_mapping_error_on_garbage(garbage: str) -> None:
    """垃圾输入必须抛 ``MappingError``（带中文原因），不能静默返回空 dict。

    静默返回空 dict 的后果是「草稿看起来生成成功，但一列都没绑定」，人审时
    极难发现；报错反而让人立刻知道模型这一轮白跑了。
    """
    with pytest.raises(MappingError) as excinfo:
        parse_llm_json(garbage)
    assert str(excinfo.value).strip()


def test_parse_llm_json_rejects_non_object_top_level() -> None:
    with pytest.raises(MappingError):
        parse_llm_json("[1, 2, 3]")


# ----------------------------------------------------------------------
# 规则匹配
# ----------------------------------------------------------------------
def test_match_columns_marks_ambiguous_and_unmapped() -> None:
    """命中唯一即 ``matched``，无候选即 ``unmapped``，多候选即 ``ambiguous``。

    ``util`` 在本项目词典里是**真正的歧义列**：``machine_utilization`` 与
    ``resource_utilization`` 都声明了它作为候选名（客户库里一个裸 ``util`` 列
    完全可能指任何一个）。因此它必须报 ``ambiguous`` 交人审，而不是按声明顺序
    猜一个 —— 猜错的代价是某个业务指标静默算错，比多问一句贵得多。
    """
    results = {
        item.column: item
        for item in match_columns(
            ["util", "line_cd", "odd_metric", "def_rate", "res_util"],
            table="mes_prod_log",
        )
    }

    assert results["util"].status == AMBIGUOUS
    assert results["util"].standard_field is None
    assert set(results["util"].candidates) == {
        "machine_utilization",
        "resource_utilization",
    }

    # 带前缀的写法没有歧义，应当唯一命中 —— 否则客户库就没法自动草拟了
    assert results["res_util"].status == MATCHED
    assert results["res_util"].standard_field == "resource_utilization"

    assert results["line_cd"].status == MATCHED
    assert results["line_cd"].standard_field == "production_line"

    assert results["def_rate"].status == MATCHED
    assert results["def_rate"].standard_field == "defect_rate"

    assert results["odd_metric"].status == UNMAPPED
    assert results["odd_metric"].standard_field is None
    assert results["odd_metric"].candidates == []


def test_match_columns_reports_ambiguity_when_two_fields_claim_one_name() -> None:
    """两个标准字段都声明同一列名 -> ``ambiguous``，给出候选而不是替人猜一个。"""
    fields = (
        StandardField(
            name="machine_utilization",
            label="设备利用率",
            unit="百分比",
            data_kind="measure",
            column_candidates=("util",),
        ),
        StandardField(
            name="resource_utilization",
            label="资源利用率",
            unit="百分比",
            data_kind="measure",
            column_candidates=("util",),
        ),
    )
    result = match_columns(["util"], table="t", fields=fields)[0]

    assert result.status == AMBIGUOUS
    assert result.standard_field is None
    assert set(result.candidates) == {"machine_utilization", "resource_utilization"}

    # 未被任何字段声明的列仍是 unmapped
    assert match_columns(["whatever"], fields=fields)[0].status == UNMAPPED


def test_standard_field_count_is_45() -> None:
    """词典是接入契约的一半，数量变了必须显式改测试而不是悄悄漂移。"""
    assert len(STANDARD_FIELDS) == 45
    assert len(canonical_names()) == 45


# ----------------------------------------------------------------------
# 取值画像
# ----------------------------------------------------------------------
def test_profile_table_collects_structure_and_values(mes_engine: Engine) -> None:
    profiles = {item.column: item for item in profile_table(mes_engine, "mes_prod_log")}

    assert set(profiles) == {
        "rec_no",
        "mach_no",
        "line_cd",
        "sft",
        "def_rate",
        "util",
        "res_util",
        "odd_metric",
    }

    rec_no = profiles["rec_no"]
    assert rec_no.is_primary_key is True
    assert rec_no.numeric is True
    assert rec_no.min_value == 1
    assert rec_no.max_value == 3
    assert rec_no.distinct_count == 3

    defect = profiles["def_rate"]
    assert defect.numeric is True
    assert defect.distinct_count == 3
    assert [round(value, 4) for value in defect.sample_values] == [0.039, 0.05, 0.0999]
    # 比率型取值：范围上界远小于 1，这正是 review 提示「可能漏了 ×100」的依据
    assert round(defect.max_value, 4) == 0.0999

    shift = profiles["sft"]
    assert shift.numeric is False
    assert shift.min_value is None
    assert shift.sample_values == ["Night", "Morning", "Afternoon"]

    assert profiles["line_cd"].sample_values == ["Line_A", "Line_B"]


def test_profile_table_never_raises_on_missing_table(base_dir: Path) -> None:
    """表不存在时显式报错，而不是安静地返回空画像骗调用方。"""
    engine = create_engine(f"sqlite:///{_slashed(base_dir, 'tiny').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (a TEXT, b TEXT)"))
        conn.execute(text("INSERT INTO t VALUES ('x', 'y')"))

    profiles = profile_table(engine, "t")
    assert [item.column for item in profiles] == ["a", "b"]
    assert profiles[0].sample_values == ["x"]

    with pytest.raises(Exception):
        profile_table(engine, "不存在的表")
    engine.dispose()


def test_profile_table_tolerates_per_column_failure(
    monkeypatch, mes_engine: Engine
) -> None:
    """单列查询失败（权限不足/类型不可比）不能拖垮整张表的画像。

    通过让 ``_quote`` 只对 ``sft`` 一列抛错来模拟：这一列的样例值、范围、去重数
    全都拿不到，但结构信息（类型、可空、主键）以及**其余六列**必须完好无损。
    草稿质量可以打折，接入流程不能因为一列而中断。
    """
    import metadata.mapping.draft as draft_module

    real_quote = draft_module._quote

    def flaky_quote(engine, name):  # noqa: ANN001
        if name == "sft":
            raise RuntimeError("模拟：该列没有查询权限")
        return real_quote(engine, name)

    monkeypatch.setattr(draft_module, "_quote", flaky_quote)
    profiles = {item.column: item for item in profile_table(mes_engine, "mes_prod_log")}

    assert set(profiles) == set(_MES_COLUMNS)

    broken = profiles["sft"]
    assert broken.sample_values == []
    assert broken.distinct_count is None
    assert broken.min_value is None
    assert broken.numeric is False
    assert broken.data_type  # 结构信息仍然拿得到

    healthy = profiles["def_rate"]
    assert healthy.numeric is True
    assert healthy.distinct_count == 3
    assert [round(value, 4) for value in healthy.sample_values] == [0.039, 0.05, 0.0999]
    assert profiles["rec_no"].is_primary_key is True


# ----------------------------------------------------------------------
# Prompt 构建
# ----------------------------------------------------------------------
def test_build_draft_prompt_mentions_vocabulary_and_json_shape(
    mes_engine: Engine,
) -> None:
    profiles = profile_table(mes_engine, "mes_prod_log")
    prompt = build_draft_prompt({"mes_prod_log": profiles}, database_type="sqlite")

    # 45 个标准字段一个不少（模型只能从词典里选，不能自造）
    for standard in STANDARD_FIELDS:
        assert standard.name in prompt

    # 画像信息要能看见：列名、样例值、取值范围
    assert "def_rate" in prompt
    assert "0.039" in prompt
    assert "'Night'" in prompt

    # 输出契约
    assert '"tables"' in prompt
    assert '"standard_fields"' in prompt
    assert "JSON" in prompt

    # 省略规则 + 单位换算的具体例子，缺了这两句模型就会硬塞/漏乘 100
    assert "省略" in prompt
    assert "def_rate * 100" in prompt
    assert "scale" in prompt and "expression" in prompt


def test_build_draft_prompt_survives_braces_in_customer_names() -> None:
    """客户列名/注释里的花括号不能让 Prompt 构建炸掉（故全程不用 ``str.format``）。"""
    from metadata.mapping.draft import ColumnProfile

    weird = ColumnProfile(
        table="t",
        column="{weird}col",
        data_type="TEXT",
        nullable=True,
        is_primary_key=False,
        comment="注释里有 {} 和 {name}",
        sample_values=["{a}"],
        distinct_count=1,
    )
    prompt = build_draft_prompt({"t": [weird]})
    assert "{weird}col" in prompt
    assert "{name}" in prompt


# ----------------------------------------------------------------------
# 规则草稿（零 LLM）
# ----------------------------------------------------------------------
def test_rule_based_draft_produces_valid_profile(mes_profile) -> None:
    result = mes_profile

    assert result.llm_used is False
    assert result.llm_error == ""
    assert result.raw_llm_output == ""
    assert [table.name for table in result.profile.tables] == ["mes_prod_log"]

    assert {item.column for item in result.matched} == {
        "rec_no",
        "mach_no",
        "line_cd",
        "sft",
        "def_rate",
        "res_util",
    }
    # util 是歧义列（设备利用率 / 资源利用率都认领），必须交人审而不是替人猜
    assert [item.column for item in result.ambiguous] == ["util"]
    assert set(result.ambiguous[0].candidates) == {
        "machine_utilization",
        "resource_utilization",
    }
    assert [item.column for item in result.unmapped] == ["odd_metric"]

    assert not result.has_errors
    # 歧义列不进草稿，因此这里不需要为它做取舍；映射本身必须是自洽的
    assert validate_profile(
        result.profile, live_columns={"mes_prod_log": _MES_COLUMNS}
    ) == []

    bindings = result.profile.bindings_for_table("mes_prod_log")
    assert bindings["record_id"].column == "rec_no"
    assert bindings["production_line"].expression == "line_cd"
    assert bindings["resource_utilization"].expression == "res_util"
    # 规则看不到取值，因此**不**做单位换算：这是刻意的，交给模型或人
    assert bindings["defect_rate"].scale is None
    assert bindings["defect_rate"].expression == "def_rate"
    # 歧义列没被草稿采纳 -> 不应对应任何映射
    assert "machine_utilization" not in bindings
    # 客户特有列不进映射（它本来就没有标准口径）
    assert all(
        binding.column != "odd_metric"
        for table in result.profile.tables
        for binding in table.bindings()
    )

    # 规则草稿同样要能渲染成可加载的 YAML
    yaml_text = render_draft_yaml(result.profile)
    assert yaml_text.startswith(DRAFT_HEADER)
    assert "column: odd_metric" in yaml_text


def test_draft_no_llm_never_touches_the_model(mes_engine: Engine, monkeypatch) -> None:
    """``use_llm=False`` 必须完全不碰模型：无 Key、离线、CI 都靠这条路径。"""
    import core.llm as llm_module

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003, ANN401
        raise AssertionError("use_llm=False 时不应创建或调用大模型")

    monkeypatch.setattr(llm_module, "get_llm_by_provider", explode)

    result = draft_mapping(
        mes_engine, ["mes_prod_log"], profile_name="p", use_llm=False
    )
    assert result.llm_used is False


def test_draft_with_stub_llm_merges_unit_conversion(
    mes_engine: Engine, base_dir: Path
) -> None:
    """桩模型给出 ×100 换算，草稿必须采纳（规则做不到这件事，它看不到取值）。"""
    payload = json.dumps(
        {
            "tables": [
                {
                    "name": "mes_prod_log",
                    "role": "fact",
                    "description": "客户 MES 生产日志",
                    "grain": "一行 = 一次生产运行",
                    "primary_key": "rec_no",
                    "columns": [
                        {
                            "column": "util",
                            "standard_fields": [
                                {
                                    "name": "machine_utilization",
                                    "unit": "比例(0-1)",
                                    "scale": 100,
                                    "expression": "util * 100",
                                    "notes": "客户列存 0.70，标准口径为 70",
                                }
                            ],
                        },
                        {
                            "column": "def_rate",
                            "standard_fields": [
                                {
                                    "name": "defect_rate",
                                    "unit": "比例(0-1)",
                                    "scale": 100,
                                    "expression": "def_rate * 100",
                                }
                            ],
                        },
                        {
                            "column": "line_cd",
                            "standard_fields": [{"name": "不存在的标准字段"}],
                        },
                        {
                            "column": "不存在的列",
                            "standard_fields": [{"name": "shift"}],
                        },
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )
    stub = _StubLLM(f"```json\n{payload}\n```")

    result = draft_mapping(
        mes_engine, ["mes_prod_log"], profile_name="p", use_llm=True, llm=stub
    )

    assert result.llm_used is True
    assert result.llm_error == ""
    assert result.raw_llm_output.startswith("```json")
    assert len(stub.calls) == 1  # 只调一次，不做重试放大成本

    bindings = result.profile.bindings_for_table("mes_prod_log")
    assert bindings["defect_rate"].expression == "def_rate * 100"
    assert bindings["defect_rate"].scale == 100
    # 规则侧已有的单位信息（来自标准字段单位）不被模型覆盖
    assert bindings["defect_rate"].unit == "百分比"
    assert bindings["machine_utilization"].expression == "util * 100"
    # 规则侧的表级语义不被模型覆盖
    assert result.profile.table("mes_prod_log").description == "自动草拟：待补充业务描述"
    assert not result.has_errors

    # 模型提议里被丢弃的部分必须出现在 issues 里（可审计），而不是静默消失
    joined = " ".join(issue.message for issue in result.issues)
    assert "不在词典中" in joined
    assert not result.has_errors

    path = write_draft(result.profile, base_dir / "llm_draft.yaml")
    reloaded = load_profile(path)
    assert reloaded.bindings_for_table("mes_prod_log")["defect_rate"].scale == 100


def test_draft_falls_back_when_llm_raises(mes_engine: Engine) -> None:
    """模型挂了要退回规则草稿，而不是让整个接入流程失败。"""
    stub = _StubLLM(RuntimeError("模拟：连接超时"))

    result = draft_mapping(
        mes_engine, ["mes_prod_log"], profile_name="p", use_llm=True, llm=stub
    )

    assert result.llm_used is False
    assert "连接超时" in result.llm_error
    assert result.raw_llm_output == ""
    # 兜底草稿依然可用
    assert result.profile.table_names == ["mes_prod_log"]
    assert not result.has_errors


def test_draft_falls_back_when_llm_returns_garbage(mes_engine: Engine) -> None:
    stub = _StubLLM("我不知道该怎么映射，建议人工看看。")

    result = draft_mapping(
        mes_engine, ["mes_prod_log"], profile_name="p", use_llm=True, llm=stub
    )

    assert result.llm_used is False
    assert result.llm_error
    assert result.profile.bindings_for_table("mes_prod_log")


def test_draft_rejects_unknown_table(mes_engine: Engine) -> None:
    with pytest.raises(MappingError) as excinfo:
        draft_mapping(mes_engine, ["没有这张表"], profile_name="p", use_llm=False)
    assert "没有这张表" in str(excinfo.value)


# ----------------------------------------------------------------------
# merge_llm_into_profile
# ----------------------------------------------------------------------
def test_merge_drops_bindings_outside_the_vocabulary(mes_profile) -> None:
    """词典外的标准字段必须被丢弃并记录 —— 放进 YAML 会直接让 validate 失败。"""
    payload = {
        "tables": [
            {
                "name": "mes_prod_log",
                "columns": [
                    {
                        "column": "odd_metric",
                        "standard_fields": [
                            {"name": "some_customer_specific_metric", "scale": 1}
                        ],
                    },
                    {
                        "column": "util",
                        "standard_fields": [
                            {"name": "machine_utilization", "scale": 100}
                        ],
                    },
                ],
            }
        ]
    }

    merge_issues = []
    merged = merge_llm_into_profile(mes_profile.profile, payload, issues=merge_issues)
    bindings = merged.bindings_for_table("mes_prod_log")

    assert "some_customer_specific_metric" not in bindings
    assert all(
        binding.standard_field in set(canonical_names())
        for table in merged.tables
        for binding in table.bindings()
    )
    # 丢弃的原因必须被记录（人审要看的是「模型想干什么、为什么被否」）
    dropped = [
        issue
        for issue in merge_issues
        if "some_customer_specific_metric" in issue.message
    ]
    assert dropped, [issue.render() for issue in merge_issues]
    assert dropped[0].where == "mes_prod_log.odd_metric"
    assert dropped[0].level == "warning"

    # 合法的那条被采纳（规则侧本来就把 util 认成 machine_utilization，这里补上换算）
    assert bindings["machine_utilization"].scale == 100
    assert bindings["machine_utilization"].expression == "util * 100"
    assert validate_profile(merged) == []


def test_merge_keeps_rule_match_and_records_conflict(mes_profile) -> None:
    """规则唯一命中优先：模型想把 line_cd 认成别的东西时只记录，不采纳。

    这里把规则草稿改造成「line_cd 已被规则绑定到 production_line」，
    再让模型提议 machine_id：规则那一条必须留下。
    """
    base = mes_profile.profile
    for column in base.table("mes_prod_log").columns:
        if column.column == "line_cd":
            column.bindings = [
                StandardFieldBinding(
                    standard_field="production_line",
                    expression="line_cd",
                    column="line_cd",
                )
            ]

    payload = {
        "tables": [
            {
                "name": "mes_prod_log",
                "columns": [
                    {
                        "column": "line_cd",
                        "standard_fields": [{"name": "machine_id"}],
                    }
                ],
            }
        ]
    }

    merged = merge_llm_into_profile(base, payload)
    bindings = merged.bindings_for_table("mes_prod_log")

    assert bindings["production_line"].column == "line_cd"
    assert "machine_id" not in {
        binding.standard_field
        for column in merged.table("mes_prod_log").columns
        if column.column == "line_cd"
        for binding in column.bindings
    }


def test_merge_rejects_phantom_columns_and_protects_role(mes_profile) -> None:
    """模型不能凭空增加列；表级 role 由发现模式/人决定，模型改了也不算。"""
    base = mes_profile.profile
    payload = {
        "tables": [
            {
                "name": "mes_prod_log",
                "role": "dimension",
                "columns": [
                    {"column": "没这列", "standard_fields": [{"name": "shift"}]},
                    {"column": "mach_no", "standard_fields": [{"name": "machine_id"}]},
                ],
            }
        ]
    }

    merge_issues = []
    merged = merge_llm_into_profile(base, payload, issues=merge_issues)

    columns = {column.column for column in merged.table("mes_prod_log").columns}
    assert "没这列" not in columns
    assert merged.table("mes_prod_log").role == base.table("mes_prod_log").role
    assert any(
        "请人工确认" in issue.message or "没有的列" in issue.message
        for issue in merge_issues
    )


def test_merge_preserves_discovery_and_rejects_phantom_columns(mes_profile) -> None:
    """模型无权改 discovery，也不能凭空增加列（列名以扫库结果为准）。"""
    base = mes_profile.profile
    base.discovery = {"exclude": ["intelligent_production_iiot"]}

    payload = {
        "tables": [
            {
                "name": "mes_prod_log",
                "role": "dimension",
                "columns": [
                    {"column": "没这列", "standard_fields": [{"name": "shift"}]},
                    {
                        "column": "util",
                        "standard_fields": [{"name": "resource_utilization"}],
                    },
                ],
            }
        ]
    }

    merged = merge_llm_into_profile(base, payload)

    assert merged.discovery == {"exclude": ["intelligent_production_iiot"]}
    columns = {column.column for column in merged.table("mes_prod_log").columns}
    assert "没这列" not in columns
    # 表级 role 由发现模式/人决定，模型改了也不算
    assert merged.table("mes_prod_log").role == base.table("mes_prod_log").role


def test_merge_does_not_mutate_the_base_profile(mes_profile) -> None:
    """merge 必须返回新对象：草稿要能反复叠加不同模型输出做对比。"""
    before = len(mes_profile.profile.bindings_for_table("mes_prod_log"))
    merge_llm_into_profile(
        mes_profile.profile,
        {
            "tables": [
                {
                    "name": "mes_prod_log",
                    "columns": [
                        {
                            "column": "util",
                            "standard_fields": [{"name": "machine_utilization"}],
                        }
                    ],
                }
            ]
        },
    )
    assert len(mes_profile.profile.bindings_for_table("mes_prod_log")) == before


# ----------------------------------------------------------------------
# 回归：SQL 标识符改写（掩码偏移）
# ----------------------------------------------------------------------
def _write_rewriter() -> ColumnRewriter:
    return ColumnRewriter(
        {"defect_rate": "def_rate * 100", "production_line": "line_cd"},
        reverse={"def_rate": ["defect_rate"], "line_cd": ["production_line"]},
    )


def test_rewrite_all_occurrences_and_skip_literals() -> None:
    """锁死掩码偏移回归：每一处出现都要改，字面量不能碰，整条 SQL 逐字符比对。

    历史 bug：掩码由 ``tools.sql_guard.strip_sql_literals_and_comments`` 提供，而
    它会把 ``'Product_A'`` 压成 ``''``，掩码长度比原文短。于是 ``'Product_A'`` 之后
    的所有下标全部错位，``GROUP BY production_line`` 被改成 ``GROUP BY produc`` +
    残留 ``tion_line``，运行时报 ``Unknown column 'produc'``。

    所以这里**断言完整字符串**，而不是「结果里包含 line_cd」——
    后者对 ``produc`` + ``tion_line`` 这种残骸也会通过。
    """
    sql = (
        "SELECT AVG(defect_rate) FROM mes_prod_log "
        "WHERE shift = 'Night' GROUP BY production_line ORDER BY production_line"
    )

    result = _write_rewriter().rewrite(sql)

    assert result.sql == (
        "SELECT AVG(def_rate * 100) FROM mes_prod_log "
        "WHERE shift = 'Night' GROUP BY line_cd ORDER BY line_cd"
    )
    assert "produc" not in result.sql
    assert "tion_line" not in result.sql
    assert result.applied == {
        "defect_rate": "def_rate * 100",
        "production_line": "line_cd",
    }


def test_rewrite_skips_literals_and_comments() -> None:
    """字面量与注释内部的同名文本不能被改写。"""
    sql = (
        "SELECT defect_rate FROM t "
        "WHERE note = 'production_line' "
        "AND tag = \"defect_rate\" "
        "-- production_line 在注释里\n"
        "AND other = 1"
    )

    result = _write_rewriter().rewrite(sql)

    assert "SELECT def_rate * 100 FROM t" in result.sql
    # 字面量原样保留
    assert "note = 'production_line'" in result.sql
    assert 'tag = "defect_rate"' in result.sql
    # 注释原样保留
    assert "-- production_line 在注释里" in result.sql
    assert len(result.sql) == len(sql) - len("defect_rate") + len("def_rate * 100")


def test_rewrite_respects_word_boundaries() -> None:
    """子串不能误伤：``avg_defect_rate`` / ``production_line_id`` 必须原样保留。

    注意最后两个 token 是**标准字段名**（``defect_rate`` / ``production_line``），
    它们被替换成客户侧表达式（``def_rate * 100`` / ``line_cd``）；前面的长列名
    含有同样子串但属于不同标识符，只能原样留下。
    """
    sql = (
        "SELECT avg_defect_rate, production_line_id, defect_rate, production_line "
        "FROM mes_prod_log"
    )

    result = _write_rewriter().rewrite(sql)

    assert "avg_defect_rate" in result.sql
    assert "production_line_id" in result.sql
    assert "avg_def_rate" not in result.sql
    assert result.sql.endswith("def_rate * 100, line_cd FROM mes_prod_log")
    assert result.applied == {
        "defect_rate": "def_rate * 100",
        "production_line": "line_cd",
    }


def test_rewrite_matches_customer_column_names_case_insensitively() -> None:
    """客户侧命名风格不统一时（示例库里混着大写），整词匹配必须大小写不敏感。"""
    sql = "SELECT SUM(DEFECT_RATE) FROM t GROUP BY PRODUCTION_LINE"

    result = _write_rewriter().rewrite(sql)

    assert result.sql == "SELECT SUM(def_rate * 100) FROM t GROUP BY line_cd"
    assert set(result.applied) == {"DEFECT_RATE", "PRODUCTION_LINE"}


def test_rewrite_is_idempotent_when_no_rule_matches() -> None:
    sql = "SELECT odd_metric FROM mes_prod_log WHERE sft = 'Night'"
    result = _write_rewriter().rewrite(sql)
    assert result.sql == sql
    assert result.changed is False


# ----------------------------------------------------------------------
# 发现模式
# ----------------------------------------------------------------------
def test_discover_tables_excludes_builtin_system_prefixes(base_dir: Path) -> None:
    """系统表/元数据表必须被挡掉：把它们暴露给模型是负收益（还会诱发臆造查询）。"""
    engine = create_engine(f"sqlite:///{_slashed(base_dir, 'sys').as_posix()}")
    with engine.begin() as conn:
        # sqlite_sequence 由 AUTOINCREMENT 自动创建，是真实存在的系统表
        conn.execute(
            text("CREATE TABLE business (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
        )
        conn.execute(text("CREATE TABLE _prisma_migrations (id TEXT)"))
        conn.execute(text("CREATE TABLE django_migrations (id TEXT)"))
        conn.execute(text("CREATE TABLE alembic_version (v TEXT)"))
        conn.execute(text("CREATE TABLE schema_migrations (v TEXT)"))
        conn.execute(text("CREATE TABLE mes_prod_log (rec_no INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE fact_production_record (record_id INTEGER)"))

    inventory = discover_tables(engine)

    assert "mes_prod_log" in inventory.business_tables
    assert "fact_production_record" in inventory.business_tables
    for excluded in (
        "_prisma_migrations",
        "django_migrations",
        "alembic_version",
        "schema_migrations",
    ):
        assert excluded not in inventory.business_tables
        assert excluded in inventory.excluded
        assert inventory.excluded[excluded]

    # sqlite_sequence 是 SQLite 自己建的，命中 sqlite_ 前缀
    if "sqlite_sequence" in inventory.all_tables:
        assert "sqlite_sequence" not in inventory.business_tables

    assert inventory.columns["mes_prod_log"] == ["rec_no"]
    assert inventory.primary_keys["mes_prod_log"] == ["rec_no"]
    engine.dispose()


def test_discover_tables_honours_profile_exclusions(base_dir: Path) -> None:
    """客户特有的排除项写在 mapping.yaml 里，而不是改代码。"""
    engine = create_engine(f"sqlite:///{_slashed(base_dir, 'cust').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE mes_prod_log (rec_no INTEGER)"))
        conn.execute(text("CREATE TABLE intelligent_production_iiot (rec_no INTEGER)"))

    profile = load_profile(TEMPLATE_MAPPING)
    inventory = discover_tables(engine, profile=profile)

    assert inventory.business_tables == ["mes_prod_log"]
    assert inventory.roles["mes_prod_log"] == "fact"
    assert "intelligent_production_iiot" in inventory.excluded
    engine.dispose()


def test_discover_tables_include_narrows_scope(base_dir: Path) -> None:
    engine = create_engine(f"sqlite:///{_slashed(base_dir, 'incl').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE a (x INTEGER)"))
        conn.execute(text("CREATE TABLE b (x INTEGER)"))

    inventory = discover_tables(engine, include=["a", "缺表"])
    assert inventory.business_tables == ["a"]
    assert "缺表" in inventory.excluded
    engine.dispose()


# ----------------------------------------------------------------------
# CLI（不连 MySQL：把 get_engine 换成临时 SQLite）
# ----------------------------------------------------------------------
def _run_cli(monkeypatch, mes_engine: Engine, argv: list[str]) -> int:
    monkeypatch.setattr(mapping_cli, "get_engine", lambda: mes_engine)
    return mapping_cli.main(argv)


def test_cli_discover_json(monkeypatch, mes_engine: Engine, capsys) -> None:
    code = _run_cli(monkeypatch, mes_engine, ["discover", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["database_type"] == "sqlite"
    assert payload["business_tables"] == ["mes_prod_log"]
    table = payload["tables"][0]
    assert table["name"] == "mes_prod_log"
    assert table["columns"] == 8
    assert table["visible"] is True


def test_cli_discover_text(monkeypatch, mes_engine: Engine, capsys) -> None:
    code = _run_cli(monkeypatch, mes_engine, ["discover"])
    out = capsys.readouterr().out
    assert code == 0
    assert "发现模式" in out
    assert "mes_prod_log" in out
    assert "可见业务表 1 张" in out


def test_cli_review_reports_conversions_and_coverage(
    monkeypatch, mes_engine: Engine, capsys, base_dir
) -> None:
    """review 的核心承诺：单位换算、未映射列、缺失字段都必须被人看见。"""
    profile = load_profile(TEMPLATE_MAPPING)
    path = base_dir / "draft.yaml"
    write_draft(profile, path)

    code = _run_cli(
        monkeypatch, mes_engine, ["review", "--mapping", str(path), "--against-db"]
    )
    out = capsys.readouterr().out

    assert "字段映射人审报告" in out
    # 覆盖率用的是真实库列数（本表只有 8 列，映射里声明了 45 列中的一部分）
    assert "[mes_prod_log]" in out
    assert "未映射列" in out
    # 单位换算逐条列出（模板里有 util / def_rate 两条）
    assert "util * 100" in out
    assert "def_rate * 100" in out
    assert "单位换算" in out
    # 本数据源缺失的标准字段清单（模板映射的列名与本测试表只有部分交集）
    assert "本数据源缺失的标准字段" in out
    assert "签字清单" in out


def test_cli_review_json(monkeypatch, mes_engine: Engine, capsys, base_dir) -> None:
    profile = load_profile(TEMPLATE_MAPPING)
    path = base_dir / "draft.yaml"
    write_draft(profile, path)

    code = _run_cli(
        monkeypatch,
        mes_engine,
        ["review", "--mapping", str(path), "--against-db", "--json"],
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1  # 映射声明的 45 列不在库里 -> validate 报 error
    assert payload["profile"] == "mes_prod_log"
    assert payload["summary"]["conversions"] == 2
    table = payload["tables"][0]
    assert table["name"] == "mes_prod_log"
    expression = {item["column"]: item["expression"] for item in table["conversions"]}
    assert expression == {"util": "util * 100", "def_rate": "def_rate * 100"}
    # 模板映射已经把 45 个标准字段全部绑定，所以「缺失」为空
    assert table["standard_fields_mapped"] == 45
    assert table["standard_fields_missing"] == []
    # 覆盖率按库里真实列算：8 列里有 7 列被映射声明
    # （rec_no / mach_no / line_cd / sft / def_rate / util / res_util），
    # 只有客户特有的 odd_metric 没有标准口径。
    assert table["columns"] == 8
    assert table["mapped_columns"] == 7
    assert table["unmapped_columns"] == ["odd_metric"]
    assert payload["summary"]["errors"] >= 1


def test_cli_review_flags_table_excluded_by_discovery(
    monkeypatch, capsys, base_dir
) -> None:
    """映射目标表被排除、或干脆不存在时必须报 error：映射写得再对也白搭。

    这里用一个**没有那张表**的库来构造「Agent 根本看不到它」的场景，
    对应 ``_against_db_issues`` 与 ``validate_profile(live_columns=...)`` 的两条检查。
    """
    engine = create_engine(f"sqlite:///{_slashed(base_dir, 'empty').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE unrelated (x INTEGER)"))

    path = base_dir / "ok.yaml"
    write_draft(load_profile(TEMPLATE_MAPPING), path)

    code = _run_cli(
        monkeypatch, engine, ["review", "--mapping", str(path), "--against-db"]
    )
    out = capsys.readouterr().out

    assert "字段映射人审报告" in out
    assert "在库里不存在" in out
    assert "[ERROR]" in out
    assert code == 1
    engine.dispose()


def test_cli_validate_against_db(
    monkeypatch, mes_engine: Engine, capsys, base_dir
) -> None:
    path = base_dir / "ok.yaml"
    write_draft(load_profile(TEMPLATE_MAPPING), path)

    code = _run_cli(
        monkeypatch, mes_engine, ["validate", "--mapping", str(path), "--against-db"]
    )
    out = capsys.readouterr().out
    assert "字段映射校验" in out
    # 映射里的 45 列在真实的 7 列 SQLite 表里不存在 -> 必须报错，退出码 1
    assert code == 1
    assert "在库里不存在" in out


def test_cli_validate_missing_file(capsys, base_dir) -> None:
    code = mapping_cli.main(["validate", "--mapping", str(base_dir / "不存在.yaml")])
    assert code == 1
    assert "加载失败" in capsys.readouterr().out


def test_cli_draft_no_llm_and_diff(
    monkeypatch, mes_engine: Engine, capsys, base_dir
) -> None:
    out_path = base_dir / "draft.yaml"
    code = _run_cli(
        monkeypatch,
        mes_engine,
        [
            "draft",
            "--table",
            "mes_prod_log",
            "--out",
            str(out_path),
            "--no-llm",
            "--profile",
            "p",
        ],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "自动草拟字段映射" in out
    assert "大模型    ：未使用" in out
    assert out_path.is_file()
    assert out_path.read_text(encoding="utf-8").startswith(DRAFT_HEADER)

    # 新草稿 vs 手写样本 -> diff 必须列出变更
    code = _run_cli(
        monkeypatch,
        mes_engine,
        ["diff", "--old", str(TEMPLATE_MAPPING), "--new", str(out_path)],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "字段映射差异" in out
    assert "mes_prod_log" in out


def test_cli_diff_identical_files(capsys) -> None:
    code = mapping_cli.main(
        ["diff", "--old", str(TEMPLATE_MAPPING), "--new", str(TEMPLATE_MAPPING)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "完全一致" in out


def test_cli_build_parser_requires_subcommand() -> None:
    parser = mapping_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
