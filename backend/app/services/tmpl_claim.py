"""批次领取领域服务：记录每个批次实际领取的派生模板版本与金额表达式，产出批次领取数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TmplClaimService(BaseModuleService):
    MODULE = "tmpl_claim"
    ENTITY = "批次领取"
    OPERATION_TERM = "领取"
    LIST_FIELDS = [
        "领取编号", "关联批次", "采用渠道", "模板编号", "模板版本",
        "金额表达式", "采用单号", "领取时间",
    ]
    REQUIRED_FIELDS = ["关联批次", "采用渠道", "模板编号"]
    STATUS_ORDER = ["待领取", "已领取", "已撤回待换版", "已失效"]
    ACTION_RULES = {"确认领取": "已领取", "标记换版": "已撤回待换版", "标记失效": "已失效"}
    NEGATIVE_ACTIONS = ["标记失效"]

    def claim_of_batch(self, batch_no: str) -> dict[str, Any] | None:
        """批次当前生效的领取记录（同一批次只允许一条有效领取）。

        状态仍是「待领取」且没有模板版本的占位行不算已领取，返回 None。
        """
        candidates = [
            r for r in store.rows(self.MODULE)
            if str(r.get("关联批次")) == str(batch_no)
            and r.get("status") in ("待领取", "已领取")
            and r.get("模板版本")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.get("领取时间") or "", r.get("id", 0)))

    def placeholder_of_batch(self, batch_no: str) -> dict[str, Any] | None:
        """批次尚未领取时的占位行（状态待领取、无模板版本）。"""
        return next(
            (r for r in store.rows(self.MODULE)
             if str(r.get("关联批次")) == str(batch_no)
             and r.get("status") == "待领取"
             and not r.get("模板版本")),
            None,
        )

    def claims_of_channel(self, channel: str) -> list[dict[str, Any]]:
        return [
            r for r in store.rows(self.MODULE)
            if str(r.get("采用渠道")) == str(channel)
        ]

    def claimed_version(self, batch_no: str) -> str:
        claim = self.claim_of_batch(batch_no)
        return str(claim.get("模板版本") or "") if claim else ""

    def claim_batch(self, batch_no: str, channel: str, template_no: str,
                    version: str, expression: str, adoption_no: str,
                    claim_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """登记批次领取：同一批次已领取其他版本时拒绝，防止一次解析混用两版产物。"""
        rows = store.rows(self.MODULE)
        existing = self.claim_of_batch(batch_no)
        if existing is not None:
            same = (existing.get("模板编号") == template_no
                    and existing.get("模板版本") == version)
            if same:
                existing.update({
                    "status": "已领取",
                    "pending": False,
                    "abnormal": False,
                    "金额表达式": expression,
                    "采用单号": adoption_no,
                    "领取时间": claim_time,
                })
                return True, (
                    f"批次 {batch_no} 已领取 {template_no}[{version}]，采用单 {adoption_no}"
                ), dict(existing)
            return False, (
                f"批次 {batch_no} 已领取模板 {existing.get('模板编号')}"
                f"[{existing.get('模板版本')}]，同一批次只能采用一致的模板数据，"
                f"不得再混入 {template_no}[{version}] 的产物"
            ), dict(existing)

        target = self.placeholder_of_batch(batch_no)
        values = {
            "关联批次": batch_no,
            "采用渠道": channel,
            "模板编号": template_no,
            "模板版本": version,
            "金额表达式": expression,
            "采用单号": adoption_no,
            "领取时间": claim_time,
            "status": "已领取",
            "pending": False,
            "abnormal": False,
        }
        if target is not None:
            target.update(values)
            return True, (
                f"批次 {batch_no} 已领取 {template_no}[{version}]，采用单 {adoption_no}"
            ), dict(target)

        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": entry_id,
            "领取编号": f"TC-{entry_id:03d}",
            **values,
        }
        rows.append(entry)
        return True, (
            f"批次 {batch_no} 已领取 {template_no}[{version}]，采用单 {adoption_no}"
        ), dict(entry)

    def mark_revoked(self, batch_no: str, revoke_time: str) -> tuple[bool, str]:
        """撤回后尚未领取的批次标记换版：撤回回执据此提示下一批用哪个表达式。"""
        placeholders = [
            r for r in store.rows(self.MODULE)
            if str(r.get("关联批次")) == str(batch_no)
            and r.get("status") == "待领取"
        ]
        if not placeholders:
            return False, f"批次 {batch_no} 没有处于待领取的领用记录"
        for placeholder in placeholders:
            placeholder["status"] = "已撤回待换版"
            placeholder["pending"] = True
            placeholder["abnormal"] = True
            placeholder["撤回时间"] = revoke_time
        return True, f"批次 {batch_no} 已标记为撤回后待换版"

    def channel_summary(self, channel: str) -> dict[str, Any]:
        """按渠道汇总领取版本，供模板确认步骤核对已领取范围。"""
        claims = self.claims_of_channel(channel)
        versions: dict[str, list[str]] = {}
        for claim in claims:
            key = f"{claim.get('模板编号')}[{claim.get('模板版本')}]"
            versions.setdefault(key, []).append(str(claim.get("关联批次")))
        return {
            "采用渠道": channel,
            "领取数": len(claims),
            "版本分布": versions,
        }
