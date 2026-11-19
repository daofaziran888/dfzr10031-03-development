"""维度归属映射与地区收入汇总的业务规则测试。

覆盖任务要求的口径：
- 成员在区间交界当天最多归属一个上级；
- 无法归属的订单单独计入待核对金额，地区汇总与待核对能对回输入；
- 两份更正对同一成员同一天给出不同上级时登记冲突，下游能看到影响；
- 旧报表可重现，任一次汇总只采用同一份映射结果；
- 更正生效后重算同一业务日，复核版本一致性并回查订单数据源批次。
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import SEED_ROWS
from app.store import store

client = TestClient(app)

BIZ_DATE = "2026-09-10"


@pytest.fixture(autouse=True)
def reset_store():
    """每个用例都从示例数据重新出发，避免用例间互相污染。"""
    store._tables = copy.deepcopy(SEED_ROWS)
    yield


def _merge() -> dict:
    response = client.post("/api/dim_mapping/merge")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"], payload["message"]
    return payload["entry"]


def _summarize(version: str | None = None) -> dict:
    values = {"业务日期": BIZ_DATE}
    if version:
        values["映射版本号"] = version
    response = client.post("/api/data_mart/region_summary", json={"values": values})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"], payload["message"]
    return payload["entry"]


# ---------- 接收与归并 ----------

def test_receive_relation_validation():
    # 缺必填字段
    response = client.post("/api/dim_mapping/relations", json={"values": {"成员编码": "S05"}})
    assert response.status_code == 200
    payload = response.json()
    assert not payload["ok"]
    assert "更正批次号" in payload["message"]
    # 生效日期晚于失效日期
    response = client.post("/api/dim_mapping/relations", json={"values": {
        "更正批次号": "FIX-X", "成员编码": "S05", "上级编码": "R-EAST",
        "生效日期": "2026-09-20", "失效日期": "2026-09-10",
    }})
    assert not response.json()["ok"]
    # 正常接收
    response = client.post("/api/dim_mapping/relations", json={"values": {
        "更正批次号": "FIX-20260919-01", "成员编码": "S05", "成员名称": "门店-深圳南山",
        "上级编码": "R-SOUTH", "上级名称": "华南",
        "生效日期": "2026-09-15", "失效日期": "2026-09-30",
    }})
    payload = response.json()
    assert payload["ok"]
    assert payload["entry"]["status"] == "待归并"


def test_merge_without_pending_relations_fails():
    _merge()
    response = client.post("/api/dim_mapping/merge")
    assert not response.json()["ok"]
    assert "没有待归并" in response.json()["message"]


def test_merge_detects_conflict_and_keeps_history():
    result = _merge()
    assert result["版本号"] == "V2"
    assert result["上一版本"] == "V1"
    assert result["冲突数"] == 1

    conflicts = client.get("/api/dim_mapping/conflicts").json()["items"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["成员编码"] == "S03"
    assert conflict["冲突日期"] == BIZ_DATE
    assert "R-EAST" in conflict["上级甲"] and "R-SOUTH" in conflict["上级乙"]
    assert "R-SOUTH" in conflict["采用上级"]  # 后接收的更正生效
    assert conflict["涉及订单批次"] == "BATCH-20260910"
    assert "SUM-20260910-01" in conflict["涉及汇总批次"]  # 涉及已经汇总过的订单
    assert conflict["影响金额"] == 1500.0

    # 旧版本整体转历史，但完整保留供重现
    versions = {v["版本号"]: v for v in client.get("/api/dim_mapping/versions").json()["items"]}
    assert versions["V1"]["状态"] == "历史版本"
    assert versions["V2"]["状态"] == "生效中"
    assert versions["V2"]["行数"] == 5


# ---------- 区间交界唯一 ----------

def test_boundary_day_has_single_parent():
    _merge()
    # 交界前一天归华北，交界当天起归华南，各只有一条命中
    day_before = client.get("/api/dim_mapping/lookup", params={"member": "S03", "date": "2026-09-09"}).json()
    assert day_before["ok"] and day_before["item"]["上级编码"] == "R-NORTH"
    boundary = client.get("/api/dim_mapping/lookup", params={"member": "S03", "date": BIZ_DATE}).json()
    assert boundary["ok"] and boundary["item"]["上级编码"] == "R-SOUTH"

    # 全量校验：生效版本里任一成员的区间两两不相交
    rows = client.get("/api/dim_mapping", params={"status": "生效中", "size": 200}).json()["items"]
    by_member: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        by_member.setdefault(row["成员编码"], []).append((row["起始日期"], row["截止日期"]))
    for member, spans in by_member.items():
        spans.sort()
        for prev, cur in zip(spans, spans[1:]):
            assert prev[1] < cur[0], f"成员 {member} 区间重叠：{prev} vs {cur}"


def test_lookup_unmapped_member_and_old_version():
    # 未映射成员查不到归属
    missing = client.get("/api/dim_mapping/lookup", params={"member": "S09", "date": BIZ_DATE}).json()
    assert not missing["ok"] and missing["归属状态"] == "未归属"
    _merge()
    # 指定历史版本可重现当时的归属
    old = client.get("/api/dim_mapping/lookup", params={"member": "S03", "date": BIZ_DATE, "version": "V1"}).json()
    assert old["ok"] and old["item"]["上级编码"] == "R-NORTH"


# ---------- 地区收入汇总 ----------

def test_summary_reconciles_and_pending_isolated():
    _merge()
    run = _summarize()
    assert run["映射版本号"] == "V2"
    regions = {row["地区编码"]: row["地区收入"] for row in run["地区明细"]}
    assert regions == {"R-EAST": 3000.0, "R-SOUTH": 2200.0}
    # 无法归属的订单单独计入待核对金额
    assert run["待核对金额"] == 800.0
    assert run["待核对订单"] == "ORD-9005"
    # 地区汇总数据与待核对金额能对回输入
    assert run["地区收入合计"] + run["待核对金额"] == run["输入订单总额"] == 6000.0
    assert run["对账差额"] == 0.0


def test_summary_uses_single_mapping_version():
    _merge()
    run = _summarize()
    versions = {row["映射版本号"] for row in run["地区明细"]}
    assert versions == {run["映射版本号"]}  # 任一次汇总只采用同一份映射结果
    assert run["订单批次号"] == "BATCH-20260910"


def test_old_report_reproducible():
    _merge()
    replay = _summarize(version="V1")
    original = client.get("/api/data_mart/region_summary/1").json()
    assert replay["汇总批次号"] != original["汇总批次号"]  # 重算是新批次，不改写旧报表
    assert replay["地区明细"] == original["地区明细"]
    assert replay["待核对金额"] == original["待核对金额"] == 800.0
    assert replay["输入订单总额"] == original["输入订单总额"] == 6000.0
    # 旧报表本身保持原样
    again = client.get("/api/data_mart/region_summary/1").json()
    assert again["映射版本号"] == "V1"
    assert {r["地区编码"]: r["地区收入"] for r in again["地区明细"]} == {
        "R-EAST": 3000.0, "R-NORTH": 1500.0, "R-SOUTH": 700.0,
    }


def test_summary_guards():
    _merge()
    # 不存在的映射版本
    response = client.post("/api/data_mart/region_summary", json={"values": {"业务日期": BIZ_DATE, "映射版本号": "V9"}})
    assert not response.json()["ok"]
    # 没有订单的业务日
    response = client.post("/api/data_mart/region_summary", json={"values": {"业务日期": "2026-09-11"}})
    assert not response.json()["ok"]
    # 缺业务日期
    response = client.post("/api/data_mart/region_summary", json={"values": {}})
    assert not response.json()["ok"]


# ---------- 冲突影响与复核 ----------

def test_conflict_impact_visible_to_downstream():
    _merge()
    run = _summarize()
    response = client.get(f"/api/data_mart/region_summary/{run['id']}/impact", params={"baseline": 1})
    assert response.status_code == 200
    impact = response.json()["item"]
    assert impact["基准版本"] == "V1" and impact["对比版本"] == "V2"
    diffs = {row["地区编码"]: row["差额"] for row in impact["地区差异"]}
    assert diffs["R-NORTH"] == -1500.0  # 华北减少
    assert diffs["R-SOUTH"] == 1500.0   # 华南增加
    assert diffs["R-EAST"] == 0.0
    assert impact["待核对差异"] == 0.0
    assert len(impact["关联冲突"]) == 1
    assert impact["关联冲突"][0]["成员编码"] == "S03"


def test_impact_requires_same_business_date():
    _merge()
    run = _summarize()
    # 另一业务日的汇总批次
    store.rows("order_fact").append({
        "id": 99, "status": "已入仓", "pending": False, "abnormal": False,
        "订单批次号": "BATCH-20260911", "业务日期": "2026-09-11",
        "订单号": "ORD-9101", "成员编码": "S01", "订单金额": 100.0,
    })
    other = client.post("/api/data_mart/region_summary", json={"values": {"业务日期": "2026-09-11"}}).json()["entry"]
    response = client.get(f"/api/data_mart/region_summary/{run['id']}/impact", params={"baseline": other["id"]})
    assert response.status_code == 400


def test_review_checks_version_recon_and_order_batch():
    _merge()
    run = _summarize()
    response = client.post(f"/api/data_mart/region_summary/{run['id']}/review")
    payload = response.json()
    assert payload["ok"], payload["message"]
    result = payload["entry"]
    # 地区收入和待核对金额共享同一个归属版本
    assert result["归属版本一致"] and result["映射版本号"] == "V2"
    # 对回输入
    assert result["对账平衡"]
    # 回查订单数据源批次
    assert result["订单批次回查"]
    assert result["订单批次号"] == "BATCH-20260910"
    assert result["批次重算金额"] == 6000.0
    # 批次状态流转为已复核
    reviewed = client.get(f"/api/data_mart/region_summary/{run['id']}").json()
    assert reviewed["status"] == "已复核"
    assert "通过" in reviewed["复核结论"]


def test_full_correction_flow_end_to_end():
    """完整走一遍：更正接收 → 归并 → 重算 → 影响对比 → 复核 → 旧报表重现。"""
    # 1. 初始：V1 下 S03 归华北，已汇总 SUM-20260910-01
    before = client.get("/api/data_mart/region_summary/1").json()
    assert before["映射版本号"] == "V1"

    # 2. 再接收一份更正并归并
    response = client.post("/api/dim_mapping/relations", json={"values": {
        "更正批次号": "FIX-20260918-03", "成员编码": "S09", "成员名称": "门店-成都高新",
        "上级编码": "R-WEST", "上级名称": "西南",
        "生效日期": "2026-09-10", "失效日期": "2026-09-30",
    }})
    assert response.json()["ok"]
    result = _merge()
    assert result["版本号"] == "V2"

    # 3. 重算同一业务日：S03 改挂华南，S09 也有了归属，待核对清零
    run = _summarize()
    regions = {row["地区编码"]: row["地区收入"] for row in run["地区明细"]}
    assert regions["R-SOUTH"] == 2200.0
    assert regions["R-WEST"] == 800.0
    assert run["待核对金额"] == 0.0
    assert run["地区收入合计"] + run["待核对金额"] == run["输入订单总额"]

    # 4. 影响对比与复核
    impact = client.get(f"/api/data_mart/region_summary/{run['id']}/impact", params={"baseline": 1}).json()["item"]
    assert {row["地区编码"] for row in impact["地区差异"] if row["差额"] != 0} == {"R-NORTH", "R-SOUTH", "R-WEST"}
    review = client.post(f"/api/data_mart/region_summary/{run['id']}/review").json()
    assert review["ok"] and review["entry"]["归属版本一致"]

    # 5. 旧报表仍可重现
    replay = _summarize(version="V1")
    assert replay["待核对金额"] == 800.0
    assert {r["地区编码"]: r["地区收入"] for r in replay["地区明细"]} == {
        "R-EAST": 3000.0, "R-NORTH": 1500.0, "R-SOUTH": 700.0,
    }
