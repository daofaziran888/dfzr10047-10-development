"""派生模板授权领域服务：维护合同模板派生版本的共享授权、批次范围与撤回回执，产出授权数据集。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TmplGrantService(BaseModuleService):
    MODULE = "tmpl_grant"
    ENTITY = "派生授权"
    OPERATION_TERM = "授权"
    LIST_FIELDS = [
        "授权编号", "模板编号", "来源模板", "派生版本", "金额表达式",
        "授权方", "被授权渠道", "批次范围起", "批次范围止", "授权时间",
    ]
    REQUIRED_FIELDS = ["授权编号", "模板编号", "被授权渠道", "批次范围起", "批次范围止"]
    STATUS_ORDER = ["草稿", "已生效", "已撤回", "已过期"]
    ACTION_RULES = {"启用授权": "已生效", "撤回共享": "已撤回", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = ["撤回共享"]

    # ---- 查询 ---------------------------------------------------------
    def _find(self, grant_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("授权编号") == grant_no:
                return row
        return None

    def active_grants(self, channel: str | None = None) -> list[dict[str, Any]]:
        """列出仍有效的授权，可按被授权渠道过滤。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "已生效"
            and (channel is None or row.get("被授权渠道") == channel)
        ]

    def find_grant(self, template_no: str, channel: str) -> dict[str, Any] | None:
        """同一派生模板在同一渠道上最近一次授权（含已撤回），用于区分撤回前后。"""
        candidates = [
            row for row in store.rows(self.MODULE)
            if row.get("模板编号") == template_no
            and row.get("被授权渠道") == channel
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.get("授权时间") or "", r.get("id", 0)))

    def grants_of_template(self, template_no: str) -> list[dict[str, Any]]:
        return [r for r in store.rows(self.MODULE) if r.get("模板编号") == template_no]

    @staticmethod
    def covers_batch(grant: dict[str, Any], batch_no: str) -> bool:
        """批次是否落在授权声明的批次范围内（批次编号按字典序可比）。"""
        start = str(grant.get("批次范围起") or "")
        end = str(grant.get("批次范围止") or "")
        batch = str(batch_no or "")
        if not batch:
            return False
        if start and batch < start:
            return False
        if end and batch > end:
            return False
        return True

    # ---- 授权校验 -----------------------------------------------------
    def validate_grant(self, template_no: str, channel: str,
                       batch_no: str) -> tuple[bool, str, dict[str, Any] | None, str]:
        """核对采用申请的权限与批次范围。

        返回 (是否通过, 说明, 命中的授权, 拒绝原因码)。拒绝原因码：
        - ``越权``：该模板的授权属于其他渠道，申请渠道没有权限；
        - ``未授权``：该模板没有任何授权；
        - ``已撤回``：授权在撤回之后不能再覆盖新批次；
        - ``超范围``：批次不在授权声明的批次范围内。
        """
        grant = self.find_grant(template_no, channel)
        if grant is None:
            others = [
                g for g in self.grants_of_template(template_no)
                if g.get("被授权渠道") != channel
            ]
            if others:
                holders = sorted({str(g.get("被授权渠道")) for g in others})
                return False, (
                    f"渠道 {channel} 未获得模板 {template_no} 的派生授权，"
                    f"该授权属于渠道 {holders}，借用他人授权属于越权"
                ), None, "越权"
            return False, f"模板 {template_no} 没有任何渠道共享授权", None, "未授权"

        if grant.get("status") == "已撤回":
            return False, (
                f"模板 {template_no} 对渠道 {channel} 的共享已于 "
                f"{grant.get('撤回时间')} 撤回，撤回后不可再用于新批次"
            ), grant, "已撤回"
        if grant.get("status") != "已生效":
            return False, (
                f"授权 {grant.get('授权编号')} 状态为 {grant.get('status')}，尚未生效"
            ), grant, "未生效"
        if not self.covers_batch(grant, batch_no):
            return False, (
                f"批次 {batch_no} 不在授权 {grant.get('授权编号')} 的批次范围 "
                f"[{grant.get('批次范围起')}, {grant.get('批次范围止')}] 内"
            ), grant, "超范围"
        return True, (
            f"授权 {grant.get('授权编号')} 有效，批次 {batch_no} 在范围内"
        ), grant, ""

    # ---- 撤回共享 -----------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """撤回共享要同时生成回执并判定存量/增量范围，不能只改状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "撤回共享":
            ok, message, updated = self.revoke(
                str(entry.get("授权编号") or ""),
                str(entry.get("撤回时间") or "") or datetime.now().isoformat(timespec="seconds"),
                str(entry.get("撤回原因") or "") or "授权方撤回共享",
            )
            return (updated if ok else entry), message
        if action == "启用授权":
            if entry.get("status") == "已撤回":
                return entry, "已撤回的授权不能重新启用，须重新发起共享授权"
            entry["status"] = "已生效"
            entry["pending"] = False
            entry["abnormal"] = False
            return entry, f"授权 {entry.get('授权编号')} 已生效"
        return super().run_action(entry_id, action)

    def revoke(self, grant_no: str, revoke_time: str,
               reason: str) -> tuple[bool, str, dict[str, Any] | None]:
        """撤回共享：撤回前已领取的批次继续可用，撤回后不得再覆盖新批次。"""
        grant = self._find(grant_no)
        if grant is None:
            return False, f"授权 {grant_no} 不存在", None
        if grant.get("status") == "已撤回":
            return False, f"授权 {grant_no} 已撤回，无需重复操作", dict(grant)

        grant["status"] = "已撤回"
        grant["pending"] = False
        grant["abnormal"] = True
        grant["撤回时间"] = revoke_time
        grant["撤回原因"] = reason

        receipt = self.revocation_receipt(grant_no)
        grant["撤回回执编号"] = receipt["撤回回执编号"]
        grant["撤回回执"] = receipt["撤回回执"]
        return True, receipt["撤回回执"], dict(grant)

    def claimed_batches(self, grant: dict[str, Any]) -> list[str]:
        """撤回时已经领取该派生版本的批次；这些批次属于可继续的存量范围。"""
        template_no = str(grant.get("模板编号") or "")
        version = str(grant.get("派生版本") or "")
        channel = str(grant.get("被授权渠道") or "")
        return sorted({
            str(claim.get("关联批次"))
            for claim in store.rows("tmpl_claim")
            if claim.get("模板编号") == template_no
            and claim.get("模板版本") == version
            and claim.get("采用渠道") == channel
            and claim.get("status") in ("已领取", "已采纳")
        })

    def batches_claimed_any_version(self, channel: str) -> set[str]:
        return {
            str(claim.get("关联批次"))
            for claim in store.rows("tmpl_claim")
            if claim.get("采用渠道") == channel and claim.get("模板版本")
        }

    def revoked_unusable_batches(self, grant: dict[str, Any]) -> list[str]:
        """授权批次范围内、撤回后尚未领取的批次：这些批次不能再使用该派生版本。"""
        if grant.get("status") != "已撤回":
            return []
        channel = str(grant.get("被授权渠道") or "")
        usable = self.batches_claimed_any_version(channel)
        return sorted({
            str(intake.get("批次编号"))
            for intake in store.rows("file_intake")
            if intake.get("来源渠道") == channel
            and self.covers_batch(grant, str(intake.get("批次编号")))
            and str(intake.get("批次编号")) not in usable
        })

    def next_allowed_grant(self, channel: str,
                           batch_no: str) -> dict[str, Any] | None:
        """撤回后仍可用的授权：同渠道、同批次范围内、状态已生效。"""
        candidates = [
            row for row in self.active_grants(channel)
            if self.covers_batch(row, batch_no)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.get("授权时间") or "", r.get("id", 0)))

    def revocation_receipt(self, grant_no: str) -> dict[str, Any]:
        """撤回回执：返回在途批次可继续的下一步与下一批可用的表达式。

        乙从回执返回正在处理的批次时，服务端明确告知：
        - 在途批次沿用撤回前已领取的版本，不需换版；
        - 下一批只能采用撤回后仍有效的授权，否则必须停下等待新授权。
        """
        grant = self._find(grant_no)
        if grant is None:
            return {}
        channel = str(grant.get("被授权渠道") or "")
        template_no = str(grant.get("模板编号") or "")
        version = str(grant.get("派生版本") or "")
        expression = str(grant.get("金额表达式") or "")
        in_flight = self.claimed_batches(grant)
        blocked = self.revoked_unusable_batches(grant)

        next_batch = ""
        next_grant: dict[str, Any] | None = None
        next_step = "撤回后无剩余批次"
        if blocked:
            next_batch = blocked[0]
            next_grant = self.next_allowed_grant(channel, next_batch)
            if next_grant is not None:
                next_step = (
                    f"批次 {next_batch} 不得再采用 {template_no}[{version}]，"
                    f"改用授权 {next_grant.get('授权编号')} 的 "
                    f"{next_grant.get('模板编号')}[{next_grant.get('派生版本')}]，"
                    f"金额表达式「{next_grant.get('金额表达式')}」"
                )
            else:
                next_step = (
                    f"批次 {next_batch} 不得再采用 {template_no}[{version}]，"
                    f"也没有其他有效授权，须停止解析等待新授权"
                )

        return {
            "撤回回执编号": f"GR-{grant_no}",
            "撤回回执": (
                f"撤回回执 GR-{grant_no}：已撤回渠道 {channel} 对 {template_no}[{version}] "
                f"的共享；在途批次 {in_flight or '无'} 可继续使用原版本，"
                f"其余批次不可再用"
            ),
            "授权编号": grant_no,
            "被授权渠道": channel,
            "模板编号": template_no,
            "派生版本": version,
            "金额表达式": expression,
            "在途批次": in_flight,
            "在途结论": (
                f"在途批次 {in_flight} 可继续沿用已领取的 {version}"
                f"「{expression}」，旧输出保留不改写"
                if in_flight else "撤回前没有已领取的在途批次"
            ),
            "下一批": next_batch,
            "下一步": next_step,
            "下一批模板编号": next_grant.get("模板编号") if next_grant else "",
            "下一批派生版本": next_grant.get("派生版本") if next_grant else "",
            "下一批金额表达式": next_grant.get("金额表达式") if next_grant else "",
        }

    def resume_batch(self, channel: str, batch_no: str) -> dict[str, Any]:
        """乙返回正在处理的批次时，服务端给出准许继续的下一步与实用表达式。"""
        from app.services import service_for

        claim_svc = service_for("tmpl_claim")
        claim = claim_svc.claim_of_batch(batch_no)
        if claim is not None and claim.get("模板版本"):
            grant = self.find_grant(str(claim.get("模板编号")), channel)
            revoked = grant is not None and grant.get("status") == "已撤回"
            return {
                "关联批次": batch_no,
                "采用渠道": channel,
                "允许继续": True,
                "模板编号": claim.get("模板编号"),
                "派生版本": claim.get("模板版本"),
                "金额表达式": claim.get("金额表达式"),
                "下一步": (
                    f"批次 {batch_no} 在撤回前已领取 "
                    f"{claim.get('模板编号')}[{claim.get('模板版本')}]，"
                    f"继续按「{claim.get('金额表达式')}」解析，不换版、不改写旧输出"
                ),
                "撤回后沿用": revoked,
            }

        grant = self.next_allowed_grant(channel, batch_no)
        if grant is None:
            return {
                "关联批次": batch_no,
                "采用渠道": channel,
                "允许继续": False,
                "模板编号": "",
                "派生版本": "",
                "金额表达式": "",
                "下一步": (
                    f"批次 {batch_no} 没有可用的派生授权，须停止解析并申请新授权"
                ),
                "撤回后沿用": False,
            }
        adoption = service_for("tmpl_adopt").adoption_of_batch(batch_no)
        if adoption is not None and adoption.get("status") in ("待复核", "复核中", "已采纳"):
            step = (
                f"批次 {batch_no} 已有采用单 {adoption.get('采用单号')}，"
                f"按 {grant.get('模板编号')}[{grant.get('派生版本')}] 继续复核"
            )
        else:
            step = (
                f"批次 {batch_no} 须采用授权 {grant.get('授权编号')} 的 "
                f"{grant.get('模板编号')}[{grant.get('派生版本')}]，"
                f"金额表达式「{grant.get('金额表达式')}」"
            )
        return {
            "关联批次": batch_no,
            "采用渠道": channel,
            "允许继续": True,
            "模板编号": grant.get("模板编号"),
            "派生版本": grant.get("派生版本"),
            "金额表达式": grant.get("金额表达式"),
            "授权编号": grant.get("授权编号"),
            "下一步": step,
            "撤回后沿用": False,
        }

    # ---- 确认范围 -----------------------------------------------------
    def scope_report(self, grant_no: str) -> dict[str, Any]:
        """输出授权的三类范围：已授权、已领取、撤回后不可再用。"""
        grant = self._find(grant_no)
        if grant is None:
            return {}
        template_no = str(grant.get("模板编号") or "")
        version = str(grant.get("派生版本") or "")
        channel = str(grant.get("被授权渠道") or "")
        claimed = self.claimed_batches(grant)

        withdrawn = grant.get("status") == "已撤回"
        all_claimed = self.batches_claimed_any_version(channel)
        pending = [
            str(intake.get("批次编号"))
            for intake in store.rows("file_intake")
            if intake.get("来源渠道") == channel
            and self.covers_batch(grant, str(intake.get("批次编号")))
            and str(intake.get("批次编号")) not in claimed
            and str(intake.get("批次编号")) not in all_claimed
        ]
        return {
            "授权编号": grant_no,
            "模板编号": template_no,
            "派生版本": version,
            "金额表达式": grant.get("金额表达式"),
            "被授权渠道": channel,
            "status": grant.get("status"),
            "已授权": {
                "授权编号": grant_no,
                "批次范围": [grant.get("批次范围起"), grant.get("批次范围止")],
                "仍有效": not withdrawn,
            },
            "已领取": claimed,
            "撤回后不可再用": pending if withdrawn else [],
            "待领取": [] if withdrawn else pending,
        }

    def scopes_of_channel(self, channel: str) -> dict[str, list[dict[str, Any]]]:
        """按渠道列出全部授权的三类范围，供模板确认步骤消费。"""
        grouped: dict[str, list[dict[str, Any]]] = {
            "已授权": [], "已领取": [], "撤回后不可再用": [],
        }
        for grant in store.rows(self.MODULE):
            if grant.get("被授权渠道") != channel:
                continue
            report = self.scope_report(str(grant.get("授权编号")))
            if grant.get("status") == "已生效":
                grouped["已授权"].append(report)
            for batch in report.get("已领取", []):
                grouped["已领取"].append({
                    "授权编号": report["授权编号"],
                    "模板编号": report["模板编号"],
                    "派生版本": report["派生版本"],
                    "金额表达式": report["金额表达式"],
                    "关联批次": batch,
                })
            for batch in report.get("撤回后不可再用", []):
                grouped["撤回后不可再用"].append({
                    "授权编号": report["授权编号"],
                    "模板编号": report["模板编号"],
                    "派生版本": report["派生版本"],
                    "金额表达式": report["金额表达式"],
                    "关联批次": batch,
                })
        return grouped
