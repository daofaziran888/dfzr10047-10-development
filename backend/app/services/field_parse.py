"""字段解析领域服务：按命中模板逐字段抽取取值并记录解析质量，产出解析字段数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FieldParseService(BaseModuleService):
    MODULE = "field_parse"
    ENTITY = "解析字段"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["解析编号", "关联批次", "命中模板", "模板版本", "字段名称", "原始取值", "解析取值", "解析耗时", "解析行号"]
    REQUIRED_FIELDS = ["解析编号", "关联批次", "命中模板"]
    STATUS_ORDER = ["待解析", "解析中", "解析成功", "部分失败"]
    ACTION_RULES = {"启动解析": "解析中", "确认成功": "解析成功", "登记部分失败": "部分失败"}
    NEGATIVE_ACTIONS = ["登记部分失败"]

    def record_parsed_field(self, parse_no: str, intake_batch: str, template_no: str,
                            field_name: str, raw_value: str, parsed_value: str,
                            elapsed_ms: int, row_num: int,
                            success: bool, template_version: str = "",
                            expression: str = "") -> dict[str, Any]:
        """登记单字段解析结果，解析失败的字段连同行号可供坏行隔离消费。

        解析结果同时冻结当时的模板版本与表达式，历史字段不会被后续模板变更改写。
        """
        rows = store.rows(self.MODULE)
        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": entry_id,
            "status": "解析成功" if success else "部分失败",
            "pending": not success,
            "abnormal": not success,
            "解析编号": parse_no,
            "关联批次": intake_batch,
            "命中模板": template_no,
            "模板版本": template_version,
            "字段名称": field_name,
            "原始取值": raw_value,
            "解析取值": parsed_value,
            "解析耗时": str(elapsed_ms),
            "解析行号": str(row_num),
            "金额表达式": expression,
        }
        rows.append(entry)
        return entry

    # ---- 按批次采用记录解析 -------------------------------------------
    def resolve_adoption_for_batch(self, intake_batch: str) -> dict[str, Any] | None:
        """字段解析取模板的口径：批次采用记录优先，其次批次领取记录。

        返回 {模板编号, 模板版本, 金额表达式, 采用单号}；都没有时返回 None。
        """
        from app.services import service_for

        claim_svc = service_for("tmpl_claim")
        claim = claim_svc.claim_of_batch(intake_batch)
        if claim is not None and claim.get("模板版本"):
            return {
                "模板编号": claim.get("模板编号"),
                "模板版本": claim.get("模板版本"),
                "金额表达式": claim.get("金额表达式"),
                "采用单号": claim.get("采用单号"),
                "来源": "批次领取",
            }

        adoption_svc = service_for("tmpl_adopt")
        adoption = adoption_svc.adoption_of_batch(intake_batch)
        if adoption is not None and adoption.get("status") == "已采纳":
            return {
                "模板编号": adoption.get("模板编号"),
                "模板版本": adoption.get("派生版本"),
                "金额表达式": adoption.get("金额表达式"),
                "采用单号": adoption.get("采用单号"),
                "来源": "渠道采用单",
            }
        return None

    def historical_versions_of_batch(self, intake_batch: str) -> list[str]:
        """该批次历史解析实际用过的模板版本（保持登记顺序，供审计追溯）。"""
        seen: list[str] = []
        for row in store.rows(self.MODULE):
            if row.get("关联批次") != intake_batch:
                continue
            version = str(row.get("模板版本") or "")
            if version and version not in seen:
                seen.append(version)
        return seen

    def parse_fields_for_batch(
        self, parse_prefix: str, intake_batch: str,
        rows: list[dict[str, str]], start_row: int, parse_time: str,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """按批次采用记录逐行解析字段并冻结版本。

        同一批次已经用某个版本完成过解析时，拒绝换用另一个版本，
        避免把两个金额表达式的产物拼成同一次解析发布。
        """
        context = self.resolve_adoption_for_batch(intake_batch)
        if context is None:
            return False, (
                f"批次 {intake_batch} 没有已采纳的采用记录，不能启动字段解析"
            ), []

        version = str(context.get("模板版本") or "")
        existing = self.historical_versions_of_batch(intake_batch)
        if existing and version not in existing:
            return False, (
                f"批次 {intake_batch} 历史解析已使用模板版本 {existing}，"
                f"本次采用记录为 {version}，同一批次只能采用一致的模板数据，"
                f"不得混用两个金额表达式"
            ), []

        parsed: list[dict[str, Any]] = []
        for offset, raw in enumerate(rows):
            row_num = start_row + offset
            for field_name, raw_value in raw.items():
                entry = self.record_parsed_field(
                    f"{parse_prefix}-{row_num}-{field_name}", intake_batch,
                    str(context.get("模板编号") or ""), field_name,
                    str(raw_value), str(raw_value), 1, row_num, True,
                    template_version=version,
                    expression=str(context.get("金额表达式") or ""))
                parsed.append(entry)
        return True, (
            f"批次 {intake_batch} 按采用单 {context.get('采用单号')} 使用 "
            f"{context.get('模板编号')}[{version}] 解析 {len(rows)} 行，"
            f"金额表达式「{context.get('金额表达式')}」"
        ), parsed

    def failed_fields_of_batch(self, intake_batch: str) -> list[dict[str, Any]]:
        """输出解析失败字段清单（含行号），坏行隔离据此建隔离行。"""
        return [
            {
                "解析编号": r.get("解析编号"),
                "字段名称": r.get("字段名称"),
                "原始取值": r.get("原始取值"),
                "解析行号": r.get("解析行号"),
            }
            for r in store.rows(self.MODULE)
            if r.get("关联批次") == intake_batch and r.get("status") == "部分失败"
        ]

    def parsed_rows_of_batch(self, intake_batch: str) -> dict[str, dict[str, Any]]:
        """按行号聚合解析成功的取值，供抽样复核与输出契约取数。"""
        grouped: dict[str, dict[str, Any]] = {}
        for r in store.rows(self.MODULE):
            if r.get("关联批次") != intake_batch:
                continue
            line = str(r.get("解析行号") or "")
            if not line:
                continue
            bucket = grouped.setdefault(line, {})
            bucket[str(r.get("字段名称"))] = r.get("解析取值")
            bucket["__status"] = (
                "部分失败" if r.get("status") == "部分失败"
                else bucket.get("__status", "解析成功")
            )
        return grouped

    def success_rate(self, intake_batch: str) -> float:
        """批次字段级解析成功率，供解析指标交叉核对。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("关联批次") == intake_batch]
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.get("status") == "解析成功") / len(rows)

    def template_hit_stats(self) -> dict[str, int]:
        """按命中模板统计解析字段数，供模板版本过渡期命中分布核对。"""
        stats: dict[str, int] = {}
        for r in store.rows(self.MODULE):
            key = str(r.get("命中模板") or "")
            if key:
                stats[key] = stats.get(key, 0) + 1
        return stats
