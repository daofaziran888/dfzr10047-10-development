"""重解析任务领域服务：按位点择定覆盖策略并编排历史批次重解析流程，产出重解析单数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ReparseTaskService(BaseModuleService):
    MODULE = "reparse_task"
    ENTITY = "重解析单"
    OPERATION_TERM = "回填"
    LIST_FIELDS = ["重解析编号", "关联批次", "重解析原因", "覆盖策略", "起始位点", "截止位点", "受影响投递数", "重解析结论"]
    REQUIRED_FIELDS = ["重解析编号", "关联批次", "重解析原因"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "被中止"]
    ACTION_RULES = {"启动重解析": "执行中", "确认完成": "已完成", "中止任务": "被中止"}
    NEGATIVE_ACTIONS = ["中止任务"]

    KEEP_OLD = "旧投递保留"
    REPLACE_OLD = "被新记录替代"
    STRATEGIES = (KEEP_OLD, REPLACE_OLD)

    def _find(self, task_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("重解析编号") == task_no:
                return row
        return None

    def plan_position_range(self, task_no: str, reason: str,
                            coverage_strategy: str, affected_batches: list[str],
                            batch_positions: dict[str, str]) -> tuple[bool, str, dict[str, Any] | None]:
        """按位点规划重解析范围：位点取受影响批次的最小/最大值。

        覆盖策略必须是已批准枚举；受影响批次为空时不建单。
        """
        if coverage_strategy not in self.STRATEGIES:
            return False, (
                f"覆盖策略「{coverage_strategy}」不在允许范围 {list(self.STRATEGIES)}"
            ), None
        if not affected_batches:
            return False, "受影响批次为空，无需创建重解析单", None

        positioned = {b: batch_positions[b] for b in affected_batches if b in batch_positions}
        if not positioned:
            return False, (
                f"受影响批次 {affected_batches[:5]} 均无位点信息，无法确定重解析范围"
            ), None

        ordered = sorted(positioned.items(), key=lambda kv: kv[1])
        start_position = ordered[0][1]
        end_position = ordered[-1][1]
        if start_position > end_position:
            return False, f"起始位点 {start_position} 晚于截止位点 {end_position}", None

        rows = store.rows(self.MODULE)
        if any(r.get("重解析编号") == task_no for r in rows):
            return False, f"重解析单 {task_no} 已存在，不得重复创建", None

        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": entry_id, "status": "待执行", "pending": True, "abnormal": False,
            "重解析编号": task_no,
            "关联批次": ordered[0][0],
            "重解析原因": reason,
            "覆盖策略": coverage_strategy,
            "起始位点": start_position,
            "截止位点": end_position,
            "受影响投递数": str(len(ordered)),
            "受影响批次": ",".join(b for b, _ in ordered),
            "重解析结论": "",
        }
        rows.append(entry)
        return True, (
            f"重解析单 {task_no} 已建单：范围 [{start_position}, {end_position}] "
            f"覆盖 {len(ordered)} 个批次，覆盖策略「{coverage_strategy}」"
        ), dict(entry)

    def batches_in_range(self, start_position: str, end_position: str) -> list[str]:
        """列出位点区间内的投递批次，供重解析执行范围核对。"""
        matched: list[str] = []
        for row in store.rows("file_intake"):
            batch_no = str(row.get("批次编号") or "")
            deliver_time = str(row.get("投递时间") or "")
            if not deliver_time:
                continue
            if start_position <= deliver_time <= end_position:
                matched.append(batch_no)
        return sorted(matched)

    def start_task(self, task_no: str) -> tuple[bool, str]:
        """启动重解析：已完成/已中止的单不得重新执行。"""
        task = self._find(task_no)
        if task is None:
            return False, f"重解析单 {task_no} 不存在"
        if task.get("status") == "已完成":
            return False, f"重解析单 {task_no} 已完成，不能重新启动"
        if task.get("status") == "被中止":
            return False, f"重解析单 {task_no} 已被中止，不能重新启动"
        task["status"] = "执行中"
        task["pending"] = True
        task["abnormal"] = False
        return True, f"重解析单 {task_no} 已开始执行"

    def complete_task(self, task_no: str, reparsed_count: int,
                      conclusion: str) -> tuple[bool, str, dict[str, Any] | None]:
        """确认完成：按覆盖策略处置旧投递记录，两种策略数据终态不同。"""
        task = self._find(task_no)
        if task is None:
            return False, f"重解析单 {task_no} 不存在", None
        if task.get("status") == "已完成":
            return False, f"重解析单 {task_no} 已完成，不能重复确认", dict(task)
        if task.get("status") == "被中止":
            return False, f"重解析单 {task_no} 已被中止，不能确认完成", dict(task)

        strategy = str(task.get("覆盖策略") or "")
        affected = [
            b.strip() for b in str(task.get("受影响批次") or "").split(",") if b.strip()
        ]
        handled = self._apply_coverage_strategy(strategy, affected, task_no)

        task["status"] = "已完成"
        task["pending"] = False
        task["abnormal"] = False
        task["受影响投递数"] = str(reparsed_count)
        task["重解析结论"] = f"{conclusion}；{handled}"
        return True, (
            f"重解析单 {task_no} 已完成，重解析 {reparsed_count} 个批次，{handled}"
        ), dict(task)

    def _deliveries_of_batch(self, intake_batch: str) -> list[dict[str, Any]]:
        """沿真实引用链找出投递批次对应的全部投递记录。

        链路：投递批次 → 解析批次(关联投递批次) → 输出契约(关联批次=批次记录编号)
              → 投递记录(关联契约)。
        """
        batch_nos = {
            str(b.get("批次记录编号")) for b in store.rows("parse_batch")
            if str(b.get("关联投递批次")) == intake_batch
        }
        contract_nos = {
            str(c.get("契约编号")) for c in store.rows("output_contract")
            if str(c.get("关联批次")) in batch_nos
        }
        return [
            d for d in store.rows("result_deliver")
            if str(d.get("关联契约")) in contract_nos
        ]

    def affected_deliveries(self, intake_batch: str) -> list[str]:
        """输出重解析会影响到的投递记录编号，供受影响投递数核对。"""
        return [str(d.get("投递记录编号")) for d in self._deliveries_of_batch(intake_batch)]

    def _apply_coverage_strategy(self, strategy: str, affected: list[str],
                                 task_no: str) -> str:
        """执行覆盖策略对旧投递记录的处置。

        旧投递保留：旧记录状态不变，仅追加归档标记，下游可同时看到新旧两版。
        被新记录替代：旧记录标记为已作废，下游只看到新版投递。
        """
        touched = 0
        for batch_no in affected:
            for record in self._deliveries_of_batch(batch_no):
                touched += 1
                if strategy == self.KEEP_OLD:
                    record["归档标记"] = f"由 {task_no} 重解析产生新版本，旧投递保留可查"
                    record["新版本任务"] = task_no
                elif strategy == self.REPLACE_OLD:
                    record["status"] = "已作废"
                    record["pending"] = False
                    record["abnormal"] = True
                    record["投递结论"] = (
                        f"被 {task_no} 重解析结果替代，旧投递记录作废"
                    )
                    record["替代任务"] = task_no
        if strategy == self.KEEP_OLD:
            return f"旧投递保留：{touched} 条投递记录追加归档标记，新旧版本并存"
        if strategy == self.REPLACE_OLD:
            return f"被新记录替代：{touched} 条旧投递记录已作废，下游只读新版本"
        return "未识别的覆盖策略，未处置旧投递记录"

    def abort_task(self, task_no: str, reason: str) -> tuple[bool, str]:
        """中止任务：已完成的单不能中止，旧投递记录保持原状。"""
        task = self._find(task_no)
        if task is None:
            return False, f"重解析单 {task_no} 不存在"
        if task.get("status") == "已完成":
            return False, f"重解析单 {task_no} 已完成，不能中止"
        if task.get("status") == "被中止":
            return False, f"重解析单 {task_no} 已处于中止状态"
        task["status"] = "被中止"
        task["pending"] = False
        task["abnormal"] = True
        task["重解析结论"] = f"中止：{reason}"
        return True, f"重解析单 {task_no} 已中止：{reason}"

    def pending_tasks(self) -> list[dict[str, Any]]:
        """列出待执行/执行中的重解析单，供调度看板消费。"""
        return [
            {
                "重解析编号": r.get("重解析编号"),
                "关联批次": r.get("关联批次"),
                "覆盖策略": r.get("覆盖策略"),
                "起始位点": r.get("起始位点"),
                "截止位点": r.get("截止位点"),
                "status": r.get("status"),
            }
            for r in store.rows(self.MODULE)
            if r.get("status") in ("待执行", "执行中")
        ]

    # ---- 按真实消费版本与字段差异定位受影响批次 -----------------------
    def batches_consumed_version(self, template_no: str,
                                 version: str) -> list[str]:
        """真正用该模板版本解析过的批次：字段解析明细里冻结的版本才算真实消费。"""
        consumed: list[str] = []
        for field in store.rows("field_parse"):
            if (field.get("命中模板") == template_no
                    and field.get("模板版本") == version
                    and field.get("关联批次")):
                batch = str(field.get("关联批次"))
                if batch not in consumed:
                    consumed.append(batch)
        return sorted(consumed)

    def affected_by_diff(self, diff_no: str) -> list[dict[str, Any]]:
        """按差异条目的来源批次定位受影响批次，并给出各批次的真实消费版本。

        重解析申请以当次真实消费版本和字段差异定位受影响批次：
        只把确实消费了旧版本的批次纳入重解析范围，避免误伤未触碰批次的旧输出。
        """
        from app.services import service_for

        diff_svc = service_for("tmpl_diff")
        claim_svc = service_for("tmpl_claim")
        diff = next(
            (r for r in store.rows("tmpl_diff") if r.get("差异编号") == diff_no),
            None)
        if diff is None:
            return []

        source_batches = diff_svc.source_batches_of(diff_no)
        adoptions = {
            str(a.get("关联批次")): a
            for a in diff_svc.adoptions_of_diff(diff_no)
        }
        affected: list[dict[str, Any]] = []
        for batch_no in source_batches:
            claim = claim_svc.claim_of_batch(batch_no)
            consumed = (
                str(claim.get("模板版本")) if claim is not None else ""
            )
            if not consumed:
                parsed = [
                    r for r in store.rows("field_parse")
                    if str(r.get("关联批次")) == batch_no and r.get("模板版本")
                ]
                if parsed:
                    consumed = str(parsed[0].get("模板版本"))
            adoption = adoptions.get(batch_no, {})
            affected.append({
                "关联批次": batch_no,
                "采用单号": adoption.get("采用单号", ""),
                "采用渠道": adoption.get("采用渠道", ""),
                "真实消费版本": consumed,
                "模板编号": (
                    str(claim.get("模板编号")) if claim is not None
                    else str(adoption.get("模板编号") or "")
                ),
                "金额表达式": (
                    str(claim.get("金额表达式")) if claim is not None
                    else str(adoption.get("金额表达式") or "")
                ),
                "差异字段": diff.get("差异字段", ""),
            })
        return affected

    def plan_from_adoption(self, task_no: str, diff_no: str,
                           coverage_strategy: str,
                           batch_positions: dict[str, str]) -> tuple[bool, str, dict[str, Any] | None]:
        """以差异条目定位受影响批次并按真实消费版本建重解析单。"""
        affected = self.affected_by_diff(diff_no)
        if not affected:
            return False, (
                f"差异 {diff_no} 没有可追溯的来源批次，无需建重解析单"
            ), None
        batch_nos = [item["关联批次"] for item in affected]
        ok, message, task = self.plan_position_range(
            task_no, f"模板差异 {diff_no} 重解析",
            coverage_strategy, batch_nos, batch_positions)
        if not ok or task is None:
            return ok, message, task
        task["差异编号"] = diff_no
        task["真实消费版本"] = ",".join(
            f"{item['关联批次']}:{item['真实消费版本'] or '未登记'}"
            for item in affected
        )
        task["差异字段"] = affected[0].get("差异字段", "")
        task["受影响批次"] = ",".join(batch_nos)
        return True, (
            f"{message}；真实消费版本 {task['真实消费版本']}，"
            f"差异字段 {task['差异字段'] or '无'}"
        ), task
