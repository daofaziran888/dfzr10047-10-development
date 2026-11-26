"""解析批次领域服务：汇聚每次解析的行级统计与闭合判定，产出批次记录数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ParseBatchService(BaseModuleService):
    MODULE = "parse_batch"
    ENTITY = "批次记录"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["批次记录编号", "关联投递批次", "解析器标识", "总行数", "成功行数", "隔离行数", "批次状态", "完成时间"]
    REQUIRED_FIELDS = ["批次记录编号", "关联投递批次", "解析器标识"]
    STATUS_ORDER = ["解析中", "已闭合", "存在隔离", "已作废"]
    ACTION_RULES = {"闭合批次": "已闭合", "登记隔离": "存在隔离", "作废批次": "已作废"}
    NEGATIVE_ACTIONS = ["作废批次"]

    @staticmethod
    def _int(row: dict[str, Any], field: str) -> int:
        text = str(row.get(field) or "").strip()
        return int(text) if text.isdigit() else 0

    def _find_by_intake(self, intake_batch: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("关联投递批次") == intake_batch:
                return row
        return None

    def tally_from_field_parse(self, intake_batch: str) -> dict[str, int]:
        """从字段解析明细聚合行级统计：成功行数与部分失败行数。"""
        parsed = store.rows("field_parse")
        rows_of_batch = [r for r in parsed if r.get("关联批次") == intake_batch]
        row_numbers: dict[str, bool] = {}
        for r in rows_of_batch:
            line = str(r.get("解析行号") or "")
            if not line:
                continue
            ok = r.get("status") == "解析成功"
            row_numbers[line] = row_numbers.get(line, True) and ok
        success = sum(1 for ok in row_numbers.values() if ok)
        failed = sum(1 for ok in row_numbers.values() if not ok)
        return {"total_rows": len(row_numbers), "success_rows": success, "failed_rows": failed}

    def tally_from_quarantine(self, intake_batch: str) -> dict[str, int]:
        """从坏行隔离明细统计真实隔离行数与已回补行数。"""
        rows = [
            r for r in store.rows("row_quarantine")
            if r.get("关联批次") == intake_batch
        ]
        return {
            "quarantined": len(rows),
            "reinstated": sum(1 for r in rows if r.get("status") == "已回补"),
            "discarded": sum(1 for r in rows if r.get("status") == "已废弃"),
            "unreviewed": sum(1 for r in rows if r.get("status") in ("已隔离", "复核中")),
        }

    def template_versions_of_batch(self, intake_batch: str) -> list[str]:
        """批次发布涉及的模板版本集合：批次记录、批次领取、字段解析三处交叉核对。"""
        versions: set[str] = set()
        batch = self._find_by_intake(intake_batch)
        if batch is not None and batch.get("模板版本"):
            versions.add(str(batch.get("模板版本")))
        for claim in store.rows("tmpl_claim"):
            if (str(claim.get("关联批次")) == intake_batch
                    and claim.get("status") in ("已领取", "已采纳")
                    and claim.get("模板版本")):
                versions.add(str(claim.get("模板版本")))
        for field in store.rows("field_parse"):
            if (str(field.get("关联批次")) == intake_batch
                    and field.get("模板版本")
                    and field.get("status") in ("解析成功", "部分失败", "解析中")):
                versions.add(str(field.get("模板版本")))
        return sorted(versions)

    def assert_single_template(self, intake_batch: str) -> tuple[bool, str]:
        """同一批次发布只能采用一致的模板数据，不能混用两版表达式产物。"""
        versions = self.template_versions_of_batch(intake_batch)
        if len(versions) > 1:
            return False, (
                f"批次 {intake_batch} 同时出现模板版本 {versions}，"
                f"同一批次发布只能采用一致的模板数据，不得把两个表达式的产物"
                f"拼成同一次解析"
            )
        return True, (
            f"批次 {intake_batch} 模板版本一致：{versions[0]}" if versions
            else f"批次 {intake_batch} 尚未登记模板版本"
        )

    def refresh_batch_counts(self, intake_batch: str) -> tuple[bool, str, dict[str, Any] | None]:
        """按字段解析与坏行隔离的真实计数刷新批次行数，不允许人工填数。"""
        batch = self._find_by_intake(intake_batch)
        if batch is None:
            return False, f"投递批次 {intake_batch} 没有对应批次记录", None
        if batch.get("status") == "已作废":
            return False, f"批次记录 {batch.get('批次记录编号')} 已作废，不再刷新计数", None

        parse_tally = self.tally_from_field_parse(intake_batch)
        quarantine_tally = self.tally_from_quarantine(intake_batch)
        quarantined = quarantine_tally["quarantined"] - quarantine_tally["reinstated"]
        success = parse_tally["success_rows"] + quarantine_tally["reinstated"]

        batch["总行数"] = str(parse_tally["total_rows"])
        batch["成功行数"] = str(success)
        batch["隔离行数"] = str(max(0, quarantined))
        return True, (
            f"批次 {intake_batch} 计数已按真实明细刷新："
            f"总 {parse_tally['total_rows']} / 成功 {success} / 隔离 {max(0, quarantined)}"
        ), dict(batch)

    def close_batch(self, intake_batch: str, close_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """闭合批次：存在未复核隔离行时不得闭合，计数必须先与明细一致。"""
        batch = self._find_by_intake(intake_batch)
        if batch is None:
            return False, f"投递批次 {intake_batch} 没有对应批次记录", None
        if batch.get("status") == "已闭合":
            return False, f"批次 {intake_batch} 已闭合，不能重复闭合", None
        if batch.get("status") == "已作废":
            return False, f"批次 {intake_batch} 已作废，不能闭合", None

        quarantine_tally = self.tally_from_quarantine(intake_batch)
        if quarantine_tally["unreviewed"] > 0:
            batch["批次状态"] = "存在隔离"
            batch["status"] = "存在隔离"
            batch["pending"] = True
            batch["abnormal"] = True
            return False, (
                f"批次 {intake_batch} 仍有 {quarantine_tally['unreviewed']} 行隔离未复核，"
                f"不得闭合"
            ), dict(batch)

        ok, _msg, _row = self.refresh_batch_counts(intake_batch)
        total = self._int(batch, "总行数")
        success = self._int(batch, "成功行数")
        if total > 0 and success == 0:
            batch["批次状态"] = "存在隔离"
            batch["status"] = "存在隔离"
            batch["pending"] = True
            batch["abnormal"] = True
            return False, f"批次 {intake_batch} 成功行数为 0，不得闭合", dict(batch)

        batch["批次状态"] = "已闭合"
        batch["status"] = "已闭合"
        batch["完成时间"] = close_time
        batch["pending"] = False
        batch["abnormal"] = False
        return True, f"批次 {intake_batch} 已闭合，成功 {success}/{total} 行", dict(batch)

    def invalidate_batch(self, intake_batch: str, reason: str) -> tuple[bool, str]:
        """作废批次：格式无法识别或路由失败时使用，作废后不得再进入契约。"""
        batch = self._find_by_intake(intake_batch)
        if batch is None:
            return False, f"投递批次 {intake_batch} 没有对应批次记录"
        if batch.get("status") == "已作废":
            return False, f"批次 {intake_batch} 已作废"
        batch["批次状态"] = "已作废"
        batch["status"] = "已作废"
        batch["pending"] = False
        batch["abnormal"] = True
        batch["作废原因"] = reason
        return True, f"批次 {intake_batch} 已作废：{reason}"

    def open_batches(self) -> list[dict[str, Any]]:
        """列出全部未闭合批次，供水位推进前置校验消费。"""
        return [
            {
                "批次记录编号": r.get("批次记录编号"),
                "关联投递批次": r.get("关联投递批次"),
                "批次状态": r.get("批次状态"),
                "隔离行数": r.get("隔离行数"),
            }
            for r in store.rows(self.MODULE)
            if r.get("批次状态") not in ("已闭合", "已作废", "闭合")
        ]

    def closed_ratio(self) -> float:
        """批次闭合率：已闭合 / 非作废总数。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("批次状态") != "已作废"]
        if not rows:
            return 0.0
        closed = sum(1 for r in rows if r.get("批次状态") in ("已闭合", "闭合"))
        return closed / len(rows)
