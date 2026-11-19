"""维度归属映射业务规则：版本区间接收、按成员归并、冲突登记与按业务日查询归属。

维度管理只登记层级定义，成员在哪一天归属哪个上级由这里的映射表回答：
- 接收维度版本区间及成员关系数据（更正批次），先落在接收表里等待归并；
- 归并时按成员把关系压成带起止日期的区间，交界当天最多归属一个上级；
- 两份更正在同一成员同一天给出不同上级时登记冲突，后接收的更正生效；
- 每次归并产出一份完整的映射版本快照，旧版本保留，供历史报表重现。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

MODULE = "dim_mapping"
RELATION_MODULE = "dim_mapping_relation"
CONFLICT_MODULE = "dim_mapping_conflict"
ORDER_MODULE = "order_fact"
SUMMARY_MODULE = "region_summary"

DEFAULT_DIMENSION = "DIME-REGION"
RELATION_REQUIRED = ["更正批次号", "成员编码", "上级编码", "生效日期", "失效日期"]


def _parse_day(text: Any, field: str) -> date:
    """把 YYYY-MM-DD 文本解析成日期；格式不对时抛出带字段名的可读错误。"""
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        raise ValueError(f"{field}「{text}」不是合法的 YYYY-MM-DD 日期") from None


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


class DimMappingService:
    # ---------- 查询 ----------

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
            rows = [row for row in rows if keyword in str(row.get("成员编码", ""))]
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

    def list_versions(self) -> list[dict[str, Any]]:
        """按版本号汇总映射快照：一个版本一份完整成员区间。"""
        versions: dict[str, dict[str, Any]] = {}
        for row in store.rows(MODULE):
            ver = str(row.get("版本号", ""))
            item = versions.setdefault(ver, {
                "版本号": ver,
                "行数": 0,
                "成员数": set(),
                "状态": row.get("status", ""),
                "归并时间": row.get("归并时间", ""),
                "来源批次": set(),
            })
            item["行数"] += 1
            item["成员数"].add(row.get("成员编码", ""))
            item["来源批次"].add(row.get("来源批次", ""))
        result = []
        for item in versions.values():
            result.append({
                "版本号": item["版本号"],
                "行数": item["行数"],
                "成员数": len(item["成员数"]),
                "状态": item["状态"],
                "归并时间": item["归并时间"],
                "来源批次": "、".join(sorted(item["来源批次"])),
            })
        return sorted(result, key=lambda x: x["版本号"])

    def list_relations(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(RELATION_MODULE)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return sorted(rows, key=lambda row: int(row.get("接收序号", 0)))

    def list_conflicts(self) -> list[dict[str, Any]]:
        return store.rows(CONFLICT_MODULE)

    def current_version(self) -> str | None:
        """当前生效中的映射版本号；一个版本整体生效、整体失效。"""
        versions = [
            int(str(row.get("版本号", "V0")).lstrip("V"))
            for row in store.rows(MODULE)
            if row.get("status") == "生效中"
        ]
        return f"V{max(versions)}" if versions else None

    def lookup(self, member: str, day_text: str, version: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """按业务日查成员归属：交界当天最多命中一个上级，多了直接报错而不是任取一条。"""
        member = str(member or "").strip()
        if not member:
            return None, "缺少必填字段：成员编码"
        try:
            day = _parse_day(day_text, "业务日期")
        except ValueError as exc:
            return None, str(exc)
        rows = []
        for row in store.rows(MODULE):
            if row.get("成员编码") != member:
                continue
            if version and row.get("版本号") != version:
                continue
            if not version and row.get("status") != "生效中":
                continue
            start = _parse_day(row.get("起始日期"), "起始日期")
            end = _parse_day(row.get("截止日期"), "截止日期")
            if start <= day <= end:
                rows.append(row)
        if len(rows) > 1:
            return None, f"成员 {member} 在 {day_text} 命中 {len(rows)} 条归属，违反区间交界唯一规则"
        if not rows:
            ver_text = version or self.current_version() or "无"
            return None, f"成员 {member} 在 {day_text} 未归属任何上级（映射版本 {ver_text}）"
        row = rows[0]
        return {
            "成员编码": member,
            "成员名称": row.get("成员名称", ""),
            "业务日期": day_text,
            "版本号": row.get("版本号", ""),
            "上级编码": row.get("上级编码", ""),
            "上级名称": row.get("上级名称", ""),
            "起始日期": row.get("起始日期", ""),
            "截止日期": row.get("截止日期", ""),
        }, "ok"

    # ---------- 接收成员关系 ----------

    def receive_relation(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """接收一条维度版本区间及成员关系数据，落入接收表等待归并。"""
        missing = [field for field in RELATION_REQUIRED if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        try:
            start = _parse_day(values.get("生效日期"), "生效日期")
            end = _parse_day(values.get("失效日期"), "失效日期")
        except ValueError as exc:
            return None, [str(exc)]
        if start > end:
            return None, ["生效日期不能晚于失效日期"]
        rows = store.rows(RELATION_MODULE)
        entry = {
            "id": _next_id(rows),
            "status": "待归并",
            "pending": True,
            "abnormal": False,
            "更正批次号": str(values.get("更正批次号")).strip(),
            "维度编号": str(values.get("维度编号") or DEFAULT_DIMENSION).strip(),
            "成员编码": str(values.get("成员编码")).strip(),
            "成员名称": str(values.get("成员名称") or "").strip(),
            "上级编码": str(values.get("上级编码")).strip(),
            "上级名称": str(values.get("上级名称") or "").strip(),
            "生效日期": start.isoformat(),
            "失效日期": end.isoformat(),
            "接收序号": max((int(row.get("接收序号", 0)) for row in rows), default=0) + 1,
            "接收时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "归并版本号": "",
        }
        rows.append(entry)
        return entry, []

    # ---------- 归并 ----------

    def merge_versions(self) -> tuple[dict[str, Any] | None, str]:
        """把待归并的成员关系按成员压成区间，产出新的映射版本。

        归并规则：
        - 同一成员的关系按接收序号依次应用，新关系覆盖重叠区间；
        - 旧区间被截断或拆分，保证交界当天最多归属一个上级；
        - 同一成员同一天被两份更正指向不同上级时登记冲突，后接收的生效；
        - 旧版本整体转为历史版本，新版本是全体成员的完整快照。
        """
        relations = self.list_relations(status="待归并")
        if not relations:
            return None, "没有待归并的成员关系，无法归并新版本"

        # 当前生效区间按成员分组，作为归并基线。
        by_member: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows(MODULE):
            if row.get("status") != "生效中":
                continue
            by_member.setdefault(str(row.get("成员编码")), []).append({
                "start": _parse_day(row.get("起始日期"), "起始日期"),
                "end": _parse_day(row.get("截止日期"), "截止日期"),
                "parent": row.get("上级编码", ""),
                "parent_name": row.get("上级名称", ""),
                "member_name": row.get("成员名称", ""),
                "source": row.get("来源批次", ""),
            })

        pending_by_member: dict[str, list[dict[str, Any]]] = {}
        for rel in relations:
            pending_by_member.setdefault(str(rel.get("成员编码")), []).append(rel)

        conflicts: list[dict[str, Any]] = []
        conflict_relation_ids: set[int] = set()
        for member, rels in pending_by_member.items():
            # 冲突检测：两两比较待归并关系，区间重叠且上级不同即冲突。
            for i in range(len(rels)):
                for j in range(i + 1, len(rels)):
                    conflict = self._detect_conflict(member, rels[i], rels[j])
                    if conflict is not None:
                        conflicts.append(conflict)
                        conflict_relation_ids.add(int(rels[i]["id"]))
                        conflict_relation_ids.add(int(rels[j]["id"]))
            # 按接收顺序应用，后接收的更正覆盖先接收的重叠部分。
            intervals = by_member.get(member, [])
            for rel in rels:
                intervals = self._apply_relation(intervals, rel)
            by_member[member] = intervals

        old_version = self.current_version()
        new_version = f"V{int((old_version or 'V0').lstrip('V')) + 1}"
        merged_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 旧版本整体转历史，新版本写入完整快照。
        for row in store.rows(MODULE):
            if row.get("status") == "生效中":
                row["status"] = "历史版本"
        mapping_rows = store.rows(MODULE)
        next_id = _next_id(mapping_rows)
        row_count = 0
        for member, intervals in sorted(by_member.items()):
            for iv in intervals:
                mapping_rows.append({
                    "id": next_id,
                    "status": "生效中",
                    "pending": False,
                    "abnormal": False,
                    "版本号": new_version,
                    "维度编号": DEFAULT_DIMENSION,
                    "成员编码": member,
                    "成员名称": iv.get("member_name", ""),
                    "上级编码": iv["parent"],
                    "上级名称": iv.get("parent_name", ""),
                    "起始日期": iv["start"].isoformat(),
                    "截止日期": iv["end"].isoformat(),
                    "来源批次": iv["source"],
                    "归并时间": merged_at,
                })
                next_id += 1
                row_count += 1

        # 接收表更新状态：涉及冲突的标记为冲突已归并，其余正常归并。
        for rel in relations:
            rel["status"] = "冲突已归并" if int(rel["id"]) in conflict_relation_ids else "已归并"
            rel["pending"] = False
            rel["abnormal"] = int(rel["id"]) in conflict_relation_ids
            rel["归并版本号"] = new_version

        # 冲突登记：补充涉及订单批次、已汇总批次与影响金额后落表。
        conflict_rows = store.rows(CONFLICT_MODULE)
        conflict_id = _next_id(conflict_rows)
        for seq, conflict in enumerate(conflicts, start=1):
            conflict["id"] = conflict_id
            conflict["冲突编号"] = f"CONF-{new_version}-{seq:02d}"
            conflict_id += 1
            conflict_rows.append(conflict)

        message = f"已归并 {len(relations)} 条成员关系，生成映射版本 {new_version}（{row_count} 行）"
        if conflicts:
            message += f"，登记归属冲突 {len(conflicts)} 起"
        return {
            "版本号": new_version,
            "上一版本": old_version or "无",
            "归并关系数": len(relations),
            "映射行数": row_count,
            "冲突数": len(conflicts),
            "冲突列表": conflicts,
            "归并时间": merged_at,
        }, message

    def _detect_conflict(
        self, member: str, first: dict[str, Any], second: dict[str, Any]
    ) -> dict[str, Any] | None:
        """两份更正对同一成员在重叠区间给出不同上级时，生成冲突记录。"""
        if first.get("上级编码") == second.get("上级编码"):
            return None
        first_start = _parse_day(first.get("生效日期"), "生效日期")
        first_end = _parse_day(first.get("失效日期"), "失效日期")
        second_start = _parse_day(second.get("生效日期"), "生效日期")
        second_end = _parse_day(second.get("失效日期"), "失效日期")
        overlap_start = max(first_start, second_start)
        overlap_end = min(first_end, second_end)
        if overlap_start > overlap_end:
            return None
        # 后接收的更正生效。
        winner, loser = (second, first) if int(second.get("接收序号", 0)) > int(first.get("接收序号", 0)) else (first, second)
        # 涉及订单与已汇总批次：冲突区间内的成员订单、覆盖冲突日期的汇总运行。
        orders = [
            order for order in store.rows(ORDER_MODULE)
            if order.get("成员编码") == member
            and overlap_start <= _parse_day(order.get("业务日期"), "业务日期") <= overlap_end
        ]
        order_batches = sorted({str(order.get("订单批次号", "")) for order in orders})
        amount = round(sum(float(order.get("订单金额", 0) or 0) for order in orders), 2)
        summary_batches = sorted({
            str(run.get("汇总批次号", ""))
            for run in store.rows(SUMMARY_MODULE)
            if overlap_start <= _parse_day(run.get("业务日期"), "业务日期") <= overlap_end
        })
        return {
            "status": "已生效",
            "pending": False,
            "abnormal": True,
            "成员编码": member,
            "成员名称": first.get("成员名称") or second.get("成员名称") or "",
            "冲突日期": overlap_start.isoformat(),
            "冲突区间": f"{overlap_start.isoformat()}~{overlap_end.isoformat()}",
            "更正批次甲": loser.get("更正批次号", ""),
            "上级甲": f"{loser.get('上级编码', '')} {loser.get('上级名称', '')}".strip(),
            "更正批次乙": winner.get("更正批次号", ""),
            "上级乙": f"{winner.get('上级编码', '')} {winner.get('上级名称', '')}".strip(),
            "采用上级": f"{winner.get('上级编码', '')} {winner.get('上级名称', '')}".strip(),
            "采用批次": winner.get("更正批次号", ""),
            "涉及订单批次": "、".join(order_batches) or "无",
            "涉及汇总批次": "、".join(summary_batches) or "无",
            "影响金额": amount,
            "影响说明": (
                f"成员 {member} 在 {overlap_start.isoformat()} 起被两份更正指向不同上级，"
                f"按接收顺序采用 {winner.get('更正批次号', '')} 的归属；"
                f"涉及订单 {len(orders)} 笔、金额 {amount}，"
                f"已汇总批次 {('、'.join(summary_batches)) if summary_batches else '无'} 需要重算复核。"
            ),
        }

    def _apply_relation(
        self, intervals: list[dict[str, Any]], relation: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """把一条成员关系应用到已有区间上：截断重叠部分，保证交界当天唯一归属。"""
        start = _parse_day(relation.get("生效日期"), "生效日期")
        end = _parse_day(relation.get("失效日期"), "失效日期")
        one_day = timedelta(days=1)
        result: list[dict[str, Any]] = []
        for iv in intervals:
            if iv["end"] < start or iv["start"] > end:
                result.append(iv)
                continue
            if iv["start"] < start:
                result.append({**iv, "end": start - one_day})
            if iv["end"] > end:
                result.append({**iv, "start": end + one_day})
        result.append({
            "start": start,
            "end": end,
            "parent": relation.get("上级编码", ""),
            "parent_name": relation.get("上级名称", ""),
            "member_name": relation.get("成员名称", ""),
            "source": relation.get("更正批次号", ""),
        })
        return sorted(result, key=lambda iv: iv["start"])
