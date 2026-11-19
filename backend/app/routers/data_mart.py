"""数据集市接口：维护集市表，覆盖提交建模、发布集市、下线集市等动作。

地区收入汇总也在这里：用维度归属映射给订单归属地区后计算地区收入，
无法归属的计入待核对金额；支持重算、冲突影响对比与复核回查。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.data_mart import DataMartService

router = APIRouter(prefix="/api/data_mart", tags=["数据集市"])

service = DataMartService()

LIST_FIELDS = ["表编号", "表名称", "主题域", "事实粒度", "关联维度", "度量字段", "刷新周期", "表状态"]
STATUSES = ["草稿", "已建模", "已发布", "已下线"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据集市清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "data_mart", "total": total, "items": items}


@router.get("/region_summary")
def list_summary_runs(
    business_date: str | None = Query(default=None, description="按业务日期过滤"),
    page: int = 1,
    size: int = 50,
) -> dict[str, Any]:
    """地区收入汇总批次列表：每次汇总固化一个映射版本。"""
    items, total = service.list_summary_runs(business_date=business_date, page=page, size=size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/region_summary", response_model=ActionResult)
def run_region_summary(payload: EntryPayload) -> ActionResult:
    """按业务日汇总地区收入；指定历史映射版本可重现旧报表。"""
    run, message = service.run_region_summary(payload.values)
    if run is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=run)


@router.get("/region_summary/{run_id}")
def get_summary_run(run_id: int) -> dict[str, Any]:
    """汇总批次明细：地区收入、待核对金额与订单批次回查信息。"""
    run = service.get_summary_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"汇总批次 {run_id} 不存在")
    return run


@router.get("/region_summary/{run_id}/impact")
def summary_impact(run_id: int, baseline: int = Query(description="基准汇总批次 id")) -> dict[str, Any]:
    """对比同一业务日两次汇总，展示归属冲突对地区收入的影响。"""
    result, message = service.summary_impact(run_id, baseline)
    if result is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "item": result}


@router.post("/region_summary/{run_id}/review", response_model=ActionResult)
def review_summary(run_id: int) -> ActionResult:
    """复核汇总批次：版本一致性、对回输入、回查订单数据源批次。"""
    result, message = service.review_summary(run_id)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按表编号检索"),
    status: str | None = Query(default=None, description="草稿、已建模、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按表编号与状态过滤数据集市列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条集市表明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"集市表 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条集市表，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="集市表已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条集市表执行提交建模、发布集市、下线集市；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
