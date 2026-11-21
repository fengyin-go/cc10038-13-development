"""缺值分布视图与补采流程路由。

这些固定路径必须在通用 `/api/{module}` 资源路由之前注册，
否则会被动态模块路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import missing_value

router = APIRouter(prefix="/api/missing-values", tags=["缺值分布与补采"])


class RecollectPayload(BaseModel):
    record_ids: list[int] = Field(..., min_length=1)
    scope: str = Field(default="confirmed", pattern="^(confirmed|pending)$")
    batch_no: str | None = None
    source_id: str | None = None
    parser_version: str | None = None
    operator: str | None = None
    note: str | None = None


class VerifyDecision(BaseModel):
    raw_record_id: int
    decision: str = Field(..., pattern="^(new_format|old_format|cannot_confirm)$")
    parser_version: str | None = None
    operator: str | None = None
    note: str | None = None


class VerifyPayload(BaseModel):
    decisions: list[VerifyDecision] = Field(..., min_length=1)


class OperatorPayload(BaseModel):
    operator: str = "值班员"


@router.get("")
def get_missing_value_view(
    batch_no: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    parser_version: str | None = Query(default=None),
    bucket: str | None = Query(default=None),
) -> dict[str, Any]:
    return missing_value.missing_value_view(
        batch_no=batch_no, source_id=source_id,
        parser_version=parser_version, bucket=bucket,
    )


@router.get("/tasks")
def list_collect_tasks() -> dict[str, Any]:
    rows = [
        missing_value.task_detail(int(task["id"]))
        for task in missing_value.store.rows("recollect_task")
    ]
    return {"items": rows, "total": len(rows)}


@router.get("/tasks/{task_id}")
def get_collect_task(task_id: int) -> dict[str, Any]:
    detail = missing_value.task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"补采任务 {task_id} 不存在")
    return detail


@router.post("/recollect")
def submit_recollect(payload: RecollectPayload) -> dict[str, Any]:
    return missing_value.submit_recollect(payload.model_dump())


@router.post("/tasks/{task_id}/run")
def run_collect_task(task_id: int, interrupt_after: int | None = Query(default=None, ge=0)) -> dict[str, Any]:
    return missing_value.run_task(task_id, interrupt_after=interrupt_after)


@router.post("/tasks/{task_id}/retry")
def retry_collect_task(task_id: int, interrupt_after: int | None = Query(default=None, ge=0)) -> dict[str, Any]:
    return missing_value.retry_task(task_id, interrupt_after=interrupt_after)


@router.post("/tasks/{task_id}/verify")
def verify_collect_items(task_id: int, payload: VerifyPayload) -> dict[str, Any]:
    return missing_value.verify_items(
        task_id, [decision.model_dump() for decision in payload.decisions])


@router.post("/tasks/{task_id}/reparse")
def reparse_collected(task_id: int) -> dict[str, Any]:
    return missing_value.reparse_recovered(task_id)


@router.post("/tasks/{task_id}/disposition")
def write_collect_disposition(task_id: int, payload: OperatorPayload | None = None) -> dict[str, Any]:
    operator = payload.operator if payload else "值班员"
    return missing_value.write_disposition(task_id, operator)


@router.get("/dispositions")
def list_collect_dispositions() -> dict[str, Any]:
    return missing_value.list_dispositions()
