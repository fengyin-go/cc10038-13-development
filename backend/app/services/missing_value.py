"""缺值分布视图与补采流程领域服务。

职责：
* 从历史质量结论、标准化明细、原始日志拼出字段级缺值视图，
  按「来源 + 解析规则版本」分组排列原始行；
* 区分两类记录：确认需要补采的日志，以及格式/口径待核对的缺值；
  未核实的记录不能作为已确认缺值下发；
* 从视图提交补采任务，任务保存提交时的范围快照；中断后重试只按原范围继续，
  已经补到的行不会再次入队；
* 核对结论回写处置清单，补采到的原文可重新解析生成新的标准化明细，
  历史解析版本及其字段口径保持原样。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from app.store import store

FORMAT_LABELS = {"old": "旧格式", "new": "新格式", "unknown": "格式不明"}
BUCKET_CONFIRMED = "已确认缺值"
BUCKET_PENDING = "待核对"
BUCKET_RECOVERED = "已补采"
BUCKET_NOT_REQUIRED = "无需补采"
BUCKETS = [BUCKET_CONFIRMED, BUCKET_PENDING, BUCKET_RECOVERED, BUCKET_NOT_REQUIRED]

TASK_ACTIVE = {"待执行", "待核对", "补采中", "中断", "部分完成"}
ITEM_PENDING = "待补采"
ITEM_REVIEW = "待核对"
ITEM_RECOVERED = "已补采"
ITEM_NOT_REQUIRED = "无需补采"
ITEM_FAILED = "失败"

DEFAULT_FIELDS = {
    "new": {"required": ["time", "level", "message", "host"], "optional": []},
    "old": {"required": ["time", "level", "message"], "optional": ["host"]},
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _next_id(module: str) -> int:
    return max((int(r.get("id", 0)) for r in store.rows(module)), default=0) + 1


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _find_raw(raw_id: int) -> dict[str, Any] | None:
    return store.find("raw_records", int(raw_id))


def _find_entity(entity_id: Any) -> dict[str, Any] | None:
    try:
        return store.find("parsed_entity", int(entity_id))
    except (TypeError, ValueError):
        return None


def _find_conclusion(conclusion_id: Any) -> dict[str, Any] | None:
    try:
        return store.find("quality_conclusion", int(conclusion_id))
    except (TypeError, ValueError):
        return None


def _rule_fields(version: str | None) -> dict[str, list[str]]:
    """按解析规则版本取字段口径；找不到时按版本命名回退。"""
    if version:
        for rule in store.rows("field_parser"):
            if rule.get("规则名称") == version:
                required = rule.get("required_fields")
                optional = rule.get("optional_fields")
                if required is None:
                    names = [p.strip() for p in str(
                        rule.get("输出字段列表", "")).split(",") if p.strip()]
                    required = names or list(DEFAULT_FIELDS["new"]["required"])
                    optional = []
                return {"required": list(required), "optional": list(optional or [])}
    scope = "old" if version and "old" in str(version) else "new"
    return {"required": list(DEFAULT_FIELDS[scope]["required"]),
            "optional": list(DEFAULT_FIELDS[scope]["optional"])}


def _row_task_item(raw_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """找出包含该原始行的补采任务条目（优先未完成的任务）。"""
    fallback: tuple[dict[str, Any] | None, dict[str, Any] | None] = (None, None)
    for task in store.rows("recollect_task"):
        for item in task.get("items", []):
            if int(item.get("raw_record_id", -1)) != int(raw_id):
                continue
            if item.get("status") in (ITEM_RECOVERED,):
                return task, item
            if fallback == (None, None):
                fallback = (task, item)
    return fallback


def _queued_item(raw_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for task in store.rows("recollect_task"):
        if task.get("status") not in TASK_ACTIVE:
            continue
        for item in task.get("items", []):
            if int(item.get("raw_record_id", -1)) == int(raw_id) and item.get("status") in (
                    ITEM_PENDING, ITEM_REVIEW):
                return task, item
    return None, None


def _build_row(conclusion: dict[str, Any]) -> dict[str, Any]:
    raw_id = int(conclusion.get("raw_record_id", 0))
    raw = _find_raw(raw_id) or {}
    entity = _find_entity(conclusion.get("parsed_entity_id")) or {}
    fields = _loads(entity.get("标准化字段集"))
    if not fields and raw.get("标准化字段集"):
        fields = _loads(raw.get("标准化字段集"))

    parser_version = conclusion.get("parser_version") or entity.get("解析规则版本", "")
    scope = _rule_fields(parser_version)
    required = list(conclusion.get("required_fields") or scope["required"])
    optional = list(conclusion.get("optional_fields") or scope["optional"])
    missing = [f for f in (conclusion.get("missing_fields") or []) if str(f)]
    candidate = [f for f in (conclusion.get("candidate_fields") or []) if str(f)]

    task, item = _row_task_item(raw_id)
    queued_task, queued = _queued_item(raw_id)
    recovered = bool(item and item.get("status") == ITEM_RECOVERED)
    if raw.get("reparse_status") == "已重新解析":
        recovered = True

    format_confidence = conclusion.get("format_confidence") or raw.get("format_confidence", "confirmed")
    source_format = conclusion.get("source_format") or raw.get("source_format", "unknown")
    version_confirmed = bool(conclusion.get("parser_version_confirmed", True))

    if recovered:
        bucket = BUCKET_RECOVERED
    elif conclusion.get("status") == "待核对" or format_confidence == "unknown" \
            or source_format == "unknown" or not version_confirmed:
        bucket = BUCKET_PENDING
    elif missing:
        bucket = BUCKET_CONFIRMED
    else:
        bucket = BUCKET_NOT_REQUIRED

    field_missing = []
    for field in missing:
        state = "candidate" if bucket == BUCKET_PENDING else "confirmed"
        if field in candidate and bucket == BUCKET_PENDING:
            state = "candidate"
        field_missing.append({"field": field, "state": state})
    for field in candidate:
        if field not in missing:
            field_missing.append({"field": field, "state": "candidate"})

    selectable = bucket in (BUCKET_CONFIRMED, BUCKET_PENDING) and not recovered
    blocked_reason = ""
    if queued is not None:
        selectable = False
        blocked_reason = f"已在补采任务 {queued_task.get('task_no')} 中"
    elif recovered:
        selectable = False
        blocked_reason = "已补采，不会再次入队"
    elif bucket == BUCKET_NOT_REQUIRED:
        selectable = False
        blocked_reason = str(conclusion.get("summary") or "按历史口径无需补采")

    return {
        "id": raw_id,
        "raw_record_id": raw_id,
        "parsed_entity_id": conclusion.get("parsed_entity_id") or entity.get("id"),
        "batch_no": conclusion.get("batch_no") or raw.get("批次编号", ""),
        "source_id": conclusion.get("source_id") or raw.get("source_id", ""),
        "来源": conclusion.get("source_id") or raw.get("source_id", ""),
        "origin_label": conclusion.get("origin_label") or conclusion.get("source_id")
        or raw.get("source_id", ""),
        "parser_version": parser_version,
        "parser_version_confirmed": version_confirmed,
        "effective_parser_version": conclusion.get("verified_parser_version") or parser_version,
        "parser_version_source": "历史解析版本" if conclusion.get("historical_parser_version")
        else "当前结论所引用的解析版本",
        "historical_parser_version": conclusion.get("historical_parser_version", ""),
        "verified_parser_version": conclusion.get("verified_parser_version", ""),
        "line_no": raw.get("原始行号", ""),
        "raw_body": raw.get("原始正文", ""),
        "source_format": source_format,
        "format_label": FORMAT_LABELS.get(source_format, "格式不明"),
        "format_confidence": format_confidence,
        "field_values": fields,
        "required_fields": required,
        "optional_fields": optional,
        "field_missing": field_missing,
        "missing_fields": [f["field"] for f in field_missing if f["state"] == "confirmed"],
        "candidate_fields": [f["field"] for f in field_missing if f["state"] == "candidate"],
        "conclusion_status": conclusion.get("status", ""),
        "conclusion_type": conclusion.get("conclusion_type", ""),
        "conclusion_summary": conclusion.get("summary", ""),
        "conclusion_id": conclusion.get("id"),
        "quality_conclusion_id": conclusion.get("id"),
        "rule_version_note": conclusion.get("rule_version_note", ""),
        "bucket": bucket,
        "view_status": bucket,
        "selectable": selectable,
        "selectable_scope": bucket if selectable else "",
        "blocked_reason": blocked_reason,
        "queued": queued is not None,
        "task_id": (queued_task or {}).get("id"),
        "task_no": (queued_task or {}).get("task_no", ""),
        "task_item_status": (queued or {}).get("status", ""),
        "recovered": recovered,
        "recovered_fields": (item or {}).get("recovered_fields", {}),
        "reparse_status": raw.get("reparse_status", ""),
        "reparsed_entity_id": (item or {}).get("reparsed_entity_id"),
        "status": bucket,
        "pending": bucket in (BUCKET_CONFIRMED, BUCKET_PENDING),
        "abnormal": bucket == BUCKET_PENDING,
    }


def _all_rows(batch_no: str | None = None, source_id: str | None = None,
              parser_version: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for conclusion in store.rows("quality_conclusion"):
        row = _build_row(conclusion)
        if batch_no and row["batch_no"] != batch_no:
            continue
        if source_id and row["source_id"] != source_id:
            continue
        if parser_version and row["parser_version"] != parser_version:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (str(r["source_id"]), str(r["parser_version"]),
                             int(r["raw_record_id"])))
    return rows


def _field_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["bucket"] not in (BUCKET_CONFIRMED, BUCKET_PENDING):
            continue
        for entry in row["field_missing"]:
            item = stats.setdefault(entry["field"], {
                "field": entry["field"], "confirmed": 0, "candidate": 0,
                "records": [],
            })
            item[entry["state"]] += 1
            item["records"].append(row["raw_record_id"])
    return sorted(stats.values(), key=lambda item: (-(item["confirmed"] + item["candidate"]),
                                                    item["field"]))


def missing_value_view(
    batch_no: str | None = None,
    source_id: str | None = None,
    parser_version: str | None = None,
    bucket: str | None = None,
) -> dict[str, Any]:
    """缺值分布视图：按来源和解析版本分组展示原始行及字段级缺值。"""
    all_rows = _all_rows(batch_no=batch_no, source_id=source_id,
                         parser_version=parser_version)
    rows = [row for row in all_rows if not bucket or row["bucket"] == bucket]

    groups: list[dict[str, Any]] = []
    for row in rows:
        group = next((g for g in groups if g["source_id"] == row["source_id"]), None)
        if group is None:
            group = {"source_id": row["source_id"], "origin_label": row["origin_label"],
                     "versions": []}
            groups.append(group)
        version = next((v for v in group["versions"]
                        if v["parser_version"] == row["parser_version"]), None)
        if version is None:
            first = row
            version = {
                "parser_version": row["parser_version"],
                "parser_version_confirmed": row["parser_version_confirmed"],
                "rule_version_note": first["rule_version_note"],
                "format_labels": [],
                "rows": [],
            }
            group["versions"].append(version)
        label = row["format_label"]
        if label not in version["format_labels"]:
            version["format_labels"].append(label)
        version["rows"].append(row)

    counts = {name: sum(1 for row in all_rows if row["bucket"] == name) for name in BUCKETS}
    return {
        "generated_at": _now_iso(),
        "batch_no": batch_no or "",
        "source_id": source_id or "",
        "parser_version": parser_version or "",
        "bucket": bucket or "",
        "buckets": counts,
        "field_stats": _field_stats(all_rows),
        "groups": groups,
        "rows": rows,
        "total": len(rows),
    }


# ---------- 补采任务 ----------
def _task_by_id(task_id: int) -> dict[str, Any] | None:
    try:
        return store.find("recollect_task", int(task_id))
    except (TypeError, ValueError):
        return None


def _task_item(task: dict[str, Any], raw_id: int) -> dict[str, Any] | None:
    for item in task.get("items", []):
        if int(item.get("raw_record_id", -1)) == int(raw_id):
            return item
    return None


def _item_detail(task: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    raw_id = int(item.get("raw_record_id", 0))
    raw = _find_raw(raw_id) or {}
    conclusion = _find_conclusion(item.get("quality_conclusion_id")) or {}
    reparsed = _find_entity(item.get("reparsed_entity_id"))
    return {
        **item,
        "raw_record_id": raw_id,
        "line_no": raw.get("原始行号", ""),
        "raw_body": raw.get("原始正文", ""),
        "batch_no": raw.get("批次编号", ""),
        "source_id": raw.get("source_id", ""),
        "parser_version": item.get("parser_version")
        or conclusion.get("parser_version", ""),
        "quality_conclusion_id": item.get("quality_conclusion_id"),
        "conclusion_status": conclusion.get("status", ""),
        "conclusion_type": conclusion.get("conclusion_type", ""),
        "conclusion_summary": conclusion.get("summary", ""),
        "conclusion_rule_note": conclusion.get("rule_version_note", ""),
        "historical_parser_version": conclusion.get("historical_parser_version", ""),
        "current_parser_version": conclusion.get("parser_version", ""),
        "field_missing": [
            {"field": field, "state": "candidate" if item.get("classification") == "pending"
             else "confirmed"}
            for field in item.get("missing_fields", [])
        ],
        "reparsed_entity": reparsed,
        "downstream_ready": bool(
            item.get("classification") == "confirmed"
            and item.get("status") == ITEM_RECOVERED
            and conclusion.get("status") == "已确认"
        ),
    }


def task_detail(task_id: int) -> dict[str, Any] | None:
    task = _task_by_id(task_id)
    if task is None:
        return None
    detail = dict(task)
    detail["items"] = [_item_detail(task, item) for item in task.get("items", [])]
    detail["scope"] = [int(raw_id) for raw_id in task.get("scope_snapshot_ids", [])]
    detail["dispositions"] = [
        entry for entry in store.rows("quality_disposition")
        if int(entry.get("task_id", -1)) == int(task["id"])
    ]
    return detail


def submit_recollect(payload: dict[str, Any]) -> dict[str, Any]:
    """从缺值分布视图提交补采；确认范围和未核实范围分开受理。"""
    scope = str(payload.get("scope") or "confirmed")
    if scope not in ("confirmed", "pending"):
        return {"ok": False, "message": f"不支持的补采范围：{scope}"}
    try:
        record_ids = [int(value) for value in payload.get("record_ids") or []]
    except (TypeError, ValueError):
        return {"ok": False, "message": "补采范围里的原始记录 ID 必须是整数"}
    if not record_ids:
        return {"ok": False, "message": "请选择需要补采的原始行"}
    if len(set(record_ids)) != len(record_ids):
        return {"ok": False, "message": "补采范围存在重复的原始记录"}

    rows = {row["raw_record_id"]: row for row in _all_rows()}
    expected_bucket = BUCKET_CONFIRMED if scope == "confirmed" else BUCKET_PENDING
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for raw_id in record_ids:
        row = rows.get(raw_id)
        if row is None:
            rejected.append({"raw_record_id": str(raw_id), "reason": "原始行不存在或没有质量结论"})
            continue
        if row["bucket"] == BUCKET_RECOVERED:
            rejected.append({"raw_record_id": str(raw_id), "reason": "该行已补采，不会再次入队"})
            continue
        if row["queued"]:
            rejected.append({"raw_record_id": str(raw_id),
                             "reason": row["blocked_reason"] or "该行已在补采任务中"})
            continue
        if row["bucket"] != expected_bucket:
            if scope == "confirmed" and row["bucket"] == BUCKET_PENDING:
                rejected.append({"raw_record_id": str(raw_id),
                                 "reason": "格式或口径尚未核实，不能作为已确认缺值提交"})
            elif scope == "pending" and row["bucket"] == BUCKET_CONFIRMED:
                rejected.append({"raw_record_id": str(raw_id),
                                 "reason": "该行已确认缺值，请走已确认补采范围"})
            else:
                rejected.append({"raw_record_id": str(raw_id),
                                 "reason": row["blocked_reason"] or f"当前状态为{row['bucket']}"})
            continue
        if scope == "confirmed" and (
                not row["parser_version_confirmed"]
                or row["conclusion_status"] != "已确认"
                or row["format_confidence"] == "unknown"):
            rejected.append({"raw_record_id": str(raw_id),
                             "reason": "字段口径未核实，不能作为已确认缺值发往下游"})
            continue
        accepted.append(row)

    if rejected:
        return {
            "ok": False,
            "message": "补采范围未提交：存在不能进入该范围的原始行",
            "rejected": rejected,
            "accepted": [row["raw_record_id"] for row in accepted],
        }

    source_ids = {row["source_id"] for row in accepted}
    versions = {row["parser_version"] for row in accepted}
    source_id = payload.get("source_id") or (source_ids.pop() if len(source_ids) == 1 else "混合来源")
    parser_version = payload.get("parser_version") or (
        versions.pop() if len(versions) == 1 else "混合版本")
    batch_no = payload.get("batch_no") or accepted[0]["batch_no"]

    items = []
    for row in accepted:
        items.append({
            "raw_record_id": row["raw_record_id"],
            "quality_conclusion_id": row["conclusion_id"],
            "classification": "confirmed" if scope == "confirmed" else "pending",
            "parser_version": row["parser_version"],
            "missing_fields": row["missing_fields"] if scope == "confirmed"
            else [entry["field"] for entry in row["field_missing"]],
            "source_format": row["source_format"],
            "status": ITEM_PENDING if scope == "confirmed" else ITEM_REVIEW,
            "verified": scope == "confirmed",
            "recovered_fields": {},
            "recovered_value": "",
            "recovered_at": "",
            "review_conclusion": "",
            "disposition_id": None,
            "reparsed_entity_id": None,
        })

    task_id = _next_id("recollect_task")
    task = {
        "id": task_id,
        "task_no": f"RECL-{task_id:04d}",
        "来源": source_id,
        "来源标识": source_id,
        "补采范围": f"{len(items)} 条原始行",
        "解析规则版本": parser_version,
        "任务状态": "待执行",
        "status": "待执行",
        "pending": True,
        "abnormal": scope == "pending",
        "source_id": source_id,
        "batch_no": batch_no,
        "parser_version": parser_version,
        "mode": scope,
        "quality_state": "已确认" if scope == "confirmed" else "未核实",
        "downstream_allowed": scope == "confirmed",
        "scope_snapshot_ids": [item["raw_record_id"] for item in items],
        "scope_count": len(items),
        "scope_snapshot": [dict(item) for item in items],
        "items": items,
        "attempts": 0,
        "submitted_at": _now_iso(),
        "submitted_by": str(payload.get("operator") or "值班员"),
        "last_retry_at": "",
        "interrupted_at": "",
        "completed_at": "",
        "disposition_ids": [],
        "note": str(payload.get("note") or (
            "从缺值分布视图提交已确认缺值" if scope == "confirmed"
            else "从缺值分布视图提交待核对缺值，核实前不得作为已确认缺值下发")),
    }
    store.rows("recollect_task").append(task)

    retry_rows = store.rows("retry_queue")
    for item in items:
        retry_rows.append({
            "id": _next_id("retry_queue"),
            "status": "待重试",
            "pending": True,
            "abnormal": False,
            "失败记录ID": str(item["raw_record_id"]),
            "失败原因": f"缺值补采：{','.join(item['missing_fields']) or '待核对'}",
            "重试次数": "0",
            "最大重试": "3",
            "下次重试时间": _now_iso(),
            "batch_no": batch_no,
            "quality_conclusion_id": item["quality_conclusion_id"],
            "task_id": task_id,
            "source": "missing_value_view",
        })

    for raw_id in task["scope_snapshot_ids"]:
        raw = _find_raw(raw_id)
        if raw is not None:
            raw["recollect_state"] = "已入队"
            raw["recollect_task_id"] = task_id

    return {
        "ok": True,
        "message": (f"补采任务 {task['task_no']} 已提交（{task['quality_state']}范围，"
                    f"{len(items)} 条原始行）"),
        "task": task_detail(task_id),
        "scope": task["scope_snapshot_ids"],
    }


def _recover_item(task: dict[str, Any], item: dict[str, Any]) -> bool:
    raw_id = int(item["raw_record_id"])
    raw = _find_raw(raw_id)
    if raw is None:
        item["status"] = ITEM_FAILED
        item["review_conclusion"] = "原始记录不存在，补采失败"
        return False
    recovered_fields = {}
    for field in item.get("missing_fields", []):
        if field == "host":
            recovered_fields[field] = f"host-{raw_id:02d}.recovered"
        else:
            recovered_fields[field] = f"recovered-{field}-{raw_id:02d}"
    item["status"] = ITEM_RECOVERED
    item["recovered_fields"] = recovered_fields
    item["recovered_value"] = _dumps(recovered_fields)
    item["recovered_at"] = _now_iso()
    item["recovered_value_note"] = "补采到的原始字段值，可重新解析进入标准化明细"
    raw["recollect_state"] = "已补采"
    raw["recovered_fields"] = dict(recovered_fields)
    raw["recovered_task_id"] = task["id"]
    raw["recovered_at"] = item["recovered_at"]
    return True


def _sync_retry_queue(task: dict[str, Any]) -> None:
    """把补采任务进度同步回重试队列，已补采的行不再排队。"""
    for entry in store.rows("retry_queue"):
        if entry.get("source") != "missing_value_view":
            continue
        if int(entry.get("task_id", -1)) != int(task["id"]):
            continue
        item = _task_item(task, int(entry.get("失败记录ID", -1)))
        if item is None:
            continue
        entry["补采任务"] = task.get("task_no", "")
        entry["补采结果"] = item.get("status", "")
        if item.get("status") in (ITEM_RECOVERED, ITEM_NOT_REQUIRED):
            entry["pending"] = False
        if task.get("status") == "已完成":
            entry["pending"] = False
        elif task.get("status") == "中断":
            entry["pending"] = True


def _finalize_task(task: dict[str, Any], interrupted: bool) -> None:
    _finalize_status(task, interrupted)
    _sync_retry_queue(task)


def _finalize_status(task: dict[str, Any], interrupted: bool) -> None:
    if interrupted:
        task["status"] = "中断"
        task["任务状态"] = "中断"
        task["pending"] = True
        task["abnormal"] = True
        task["interrupted_at"] = _now_iso()
        return
    items = task.get("items", [])
    for item in items:
        if item["status"] == ITEM_PENDING and item.get("classification") == "pending" \
                and not item.get("verified"):
            item["status"] = ITEM_REVIEW
    if any(item.get("classification") == "pending" and not item.get("verified")
           for item in items):
        # 还有未核实的原始行：任务不能算完成，保持可继续核对/补采。
        task["status"] = "待核对"
        task["任务状态"] = "待核对"
        task["pending"] = True
        task["abnormal"] = False
        task["awaiting_review_at"] = _now_iso()
        return
    if not items or all(item["status"] == ITEM_RECOVERED for item in items):
        task["status"] = "已完成"
        task["任务状态"] = "已完成"
        task["pending"] = False
        task["abnormal"] = False
        task["completed_at"] = _now_iso()
    elif any(item["status"] in (ITEM_RECOVERED, ITEM_NOT_REQUIRED) for item in items):
        task["status"] = "部分完成"
        task["任务状态"] = "部分完成"
        task["pending"] = True
        task["abnormal"] = any(item["status"] == ITEM_FAILED for item in items)
        task["completed_at"] = _now_iso()
    else:
        task["status"] = "待执行"
        task["任务状态"] = "待执行"
        task["pending"] = True


def _run_items(task: dict[str, Any], interrupt_after: int | None) -> dict[str, Any]:
    task["status"] = "补采中"
    task["任务状态"] = "补采中"
    processed: list[int] = []
    awaiting_review: list[int] = []
    already: list[int] = []
    interrupted = False
    for item in task.get("items", []):
        if item["status"] == ITEM_RECOVERED:
            # 已经补到的行不再重复入队。
            already.append(int(item["raw_record_id"]))
            continue
        if item["status"] != ITEM_PENDING:
            if item["status"] == ITEM_REVIEW:
                awaiting_review.append(int(item["raw_record_id"]))
            continue
        if item.get("classification") == "pending" and not item.get("verified"):
            item["status"] = ITEM_REVIEW
            awaiting_review.append(int(item["raw_record_id"]))
            continue
        if interrupt_after is not None and len(processed) >= interrupt_after:
            interrupted = True
            break
        if _recover_item(task, item):
            processed.append(int(item["raw_record_id"]))
    _finalize_task(task, interrupted)
    return {
        "processed": processed,
        "awaiting_review": awaiting_review,
        "already_recovered": already,
        "interrupted": interrupted,
    }


def run_task(task_id: int, interrupt_after: int | None = None) -> dict[str, Any]:
    task = _task_by_id(task_id)
    if task is None:
        return {"ok": False, "message": f"补采任务 {task_id} 不存在"}
    if task["status"] == "已完成":
        return {"ok": False, "message": f"补采任务 {task['task_no']} 已完成，无需重复执行"}
    result = _run_items(task, interrupt_after)
    message = (f"补采任务 {task['task_no']} 已中断，剩余原始行保留在本任务范围"
               if result["interrupted"]
               else f"补采任务 {task['task_no']} 执行完成")
    if result["awaiting_review"]:
        message += "；待核对原始行未补采，需先完成核实"
    return {"ok": True, "message": message, **result, "task": task_detail(task_id)}


def retry_task(task_id: int, interrupt_after: int | None = None) -> dict[str, Any]:
    """中断/部分完成后重试：范围仍取提交时的快照，不重新挑选原始行。"""
    task = _task_by_id(task_id)
    if task is None:
        return {"ok": False, "message": f"补采任务 {task_id} 不存在"}
    if task["status"] not in ("待执行", "待核对", "中断", "部分完成"):
        return {"ok": False, "message": f"补采任务 {task['task_no']} 当前状态为{task['status']}，不能重试"}
    scope_ids = [int(raw_id) for raw_id in task.get("scope_snapshot_ids", [])]
    current_ids = [int(item["raw_record_id"]) for item in task.get("items", [])]
    if scope_ids != current_ids:
        return {"ok": False, "message": "补采任务范围快照与明细不一致，已阻止重试"}
    task["attempts"] = int(task.get("attempts", 0)) + 1
    task["last_retry_at"] = _now_iso()
    result = _run_items(task, interrupt_after)
    return {
        "ok": True,
        "message": (f"补采任务 {task['task_no']} 已按原提交范围重试"
                    f"（{len(scope_ids)} 条，已补采的行不重复处理）"),
        "scope": scope_ids,
        **result,
        "task": task_detail(task_id),
    }


# ---------- 核对 ----------
def verify_items(task_id: int, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """核对待定记录的格式与字段口径，结论回写历史质量结论。"""
    task = _task_by_id(task_id)
    if task is None:
        return {"ok": False, "message": f"补采任务 {task_id} 不存在"}
    if not decisions:
        return {"ok": False, "message": "请提供核对结论"}
    updated: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for decision in decisions:
        try:
            raw_id = int(decision.get("raw_record_id"))
        except (TypeError, ValueError):
            rejected.append({"raw_record_id": str(decision.get("raw_record_id")),
                             "reason": "原始记录 ID 必须是整数"})
            continue
        item = _task_item(task, raw_id)
        if item is None:
            rejected.append({"raw_record_id": str(raw_id), "reason": "不在本任务的提交范围内"})
            continue
        if item["status"] == ITEM_RECOVERED:
            rejected.append({"raw_record_id": str(raw_id), "reason": "该行已补采，不再重复核对"})
            continue
        outcome = str(decision.get("decision") or "")
        if outcome not in ("new_format", "old_format", "cannot_confirm"):
            rejected.append({"raw_record_id": str(raw_id), "reason": f"不支持的核对结论：{outcome}"})
            continue
        conclusion = _find_conclusion(item.get("quality_conclusion_id"))
        if conclusion is None:
            rejected.append({"raw_record_id": str(raw_id), "reason": "历史质量结论不存在"})
            continue
        raw = _find_raw(raw_id) or {}
        fields = _loads((_find_entity(conclusion.get("parsed_entity_id")) or {})
                        .get("标准化字段集"))
        operator = str(decision.get("operator") or "值班员")
        note = str(decision.get("note") or "")
        conclusion.setdefault("historical_parser_version", conclusion.get("parser_version", ""))
        conclusion.setdefault("historical_required_fields", list(conclusion.get("required_fields") or []))

        if outcome == "new_format":
            target_version = str(decision.get("parser_version") or "")
            if not target_version:
                target_version = next((rule.get("规则名称") for rule in store.rows("field_parser")
                                       if rule.get("format_scope") == "new"), "app-log-new-v2")
            scope = _rule_fields(target_version)
            missing = [f for f in scope["required"] if not str(fields.get(f, "")).strip()]
            conclusion.update({
                "status": "已确认",
                "conclusion_type": "字段缺值",
                "source_format": "new",
                "format_confidence": "confirmed",
                "parser_version_confirmed": True,
                "verified_parser_version": target_version,
                "required_fields": scope["required"],
                "optional_fields": scope["optional"],
                "candidate_fields": [],
                "missing_fields": missing,
                "summary": f"核对结论：该历史行按新版格式处理，host 等必填字段按 {target_version} 口径判定",
                "verified_at": _now_iso(),
                "verified_by": operator,
                "verification_note": note,
                "rule_version_note": (
                    f"历史解析版本 {conclusion['historical_parser_version']} 的字段口径仍保留；"
                    f"核对后按 {target_version} 重新判定缺值"),
            })
            item.update({
                "classification": "confirmed",
                "verified": True,
                "status": ITEM_PENDING,
                "missing_fields": missing,
                "source_format": "new",
                "parser_version": target_version,
                "review_conclusion": "按新版格式核对，host 必填",
            })
            raw.update({"source_format": "new", "format_confidence": "confirmed",
                        "parser_version_confirmed": True, "verified_format": "new"})
            updated.append({"raw_record_id": raw_id, "decision": outcome,
                            "missing_fields": missing})
        elif outcome == "old_format":
            scope = _rule_fields(conclusion["historical_parser_version"])
            missing = [f for f in scope["required"] if not str(fields.get(f, "")).strip()]
            conclusion.update({
                "status": "无缺值" if not missing else "已确认",
                "conclusion_type": "无需补采" if not missing else "字段缺值",
                "source_format": "old",
                "format_confidence": "confirmed",
                "parser_version_confirmed": True,
                "required_fields": scope["required"],
                "optional_fields": scope["optional"],
                "candidate_fields": [],
                "missing_fields": missing,
                "summary": (f"核对结论：该历史行确认为旧版格式，{scope['required']} 必填、"
                            f"host 可空，按历史口径无需补采" if not missing else
                            f"核对结论：确认为旧版格式，仍缺少 {','.join(missing)}"),
                "verified_at": _now_iso(),
                "verified_by": operator,
                "verification_note": note,
                "rule_version_note": (
                    f"沿用历史解析版本 {conclusion['historical_parser_version']} 的字段口径：host 可空"),
            })
            item.update({
                "classification": "not_required" if not missing else "confirmed",
                "verified": True,
                "status": ITEM_NOT_REQUIRED if not missing else ITEM_PENDING,
                "missing_fields": missing,
                "source_format": "old",
                "review_conclusion": "按旧版格式核对，host 可空",
            })
            raw.update({"source_format": "old", "format_confidence": "confirmed",
                        "parser_version_confirmed": True, "verified_format": "old"})
            updated.append({"raw_record_id": raw_id, "decision": outcome,
                            "missing_fields": missing})
        else:
            item.update({"verified": False, "status": ITEM_REVIEW,
                         "review_conclusion": "仍无法确认格式，保持待核对"})
            conclusion.update({
                "status": "待核对",
                "verification_note": note,
                "last_reviewed_at": _now_iso(),
                "last_reviewed_by": operator,
                "summary": conclusion.get("summary") or "格式无法确认，需继续核对后才能补采",
            })
            updated.append({"raw_record_id": raw_id, "decision": outcome,
                            "missing_fields": conclusion.get("missing_fields", [])})
    if rejected:
        return {"ok": False, "message": "部分核对结论未受理", "rejected": rejected,
                "updated": updated, "task": task_detail(task_id)}
    _finalize_task(task, False)
    return {
        "ok": True,
        "message": f"核对结论已回写历史质量结论（{len(updated)} 条），补采只针对已核实的原始行",
        "updated": updated,
        "task": task_detail(task_id),
    }


# ---------- 重新解析 ----------
def reparse_recovered(task_id: int) -> dict[str, Any]:
    """把补采到的原文重新解析进标准化明细，不覆盖历史解析版本。"""
    task = _task_by_id(task_id)
    if task is None:
        return {"ok": False, "message": f"补采任务 {task_id} 不存在"}
    created: list[int] = []
    skipped: list[int] = []
    for item in task.get("items", []):
        if item.get("status") != ITEM_RECOVERED:
            skipped.append(int(item["raw_record_id"]))
            continue
        if item.get("reparsed_entity_id"):
            skipped.append(int(item["raw_record_id"]))
            continue
        raw = _find_raw(int(item["raw_record_id"]))
        conclusion = _find_conclusion(item.get("quality_conclusion_id")) or {}
        old_entity = _find_entity(conclusion.get("parsed_entity_id")) or {}
        fields = _loads(old_entity.get("标准化字段集"))
        fields.update(item.get("recovered_fields") or {})
        scope = _rule_fields(item.get("parser_version"))
        missing = [f for f in scope["required"] if not str(fields.get(f, "")).strip()]
        entity_id = _next_id("parsed_entity")
        digest = hashlib.sha256(_dumps(fields).encode()).hexdigest()[:16]
        entity = {
            "id": entity_id,
            "status": "待校验",
            "pending": True,
            "abnormal": bool(missing),
            "来源批次": (raw or {}).get("批次编号") or conclusion.get("batch_no", ""),
            "原始记录ID": str(item["raw_record_id"]),
            "解析规则版本": item.get("parser_version", ""),
            "标准化字段集": _dumps(fields),
            "校验状态": "待校验",
            "parser_rule": item.get("parser_version", ""),
            "schema_version": old_entity.get("schema_version", ""),
            "source_format": item.get("source_format", ""),
            "format_confidence": "confirmed",
            "parser_version_confirmed": True,
            "missing_fields": missing,
            "quality_conclusion_id": item.get("quality_conclusion_id"),
            "reparsed_from_entity_id": conclusion.get("parsed_entity_id"),
            "reparse_task_id": task["id"],
            "reparsed_at": _now_iso(),
            "content_digest": digest,
        }
        store.rows("parsed_entity").append(entity)
        item["reparsed_entity_id"] = entity_id
        created.append(entity_id)
        if raw is not None:
            raw["reparse_status"] = "已重新解析"
            raw["reparse_entity_id"] = entity_id
            raw["reparse_task_id"] = task["id"]
            raw["pending"] = False
            raw["abnormal"] = bool(missing)
            raw["status"] = "已解析"
            raw["解析状态"] = "已解析"
        conclusion["reparsed_entity_id"] = entity_id
        conclusion["reparsed_at"] = _now_iso()
    return {
        "ok": bool(created),
        "message": (f"补采原文已重新解析，新增 {len(created)} 条标准化明细"
                    if created else "没有可重新解析的已补采原始行"),
        "created_entity_ids": created,
        "skipped_raw_ids": skipped,
        "task": task_detail(task_id),
    }


# ---------- 处置清单 ----------
def _field_conclusions(item: dict[str, Any], conclusion: dict[str, Any],
                       fields: dict[str, Any]) -> list[dict[str, Any]]:
    required = list(conclusion.get("required_fields") or [])
    optional = list(conclusion.get("optional_fields") or [])
    recovered = item.get("recovered_fields") or {}
    entries = []
    for field in required + [f for f in optional if f not in required]:
        value = recovered.get(field, fields.get(field, ""))
        is_required = field in required
        entries.append({
            "field": field,
            "required": is_required,
            "value": str(value),
            "missing": is_required and not str(value).strip(),
            "state": ("待核对" if item.get("classification") == "pending"
                      else "已补齐" if field in recovered else
                      "缺值" if is_required and not str(value).strip() else "正常"),
        })
    return entries


def write_disposition(task_id: int, operator: str = "值班员") -> dict[str, Any]:
    """把核对/补采结论回写处置清单，未核实的记录标记为不可下发。"""
    task = _task_by_id(task_id)
    if task is None:
        return {"ok": False, "message": f"补采任务 {task_id} 不存在"}
    existing = {int(entry.get("raw_record_id", -1)) for entry in store.rows("quality_disposition")
                if int(entry.get("task_id", -1)) == int(task["id"])}
    written: list[dict[str, Any]] = []
    skipped: list[int] = []
    for item in task.get("items", []):
        raw_id = int(item["raw_record_id"])
        if raw_id in existing:
            skipped.append(raw_id)
            continue
        if item.get("status") not in (ITEM_RECOVERED, ITEM_NOT_REQUIRED, ITEM_REVIEW, ITEM_FAILED):
            skipped.append(raw_id)
            continue
        conclusion = _find_conclusion(item.get("quality_conclusion_id")) or {}
        old_entity = _find_entity(conclusion.get("parsed_entity_id")) or {}
        fields = _loads(old_entity.get("标准化字段集"))
        fields.update(item.get("recovered_fields") or {})
        field_entries = _field_conclusions(item, conclusion, fields)
        if item.get("status") == ITEM_RECOVERED:
            decision = "补采成功"
        elif item.get("status") == ITEM_NOT_REQUIRED:
            decision = "无需补采"
        elif item.get("classification") == "pending" and not item.get("verified"):
            decision = "未核实，不得作为已确认缺值下发"
        else:
            decision = "仍缺值"
        downstream_ready = bool(
            item.get("classification") == "confirmed"
            and item.get("status") == ITEM_RECOVERED
            and conclusion.get("status") == "已确认"
        )
        entity_id = _next_id("quality_disposition")
        entry = {
            "id": entity_id,
            "status": "已回写",
            "pending": item.get("status") == ITEM_REVIEW,
            "abnormal": not downstream_ready and decision == "仍缺值",
            "处置编号": f"DISP-{entity_id:04d}",
            "关联补采任务": task["task_no"],
            "原始记录ID": str(raw_id),
            "字段结论": "；".join(
                f"{f['field']}={f['value'] or '空'}({f['state']})" for f in field_entries),
            "处置结果": decision,
            "下游可用": "是" if downstream_ready else "否",
            "task_id": task["id"],
            "task_no": task["task_no"],
            "raw_record_id": raw_id,
            "quality_conclusion_id": conclusion.get("id"),
            "source_id": conclusion.get("source_id", ""),
            "parser_version": conclusion.get("parser_version", ""),
            "historical_parser_version": conclusion.get("historical_parser_version", ""),
            "source_format": conclusion.get("source_format", ""),
            "classification": item.get("classification"),
            "verified": bool(item.get("verified")),
            "field_conclusions": field_entries,
            "missing_fields": [f["field"] for f in field_entries if f["missing"]],
            "decision": decision,
            "downstream_ready": downstream_ready,
            "downstream_scope": "已确认缺值" if downstream_ready else "不下发",
            "recovered_at": item.get("recovered_at", ""),
            "verified_at": conclusion.get("verified_at", ""),
            "written_at": _now_iso(),
            "written_by": operator,
        }
        store.rows("quality_disposition").append(entry)
        written.append(entry)
        item["disposition_id"] = entity_id
        task.setdefault("disposition_ids", []).append(entity_id)
    task["last_disposition_at"] = _now_iso()
    return {
        "ok": bool(written),
        "message": (f"已回写 {len(written)} 条处置结论到处置清单"
                    if written else "没有新的处置结论可回写"),
        "entries": written,
        "skipped_raw_ids": skipped,
        "task": task_detail(task_id),
    }


def list_dispositions() -> dict[str, Any]:
    rows = list(store.rows("quality_disposition"))
    return {"items": rows, "total": len(rows)}
