"""维度归属映射接口：接收成员关系、归并映射版本、按业务日查询归属、登记冲突。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dim_mapping import DimMappingService

router = APIRouter(prefix="/api/dim_mapping", tags=["维度归属映射"])

service = DimMappingService()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出维度归属映射清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dim_mapping", "total": total, "items": items}


@router.get("/versions")
def list_versions() -> dict[str, Any]:
    """映射版本列表：每次归并产出一份完整快照，旧版本保留供重现。"""
    versions = service.list_versions()
    return {"items": versions, "total": len(versions)}


@router.get("/relations")
def list_relations(status: str | None = Query(default=None, description="待归并、已归并、冲突已归并")) -> dict[str, Any]:
    """成员关系接收表：维度版本区间及成员关系数据的落表记录。"""
    items = service.list_relations(status=status)
    return {"items": items, "total": len(items)}


@router.post("/relations", response_model=ActionResult)
def receive_relation(payload: EntryPayload) -> ActionResult:
    """接收一条成员关系更正；缺字段或日期区间不合法时说明原因。"""
    entry, missing = service.receive_relation(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"成员关系未接收：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"成员关系已接收，等待归并（接收序号 {entry['接收序号']}）", entry=entry)


@router.post("/merge", response_model=ActionResult)
def merge_versions() -> ActionResult:
    """归并待处理的成员关系，生成新的映射版本；冲突会被登记而不是静默覆盖。"""
    result, message = service.merge_versions()
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/conflicts")
def list_conflicts() -> dict[str, Any]:
    """归属冲突记录：同一成员同一天被两份更正指向不同上级的留痕。"""
    items = service.list_conflicts()
    return {"items": items, "total": len(items)}


@router.get("/lookup")
def lookup_attribution(
    member: str = Query(description="成员编码"),
    date: str = Query(description="业务日期 YYYY-MM-DD"),
    version: str | None = Query(default=None, description="映射版本号，缺省取当前生效版本"),
) -> dict[str, Any]:
    """按业务日查成员归属：交界当天最多一个上级；查不到时明确返回未归属。"""
    row, message = service.lookup(member, date, version)
    if row is None:
        return {"ok": False, "message": message, "归属状态": "未归属"}
    return {"ok": True, "message": "ok", "归属状态": "已归属", "item": row}


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按成员编码检索"),
    status: str | None = Query(default=None, description="生效中、历史版本"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按成员编码、版本号等条件过滤归属映射列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条归属映射明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"归属映射 {entry_id} 不存在或已归档")
    return entry
