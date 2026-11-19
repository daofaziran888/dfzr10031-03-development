"""数据集市业务规则：状态流转、字段校验与筛选口径都收在这里。

集市表登记之外，这里还负责地区收入汇总：
- 每次汇总固化一份映射版本（任一次汇总只采用同一份映射结果）；
- 按业务日把订单归属到地区后计算地区收入，无法归属的计入待核对金额；
- 地区收入合计 + 待核对金额必须能对回输入订单总额；
- 维度更正生效后重算同一业务日，复核版本一致性并回查订单数据源批次。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "data_mart"
SUMMARY_MODULE = "region_summary"
ORDER_MODULE = "order_fact"
MAPPING_MODULE = "dim_mapping"
CONFLICT_MODULE = "dim_mapping_conflict"
REQUIRED_FIELDS = ["表编号", "表名称", "主题域"]
STATUS_ORDER = ["草稿", "已建模", "已发布", "已下线"]
ACTION_RULES = {"提交建模": "已建模", "发布集市": "已发布", "下线集市": "已下线"}
NEGATIVE_ACTIONS = ["下线集市"]


def _parse_day(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        return None


def _round2(value: float) -> float:
    return round(value + 1e-9, 2)


class DataMartService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("表编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"集市表 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据集市可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"集市表已{action}"

    # ---------- 地区收入汇总 ----------

    def list_summary_runs(
        self,
        *,
        business_date: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(SUMMARY_MODULE)
        if business_date:
            rows = [row for row in rows if row.get("业务日期") == business_date]
        rows = sorted(rows, key=lambda row: str(row.get("汇总批次号", "")))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_summary_run(self, run_id: int) -> dict[str, Any] | None:
        return store.find(SUMMARY_MODULE, run_id)

    def _latest_mapping_version(self) -> str | None:
        versions = [
            int(str(row.get("版本号", "V0")).lstrip("V"))
            for row in store.rows(MAPPING_MODULE)
            if row.get("status") == "生效中"
        ]
        return f"V{max(versions)}" if versions else None

    def run_region_summary(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """按业务日汇总地区收入：一次汇总只采用同一份映射结果。

        无法归属的订单单独计入待核对金额；地区收入合计 + 待核对金额 = 输入订单总额。
        指定历史映射版本可以重现旧报表，不指定则采用当前生效版本。
        """
        day_text = str(values.get("业务日期") or "").strip()
        if not day_text:
            return None, "缺少必填字段：业务日期"
        day = _parse_day(day_text)
        if day is None:
            return None, f"业务日期「{day_text}」不是合法的 YYYY-MM-DD 日期"

        version = str(values.get("映射版本号") or "").strip()
        if not version:
            version = self._latest_mapping_version() or ""
        if not version:
            return None, "还没有生效中的映射版本，请先在维度归属映射里归并版本"
        version_rows = [row for row in store.rows(MAPPING_MODULE) if row.get("版本号") == version]
        if not version_rows:
            return None, f"映射版本 {version} 不存在，无法用于汇总"

        # 固化本次汇总的映射快照：整个运行期间只读这一份，保证口径一致。
        index: dict[str, list[tuple[date, date, dict[str, Any]]]] = {}
        for row in version_rows:
            start = _parse_day(row.get("起始日期"))
            end = _parse_day(row.get("截止日期"))
            if start is None or end is None:
                continue
            index.setdefault(str(row.get("成员编码")), []).append((start, end, row))

        orders = [order for order in store.rows(ORDER_MODULE) if order.get("业务日期") == day_text]
        if not orders:
            return None, f"业务日 {day_text} 没有订单数据，无法汇总地区收入"

        region_map: dict[str, dict[str, Any]] = {}
        pending_orders: list[dict[str, Any]] = []
        for order in orders:
            member = str(order.get("成员编码", ""))
            hits = [row for start, end, row in index.get(member, []) if start <= day <= end]
            if len(hits) > 1:
                return None, f"成员 {member} 在 {day_text} 命中多条归属，映射版本 {version} 违反交界唯一规则，已中止汇总"
            amount = float(order.get("订单金额", 0) or 0)
            if not hits:
                pending_orders.append(order)
                continue
            parent = hits[0]
            bucket = region_map.setdefault(str(parent.get("上级编码")), {
                "地区编码": parent.get("上级编码", ""),
                "地区名称": parent.get("上级名称", ""),
                "地区收入": 0.0,
                "订单笔数": 0,
            })
            bucket["地区收入"] = _round2(bucket["地区收入"] + amount)
            bucket["订单笔数"] += 1

        input_total = _round2(sum(float(order.get("订单金额", 0) or 0) for order in orders))
        region_total = _round2(sum(bucket["地区收入"] for bucket in region_map.values()))
        pending_total = _round2(sum(float(order.get("订单金额", 0) or 0) for order in pending_orders))
        diff = _round2(input_total - region_total - pending_total)

        runs = store.rows(SUMMARY_MODULE)
        seq = sum(1 for run in runs if run.get("业务日期") == day_text) + 1
        run = {
            "id": max((int(row.get("id", 0)) for row in runs), default=0) + 1,
            "status": "已汇总",
            "pending": True,
            "abnormal": False,
            "汇总批次号": f"SUM-{day_text.replace('-', '')}-{seq:02d}",
            "业务日期": day_text,
            "映射版本号": version,
            "订单批次号": "、".join(sorted({str(order.get("订单批次号", "")) for order in orders})),
            "输入订单总额": input_total,
            "地区收入合计": region_total,
            "待核对金额": pending_total,
            "对账差额": diff,
            "待核对订单": "、".join(str(order.get("订单号", "")) for order in pending_orders),
            "待核对笔数": len(pending_orders),
            "地区明细": [
                {**bucket, "映射版本号": version}
                for bucket in sorted(region_map.values(), key=lambda item: str(item["地区编码"]))
            ],
            "汇总时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "复核结论": "",
        }
        runs.append(run)
        return run, f"业务日 {day_text} 已按映射版本 {version} 完成地区收入汇总（{run['汇总批次号']}）"

    def summary_impact(self, run_id: int, baseline_id: int) -> tuple[dict[str, Any] | None, str]:
        """对比同一业务日两次汇总，让下游看到归属冲突对地区收入的影响。"""
        run = store.find(SUMMARY_MODULE, run_id)
        if run is None:
            return None, f"汇总批次 {run_id} 不存在"
        baseline = store.find(SUMMARY_MODULE, baseline_id)
        if baseline is None:
            return None, f"基准批次 {baseline_id} 不存在"
        if run.get("业务日期") != baseline.get("业务日期"):
            return None, "两次汇总的业务日期不同，无法对比归属冲突影响"

        base_rows = {str(row.get("地区编码")): row for row in baseline.get("地区明细", [])}
        run_rows = {str(row.get("地区编码")): row for row in run.get("地区明细", [])}
        diffs = []
        for code in sorted(set(base_rows) | set(run_rows)):
            before = float(base_rows.get(code, {}).get("地区收入", 0) or 0)
            after = float(run_rows.get(code, {}).get("地区收入", 0) or 0)
            diffs.append({
                "地区编码": code,
                "地区名称": (run_rows.get(code) or base_rows.get(code) or {}).get("地区名称", ""),
                "基准收入": before,
                "对比收入": after,
                "差额": _round2(after - before),
            })
        related = [
            conflict for conflict in store.rows(CONFLICT_MODULE)
            if str(baseline.get("汇总批次号", "")) in str(conflict.get("涉及汇总批次", ""))
        ]
        result = {
            "业务日期": run.get("业务日期"),
            "基准批次": baseline.get("汇总批次号"),
            "基准版本": baseline.get("映射版本号"),
            "对比批次": run.get("汇总批次号"),
            "对比版本": run.get("映射版本号"),
            "地区差异": diffs,
            "待核对差异": _round2(float(run.get("待核对金额", 0) or 0) - float(baseline.get("待核对金额", 0) or 0)),
            "关联冲突": related,
        }
        changed = [row for row in diffs if row["差额"] != 0]
        if changed:
            detail = "；".join(f"{row['地区名称'] or row['地区编码']} {row['差额']:+.2f}" for row in changed)
            message = f"映射版本 {result['基准版本']} → {result['对比版本']} 的地区收入影响：{detail}"
        else:
            message = "两次汇总的地区收入没有差异"
        return result, message

    def review_summary(self, run_id: int) -> tuple[dict[str, Any] | None, str]:
        """复核汇总批次：版本一致性、对回输入、回查订单数据源批次。"""
        run = store.find(SUMMARY_MODULE, run_id)
        if run is None:
            return None, f"汇总批次 {run_id} 不存在"

        version = str(run.get("映射版本号", ""))
        details = run.get("地区明细", [])
        # 1. 地区收入与待核对金额共享同一个归属版本：明细行版本必须等于批次版本，
        #    待核对金额与地区收入同属本批次，天然共用该版本。
        detail_versions = {str(row.get("映射版本号", "")) for row in details}
        version_ok = detail_versions <= {version} and bool(version)
        # 2. 对回输入：地区收入合计 + 待核对金额 = 输入订单总额，且明细合计等于批次合计。
        detail_sum = _round2(sum(float(row.get("地区收入", 0) or 0) for row in details))
        recon_ok = (
            _round2(float(run.get("地区收入合计", 0) or 0) + float(run.get("待核对金额", 0) or 0))
            == _round2(float(run.get("输入订单总额", 0) or 0))
            and detail_sum == _round2(float(run.get("地区收入合计", 0) or 0))
        )
        # 3. 回查订单数据源批次：按批次号与业务日重算订单总额，与输入比对。
        batches = [b for b in str(run.get("订单批次号", "")).split("、") if b]
        orders = [
            order for order in store.rows(ORDER_MODULE)
            if str(order.get("订单批次号", "")) in batches and order.get("业务日期") == run.get("业务日期")
        ]
        batch_total = _round2(sum(float(order.get("订单金额", 0) or 0) for order in orders))
        batch_ok = bool(batches) and batch_total == _round2(float(run.get("输入订单总额", 0) or 0))

        all_ok = version_ok and recon_ok and batch_ok
        conclusion = (
            f"归属版本一致性：{'通过' if version_ok else '不通过'}（批次 {version}，明细版本 {sorted(detail_versions) or ['无']}）；"
            f"对账：{'通过' if recon_ok else '不通过'}（地区收入 {run.get('地区收入合计')} + 待核对 {run.get('待核对金额')} "
            f"vs 输入 {run.get('输入订单总额')}）；"
            f"订单批次回查：{'通过' if batch_ok else '不通过'}（{run.get('订单批次号')} 重算 {batch_total}）"
        )
        run["复核结论"] = conclusion
        run["status"] = "已复核" if all_ok else "复核异常"
        run["pending"] = not all_ok
        run["abnormal"] = not all_ok
        result = {
            "汇总批次号": run.get("汇总批次号"),
            "业务日期": run.get("业务日期"),
            "映射版本号": version,
            "归属版本一致": version_ok,
            "对账平衡": recon_ok,
            "订单批次回查": batch_ok,
            "订单批次号": run.get("订单批次号"),
            "批次重算金额": batch_total,
            "复核结论": conclusion,
        }
        return result, f"汇总批次 {run.get('汇总批次号')} 复核{'通过' if all_ok else '未通过'}"
