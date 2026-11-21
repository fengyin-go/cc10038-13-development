"""缺值分布视图与补采流程业务测试。

覆盖：字段级缺值口径按历史解析版本判定、新旧格式混批、格式不明的待核对行、
从视图提交补采、未核实范围不得作为已确认缺值下发、中断后按原范围重试、
已补采行不重复入队、核对结论回写、重新解析与处置清单回写。
"""
from __future__ import annotations

import json

from app.services import missing_value
from app.store import store


BATCH = "INGE-1001"
NEW_FORMAT_RAW = 4      # 新格式，缺 host
OLD_FORMAT_RAW = 5      # 旧格式，host 可空
UNKNOWN_FORMAT_RAW = 6  # 格式不明
QUEUED_RAW = 7          # 历史巡检已确认缺值，已在 RECL-0001
MISSING_MESSAGE_RAW = 8  # 新格式，缺 message


def _view(**kwargs):
    return missing_value.missing_value_view(batch_no=BATCH, **kwargs)


def _row(view, raw_id):
    return next(row for row in view["rows"] if row["raw_record_id"] == raw_id)


class TestMissingValueView:
    def test_view_groups_by_source_and_parser_version(self):
        view = _view()
        assert len(view["groups"]) == 1
        group = view["groups"][0]
        assert group["source_id"] == "SRC-MIXED-APP"
        versions = {version["parser_version"] for version in group["versions"]}
        assert {"app-log-old-v1", "app-log-new-v2"} <= versions

    def test_new_format_missing_host_is_confirmed(self):
        row = _row(_view(), NEW_FORMAT_RAW)
        assert row["source_format"] == "new"
        assert row["bucket"] == "已确认缺值"
        assert row["missing_fields"] == ["host"]
        assert row["selectable"] is True

    def test_old_format_host_is_not_missing(self):
        row = _row(_view(), OLD_FORMAT_RAW)
        assert row["source_format"] == "old"
        assert row["bucket"] == "无需补采"
        assert row["missing_fields"] == []
        assert "host" in row["optional_fields"]
        assert row["selectable"] is False

    def test_unknown_format_is_pending_and_not_selectable_as_confirmed(self):
        row = _row(_view(), UNKNOWN_FORMAT_RAW)
        assert row["source_format"] == "unknown"
        assert row["bucket"] == "待核对"
        assert row["parser_version_confirmed"] is False
        assert row["selectable_scope"] == "待核对"
        assert row["candidate_fields"] == ["host"]

    def test_field_stats_split_confirmed_and_candidate(self):
        stats = {item["field"]: item for item in _view()["field_stats"]}
        assert stats["host"]["confirmed"] == 2  # 行 4、7
        assert stats["host"]["candidate"] == 1  # 行 6
        assert stats["message"]["confirmed"] == 1  # 行 8

    def test_bucket_filter(self):
        pending = _view(bucket="待核对")
        assert [row["raw_record_id"] for row in pending["rows"]] == [UNKNOWN_FORMAT_RAW]
        assert pending["buckets"]["已确认缺值"] == 3


class TestRecollectSubmission:
    def test_confirmed_scope_rejects_unverified_row(self):
        result = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW, UNKNOWN_FORMAT_RAW],
            "scope": "confirmed",
        })
        assert result["ok"] is False
        assert result["rejected"][0]["raw_record_id"] == str(UNKNOWN_FORMAT_RAW)
        assert "未核实" in result["rejected"][0]["reason"]
        # 整单不受理，避免半截写入
        assert len(store.rows("recollect_task")) == 1

    def test_pending_scope_accepts_unverified_row_but_marks_not_downstream(self):
        result = missing_value.submit_recollect({
            "record_ids": [UNKNOWN_FORMAT_RAW], "scope": "pending",
        })
        assert result["ok"] is True
        task = result["task"]
        assert task["quality_state"] == "未核实"
        assert task["downstream_allowed"] is False
        assert task["items"][0]["classification"] == "pending"
        assert task["items"][0]["verified"] is False

    def test_submitted_task_links_quality_conclusion(self):
        result = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW], "scope": "confirmed",
        })
        item = result["task"]["items"][0]
        assert item["quality_conclusion_id"] == 1
        assert item["conclusion_summary"]
        assert item["historical_parser_version"] is not None

    def test_already_queued_row_is_rejected(self):
        result = missing_value.submit_recollect({
            "record_ids": [QUEUED_RAW], "scope": "confirmed",
        })
        assert result["ok"] is False
        assert "已在补采任务" in result["rejected"][0]["reason"]

    def test_no_duplicate_record_ids(self):
        result = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW, NEW_FORMAT_RAW], "scope": "confirmed",
        })
        assert result["ok"] is False


class TestInterruptedRetry:
    def test_retry_keeps_original_scope_and_skips_recovered_rows(self):
        task = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW, MISSING_MESSAGE_RAW], "scope": "confirmed",
        })["task"]
        first = missing_value.run_task(task["id"], interrupt_after=1)
        assert first["interrupted"] is True
        assert first["processed"] == [NEW_FORMAT_RAW]
        assert first["task"]["status"] == "中断"
        assert first["task"]["scope"] == [NEW_FORMAT_RAW, MISSING_MESSAGE_RAW]

        retry = missing_value.retry_task(task["id"])
        assert retry["ok"] is True
        assert retry["scope"] == [NEW_FORMAT_RAW, MISSING_MESSAGE_RAW]
        assert retry["already_recovered"] == [NEW_FORMAT_RAW]
        assert retry["processed"] == [MISSING_MESSAGE_RAW]
        assert retry["task"]["status"] == "已完成"
        assert retry["task"]["attempts"] == 1

    def test_completed_task_cannot_retry(self):
        task = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW], "scope": "confirmed",
        })["task"]
        missing_value.run_task(task["id"])
        result = missing_value.retry_task(task["id"])
        assert result["ok"] is False
        assert "已完成" in result["message"]

    def test_reparse_creates_new_entities_and_keeps_old_version(self):
        task = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW], "scope": "confirmed",
        })["task"]
        missing_value.run_task(task["id"])
        old = next(e for e in store.rows("parsed_entity") if e["原始记录ID"] == str(NEW_FORMAT_RAW))
        old_fields = json.loads(old["标准化字段集"])

        result = missing_value.reparse_recovered(task["id"])
        assert result["ok"] is True
        assert len(result["created_entity_ids"]) == 1
        new_entity = store.find("parsed_entity", result["created_entity_ids"][0])
        fields = json.loads(new_entity["标准化字段集"])
        assert fields["host"] == f"host-{NEW_FORMAT_RAW:02d}.recovered"
        # 历史解析版本和字段口径保持原样
        assert old["解析规则版本"] == "app-log-new-v2"
        assert json.loads(old["标准化字段集"]) == old_fields
        assert new_entity["reparsed_from_entity_id"] == old["id"]

        again = missing_value.reparse_recovered(task["id"])
        assert again["created_entity_ids"] == []


class TestVerification:
    def test_unknown_row_stays_pending_until_verified(self):
        task = missing_value.submit_recollect({
            "record_ids": [UNKNOWN_FORMAT_RAW], "scope": "pending",
        })["task"]
        run = missing_value.run_task(task["id"])
        assert run["processed"] == []
        assert run["awaiting_review"] == [UNKNOWN_FORMAT_RAW]
        assert run["task"]["status"] == "待核对"

    def test_verify_old_format_marks_not_required(self):
        task = missing_value.submit_recollect({
            "record_ids": [UNKNOWN_FORMAT_RAW], "scope": "pending",
        })["task"]
        missing_value.run_task(task["id"])
        result = missing_value.verify_items(task["id"], [
            {"raw_record_id": UNKNOWN_FORMAT_RAW, "decision": "old_format"},
        ])
        assert result["ok"] is True
        item = result["task"]["items"][0]
        assert item["classification"] == "not_required"
        conclusion = store.find("quality_conclusion", 3)
        assert conclusion["status"] == "无缺值"
        assert conclusion["historical_parser_version"] == "app-log-old-v1"
        # 不再需要补采，也不允许按已确认范围提交
        row = _row(_view(), UNKNOWN_FORMAT_RAW)
        assert row["bucket"] == "无需补采"

    def test_verify_new_format_promotes_to_confirmed_missing(self):
        task = missing_value.submit_recollect({
            "record_ids": [UNKNOWN_FORMAT_RAW], "scope": "pending",
        })["task"]
        missing_value.run_task(task["id"])
        result = missing_value.verify_items(task["id"], [
            {"raw_record_id": UNKNOWN_FORMAT_RAW, "decision": "new_format",
             "parser_version": "app-log-new-v2"},
        ])
        item = result["task"]["items"][0]
        assert item["classification"] == "confirmed"
        assert item["verified"] is True
        assert item["missing_fields"] == ["host"]
        assert item["historical_parser_version"] == "app-log-old-v1"
        conclusion = store.find("quality_conclusion", 3)
        assert conclusion["status"] == "已确认"
        assert conclusion["required_fields"] == ["time", "level", "message", "host"]

        run = missing_value.run_task(task["id"])
        assert run["processed"] == [UNKNOWN_FORMAT_RAW]
        assert run["task"]["items"][0]["recovered_fields"] == {
            "host": f"host-{UNKNOWN_FORMAT_RAW:02d}.recovered",
        }

    def test_cannot_confirm_keeps_pending(self):
        task = missing_value.submit_recollect({
            "record_ids": [UNKNOWN_FORMAT_RAW], "scope": "pending",
        })["task"]
        missing_value.run_task(task["id"])
        result = missing_value.verify_items(task["id"], [
            {"raw_record_id": UNKNOWN_FORMAT_RAW, "decision": "cannot_confirm"},
        ])
        assert result["task"]["items"][0]["status"] == "待核对"
        assert store.find("quality_conclusion", 3)["status"] == "待核对"


class TestDisposition:
    def test_disposition_only_downstreams_verified_recovered_rows(self):
        confirmed = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW], "scope": "confirmed",
        })["task"]
        pending = missing_value.submit_recollect({
            "record_ids": [UNKNOWN_FORMAT_RAW], "scope": "pending",
        })["task"]
        missing_value.run_task(confirmed["id"])
        missing_value.run_task(pending["id"])

        good = missing_value.write_disposition(confirmed["id"])
        assert good["entries"][0]["downstream_ready"] is True
        assert good["entries"][0]["downstream_scope"] == "已确认缺值"

        blocked = missing_value.write_disposition(pending["id"])
        entry = blocked["entries"][0]
        assert entry["downstream_ready"] is False
        assert entry["downstream_scope"] == "不下发"
        assert "未核实" in entry["decision"]

    def test_disposition_is_idempotent(self):
        task = missing_value.submit_recollect({
            "record_ids": [NEW_FORMAT_RAW], "scope": "confirmed",
        })["task"]
        missing_value.run_task(task["id"])
        first = missing_value.write_disposition(task["id"])
        second = missing_value.write_disposition(task["id"])
        assert len(first["entries"]) == 1
        assert second["entries"] == []
        assert second["skipped_raw_ids"] == [NEW_FORMAT_RAW]


class TestRetryQueueIntegration:
    def test_retry_queue_entry_uses_task_scope_and_stops_queueing(self):
        from app.services import SERVICES

        retry = next(row for row in store.rows("retry_queue")
                     if row.get("quality_conclusion_id") == 4)
        assert retry["status"] == "待重试"

        entry, message = SERVICES["retry_queue"].run_action(retry["id"], "立即重试")
        assert entry is not None
        assert "已按原提交范围重试" in message
        assert entry["pending"] is False
        assert entry["补采结果"] == "已补采"

        task = store.find("recollect_task", retry["task_id"])
        assert task["status"] == "已完成"
        assert task["scope_snapshot_ids"] == [7]

        # 补采完成后视图不再把该行列为待补采，也不会再次入队
        result = missing_value.submit_recollect({"record_ids": [7], "scope": "confirmed"})
        assert result["ok"] is False
        assert "已补采" in result["rejected"][0]["reason"]

    def test_generic_retry_entry_still_uses_default_flow(self):
        from app.services import SERVICES

        entry, message = SERVICES["retry_queue"].run_action(1, "立即重试")
        assert entry["status"] == "重试中"
        assert "重试任务已立即重试" == message
