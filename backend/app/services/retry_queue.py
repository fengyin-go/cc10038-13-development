"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RetryQueueService(BaseModuleService):
    MODULE = "retry_queue"
    ENTITY = "重试任务"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["失败记录ID", "失败原因", "重试次数", "最大重试", "下次重试时间"]
    REQUIRED_FIELDS = ["失败记录ID", "失败原因", "重试次数"]
    STATUS_ORDER = ["待重试", "重试中", "超过上限"]
    ACTION_RULES = {"立即重试": "重试中", "放弃重试": "超过上限"}
    NEGATIVE_ACTIONS = []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """缺值补采任务的重试入口：按任务提交时的范围执行，不重新挑选原始行。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("source") != "missing_value_view":
            return super().run_action(entry_id, action)
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        if action == "放弃重试":
            entry["status"] = "超过上限"
            entry["pending"] = False
            return entry, f"重试任务 {entry_id} 已放弃补采"

        from app.services import missing_value

        result = missing_value.retry_task(int(entry.get("task_id", 0)))
        if not result.get("ok"):
            entry["status"] = "超过上限"
            entry["pending"] = False
            return None, result.get("message", "补采重试失败")
        task = result.get("task") or {}
        entry["重试次数"] = str(task.get("attempts", entry.get("重试次数", "0")))
        entry["下次重试时间"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry["补采任务"] = task.get("task_no", "")
        # 补采结果由 missing_value 按原始行粒度同步（已补采 / 无需补采 / 待核对）。
        entry["补采任务状态"] = task.get("status", "")
        # 状态枚举保持模块既有口径：补采完成后任务不再排队等待。
        entry["status"] = "重试中"
        entry["pending"] = task.get("status") != "已完成"
        return entry, result.get("message", "补采任务已按原范围重试")
