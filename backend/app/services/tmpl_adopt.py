"""渠道采用单领域服务：按渠道申请采用派生模板、核对权限与批次范围，产出渠道采用单数据集。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TmplAdoptService(BaseModuleService):
    MODULE = "tmpl_adopt"
    ENTITY = "渠道采用单"
    OPERATION_TERM = "采用"
    LIST_FIELDS = [
        "采用单号", "关联批次", "采用渠道", "模板编号", "来源模板",
        "派生版本", "金额表达式", "差异编号", "校验结论", "申请时间",
    ]
    REQUIRED_FIELDS = ["采用渠道", "模板编号", "关联批次"]
    STATUS_ORDER = ["待复核", "复核中", "已采纳", "已拒绝"]
    ACTION_RULES = {"发起复核": "复核中", "确认采用": "已采纳", "驳回采用": "已拒绝"}
    NEGATIVE_ACTIONS = ["驳回采用"]

    # ---- 查询 ---------------------------------------------------------
    def _find(self, adoption_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("采用单号") == adoption_no:
                return row
        return None

    def _next_no(self) -> str:
        used = {
            str(r.get("采用单号") or "")
            for r in store.rows(self.MODULE)
        }
        index = 1
        while f"AD-{index:03d}" in used:
            index += 1
        return f"AD-{index:03d}"

    def adoption_of_batch(self, batch_no: str) -> dict[str, Any] | None:
        """批次最近一张渠道采用单。"""
        candidates = [
            r for r in store.rows(self.MODULE)
            if str(r.get("关联批次")) == str(batch_no)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.get("申请时间") or "", r.get("id", 0)))

    def adoptions_of_diff(self, diff_no: str) -> list[dict[str, Any]]:
        return [
            r for r in store.rows(self.MODULE)
            if str(r.get("差异编号")) == str(diff_no)
        ]

    def adoptions_of_channel(self, channel: str) -> list[dict[str, Any]]:
        return [
            r for r in store.rows(self.MODULE)
            if str(r.get("采用渠道")) == str(channel)
        ]

    # ---- 表单提交 -----------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """抽取模板页的采用表单入口：核对权限、批次范围后生成渠道采用单。"""
        adoption_no = str(values.get("采用单号") or "").strip() or self._next_no()
        ok, message, entry = self.submit_adoption(
            adoption_no=adoption_no,
            channel=str(values.get("采用渠道") or "").strip(),
            template_no=str(values.get("模板编号") or "").strip(),
            batch_no=str(values.get("关联批次") or "").strip(),
            apply_time=str(values.get("申请时间") or "").strip() or _now(),
            applicant=str(values.get("申请人") or "").strip(),
        )
        if not ok:
            return None, [message]
        return entry, []

    def submit_adoption(self, adoption_no: str, channel: str, template_no: str,
                        batch_no: str, apply_time: str,
                        applicant: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """申请采用派生模板：服务端核对有效权限和批次范围后才落采用单。

        拒绝原因区分越权、未授权、已撤回、超范围与同批混用；
        通过时返回渠道采用单，差异编号供模板差异页发起复核。
        """
        from app.services import service_for

        if not channel or not template_no or not batch_no:
            return False, "采用渠道、模板编号与关联批次均为必填", None
        if any(r.get("采用单号") == adoption_no for r in store.rows(self.MODULE)):
            return False, f"采用单 {adoption_no} 已存在，不得重复提交", None

        template = next(
            (r for r in store.rows("extract_tmpl") if r.get("模板编号") == template_no),
            None)
        if template is None:
            return False, f"模板 {template_no} 不存在，无法申请采用", None
        if not template.get("归属方"):
            return False, (
                f"模板 {template_no} 不是可共享的合同模板，不能按渠道借用"
            ), None

        intake = next(
            (r for r in store.rows("file_intake") if r.get("批次编号") == batch_no),
            None)
        if intake is None:
            return False, f"投递批次 {batch_no} 不存在，无法核对批次范围", None

        # 先核对批次归属：借用他人授权替别的渠道提交采用，本身属于越权。
        actual_channel = str(intake.get("来源渠道") or "")
        if actual_channel != channel:
            return False, (
                f"批次 {batch_no} 实际来源渠道为 {actual_channel}，"
                f"不能由渠道 {channel} 代为提交采用，属于越权"
            ), None

        grant_svc = service_for("tmpl_grant")
        grant_ok, grant_msg, grant, reason_code = grant_svc.validate_grant(
            template_no, channel, batch_no)
        if not grant_ok:
            return False, grant_msg, None

        claim_svc = service_for("tmpl_claim")
        claim = claim_svc.claim_of_batch(batch_no)
        version = str(template.get("版本号") or "")
        if claim is not None and claim.get("模板版本") and claim.get("模板版本") != version:
            return False, (
                f"批次 {batch_no} 已领取模板 {claim.get('模板编号')}"
                f"[{claim.get('模板版本')}]，同一批次只能采用一致的模板数据，"
                f"不得再混入 [{version}] 的产物"
            ), None
        existing = self.adoption_of_batch(batch_no)
        if existing is not None and existing.get("status") in ("待复核", "复核中", "已采纳") \
                and existing.get("派生版本") == version:
            return False, (
                f"批次 {batch_no} 已有采用单 {existing.get('采用单号')}"
                f"（{existing.get('status')}），不得重复提交"
            ), None

        diff_no = self._link_diff(template, adoption_no, batch_no, apply_time)

        entry_id = max((int(r.get("id", 0)) for r in store.rows(self.MODULE)), default=0) + 1
        entry: dict[str, Any] = {
            "id": entry_id,
            "status": "待复核",
            "pending": True,
            "abnormal": False,
            "采用单号": adoption_no,
            "关联批次": batch_no,
            "采用渠道": channel,
            "模板编号": template_no,
            "来源模板": self._source_template_no(template) or template_no,
            "派生版本": version,
            "金额表达式": template.get("金额表达式", ""),
            "批次范围": f"{grant.get('批次范围起')}~{grant.get('批次范围止')}",
            "差异编号": diff_no,
            "校验结论": f"{grant_msg}；采用单待模板差异页复核",
            "申请时间": apply_time,
            "授权编号": grant.get("授权编号"),
            "申请人": applicant,
        }
        store.rows(self.MODULE).append(entry)
        return True, (
            f"采用单 {adoption_no} 已生成：渠道 {channel} 采用 {template_no}"
            f"[{version}]，批次 {batch_no}，差异编号 {diff_no}，待模板差异页复核"
        ), dict(entry)

    def _source_template_no(self, template: dict[str, Any]) -> str:
        """派生版本对应的来源模板编号（同族声明的父版本）。"""
        parent_version = str(template.get("派生自") or "")
        if not parent_version:
            return str(template.get("模板编号") or "")
        for row in store.rows("extract_tmpl"):
            if row.get("模板名称") == template.get("模板名称") \
                    and row.get("适用格式") == template.get("适用格式") \
                    and row.get("版本号") == parent_version:
                return str(row.get("模板编号") or "")
        return ""

    def _link_diff(self, template: dict[str, Any], adoption_no: str,
                   batch_no: str, apply_time: str) -> str:
        """把采用单挂到模板差异条目，差异记录因此可追到真实来源批次。"""
        from app.services import service_for

        diff_svc = service_for("tmpl_diff")
        source_no = self._source_template_no(template)
        source = next(
            (r for r in store.rows("extract_tmpl")
             if r.get("模板编号") == source_no), None)
        old_version = str(source.get("版本号") or template.get("版本号")) \
            if source is not None else str(template.get("版本号") or "")
        new_version = str(template.get("版本号") or "")

        existing = diff_svc.find_diff_by_versions(old_version, new_version)
        if existing is not None:
            diff_no = str(existing.get("差异编号"))
            diff_svc.link_adoption(diff_no, batch_no, adoption_no)
            return diff_no

        changed: list[str] = []
        expression = str(template.get("金额表达式") or "")
        if expression and old_version != new_version:
            changed = [expression.split("=")[0].strip()]
        diff_no = f"TD-{old_version}-{new_version}"
        diff_svc.register_adoption_diff(
            diff_no, old_version, new_version, changed,
            batch_no, adoption_no, apply_time)
        return diff_no

    def rejected_batches(self, channel: str) -> list[dict[str, Any]]:
        """该渠道被服务端拒绝的采用申请：越权等拒绝记录必须与采用单一起可见。"""
        return [
            r for r in store.rows(self.MODULE)
            if r.get("采用渠道") == channel and r.get("status") == "已拒绝"
        ]

    def rejected_adoptions(self) -> list[dict[str, Any]]:
        return [r for r in store.rows(self.MODULE) if r.get("status") == "已拒绝"]

    def request_adoption(self, channel: str, template_no: str, batch_no: str,
                         applicant: str = "",
                         apply_time: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """表单提交的另一种写法：自动生成采用单号。"""
        return self.submit_adoption(
            adoption_no=self._next_no(),
            channel=channel,
            template_no=template_no,
            batch_no=batch_no,
            apply_time=apply_time or _now(),
            applicant=applicant,
        )

    def submit_and_reject(self, adoption_no: str, channel: str, template_no: str,
                          batch_no: str, apply_time: str,
                          applicant: str = "") -> tuple[bool, str, dict[str, Any]]:
        """越权类申请：即使被拒也落一条已拒绝记录，保证越权尝试可留痕核查。"""
        ok, message, entry = self.submit_adoption(
            adoption_no, channel, template_no, batch_no, apply_time, applicant)
        if ok and entry is not None:
            return True, message, entry
        if "越权" not in message:
            return False, message, {}
        rows = store.rows(self.MODULE)
        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        record = {
            "id": entry_id, "status": "已拒绝", "pending": False,
            "abnormal": True, "采用单号": adoption_no,
            "关联批次": batch_no, "采用渠道": channel,
            "模板编号": template_no, "来源模板": "",
            "派生版本": "", "金额表达式": "", "批次范围": "",
            "差异编号": "", "校验结论": message,
            "申请时间": apply_time, "申请人": applicant,
        }
        rows.append(record)
        return False, message, record

    # ---- 复核与采纳 ---------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        adoption_no = str(entry.get("采用单号") or "")
        if action == "确认采用":
            ok, message, updated = self.adopt(adoption_no, _now())
            return (updated if ok else entry), message
        if action == "驳回采用":
            ok, message = self.reject(adoption_no, "人工驳回", _now())
            return (dict(entry) if ok else entry), message
        if action == "发起复核":
            if entry.get("status") not in ("待复核", "复核中"):
                return entry, f"采用单 {adoption_no} 状态为 {entry.get('status')}，不能发起复核"
            entry["status"] = "复核中"
            entry["pending"] = True
            entry["abnormal"] = False
            return entry, f"采用单 {adoption_no} 已在模板差异页发起复核"
        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

    def adopt(self, adoption_no: str, adopt_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """确认采用：复核通过后登记批次领取，并锁定该批次唯一模板版本。"""
        from app.services import service_for

        entry = self._find(adoption_no)
        if entry is None:
            return False, f"采用单 {adoption_no} 不存在", None
        if entry.get("status") == "已采纳":
            return False, f"采用单 {adoption_no} 已采纳，不能重复确认", dict(entry)
        if entry.get("status") == "已拒绝":
            return False, f"采用单 {adoption_no} 已被驳回，不能重新采用", dict(entry)

        channel = str(entry.get("采用渠道") or "")
        template_no = str(entry.get("模板编号") or "")
        batch_no = str(entry.get("关联批次") or "")
        grant_svc = service_for("tmpl_grant")
        ok, message, grant, _code = grant_svc.validate_grant(template_no, channel, batch_no)
        if not ok:
            entry["status"] = "已拒绝"
            entry["pending"] = False
            entry["abnormal"] = True
            entry["校验结论"] = message
            return False, message, dict(entry)

        claim_svc = service_for("tmpl_claim")
        claimed, claim_msg, claim = claim_svc.claim_batch(
            batch_no=batch_no,
            channel=channel,
            template_no=template_no,
            version=str(entry.get("派生版本") or ""),
            expression=str(entry.get("金额表达式") or ""),
            adoption_no=adoption_no,
            claim_time=adopt_time,
        )
        if not claimed:
            entry["校验结论"] = claim_msg
            return False, claim_msg, dict(entry)

        for batch in store.rows("parse_batch"):
            if str(batch.get("关联投递批次")) == batch_no:
                batch["模板版本"] = str(entry.get("派生版本") or "")
                batch["采用单号"] = adoption_no
                break

        entry["status"] = "已采纳"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["采纳时间"] = adopt_time
        entry["校验结论"] = (
            f"{message}；批次已领取 {template_no}[{entry.get('派生版本')}]，"
            f"金额表达式「{entry.get('金额表达式')}」"
        )
        return True, (
            f"采用单 {adoption_no} 已采纳：批次 {batch_no} 使用 "
            f"{template_no}[{entry.get('派生版本')}]，"
            f"金额表达式「{entry.get('金额表达式')}」"
        ), dict(entry)

    def reject(self, adoption_no: str, reason: str,
               reject_time: str) -> tuple[bool, str]:
        entry = self._find(adoption_no)
        if entry is None:
            return False, f"采用单 {adoption_no} 不存在"
        if entry.get("status") == "已采纳":
            return False, f"采用单 {adoption_no} 已采纳，不能驳回"
        if entry.get("status") == "已拒绝":
            return False, f"采用单 {adoption_no} 已被驳回"
        entry["status"] = "已拒绝"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["驳回原因"] = reason
        entry["驳回时间"] = reject_time
        entry["校验结论"] = f"复核驳回：{reason}"
        return True, f"采用单 {adoption_no} 已驳回：{reason}"

    def review_scope(self, adoption_no: str) -> dict[str, Any]:
        """模板差异页复核所需的采用单摘要：渠道、批次、版本、表达式与差异编号。"""
        entry = self._find(adoption_no)
        if entry is None:
            return {}
        return {
            "采用单号": entry.get("采用单号"),
            "采用渠道": entry.get("采用渠道"),
            "关联批次": entry.get("关联批次"),
            "模板编号": entry.get("模板编号"),
            "来源模板": entry.get("来源模板"),
            "派生版本": entry.get("派生版本"),
            "金额表达式": entry.get("金额表达式"),
            "差异编号": entry.get("差异编号"),
            "status": entry.get("status"),
            "校验结论": entry.get("校验结论"),
        }
