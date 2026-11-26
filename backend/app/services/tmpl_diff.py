"""模板差异领域服务：逐字段比对模板新旧版本抽取表达式变更，产出差异条目数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TmplDiffService(BaseModuleService):
    MODULE = "tmpl_diff"
    ENTITY = "差异条目"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["差异编号", "旧模板版本", "新模板版本", "差异字段", "差异类别", "影响批次数", "修正方案", "登记时间"]
    REQUIRED_FIELDS = ["差异编号", "旧模板版本", "新模板版本"]
    STATUS_ORDER = ["待比对", "比对中", "已收敛", "偏离超限"]
    ACTION_RULES = {"启动比对": "比对中", "确认收敛": "已收敛", "标记超限": "偏离超限"}
    NEGATIVE_ACTIONS = ["标记超限"]

    # 差异字段数超过该上限时判定偏离超限，必须分批重解析而非一次性覆盖
    MAX_SINGLE_PASS_FIELDS = 6

    def _find_tmpl(self, version_no: str) -> dict[str, Any] | None:
        for row in store.rows("extract_tmpl"):
            if row.get("版本号") == version_no:
                return row
        return None

    def compare_versions(self, diff_no: str, old_version: str, new_version: str,
                         old_fields: list[str], new_fields: list[str],
                         changed_fields: list[str], affected_batches: int,
                         register_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """逐字段比对新旧版本，按差异字段数判定收敛或偏离超限。

        差异条目记录新增/删除/变更字段清单，影响批次数决定重解析位点范围。
        """
        old_tmpl = self._find_tmpl(old_version)
        new_tmpl = self._find_tmpl(new_version)
        if old_tmpl is None or new_tmpl is None:
            missing = old_version if old_tmpl is None else new_version
            return False, f"模板版本 {missing} 在抽取模板中不存在，无法比对", None

        added = sorted(set(new_fields) - set(old_fields))
        removed = sorted(set(old_fields) - set(new_fields))
        changed = sorted(set(changed_fields) & (set(old_fields) & set(new_fields)))
        diff_count = len(added) + len(removed) + len(changed)

        categories: list[str] = []
        if added:
            categories.append(f"新增{len(added)}")
        if removed:
            categories.append(f"删除{len(removed)}")
        if changed:
            categories.append(f"变更{len(changed)}")

        converged = diff_count <= self.MAX_SINGLE_PASS_FIELDS
        status = "已收敛" if converged else "偏离超限"
        if diff_count == 0:
            plan = "新旧版本抽取表达式一致，无需重解析"
        elif converged:
            plan = f"差异 {diff_count} 个字段在单趟上限内，按位点范围一次性重解析 {affected_batches} 个批次"
        else:
            chunks = -(-diff_count // self.MAX_SINGLE_PASS_FIELDS)
            plan = (
                f"差异 {diff_count} 个字段超过单趟上限 {self.MAX_SINGLE_PASS_FIELDS}，"
                f"影响 {affected_batches} 个批次须分 {chunks} 批重解析，禁止一次性覆盖"
            )

        rows = store.rows(self.MODULE)
        target = next((r for r in rows if r.get("差异编号") == diff_no), None)
        if target is None:
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            target = {"id": entry_id, "差异编号": diff_no}
            rows.append(target)
        target.update({
            "旧模板版本": old_version,
            "新模板版本": new_version,
            "差异字段": ",".join(added + removed + changed),
            "差异类别": ",".join(categories) or "无差异",
            "影响批次数": str(affected_batches),
            "修正方案": plan,
            "登记时间": register_time,
            "新增字段": ",".join(added),
            "删除字段": ",".join(removed),
            "变更字段": ",".join(changed),
            "差异字段数": str(diff_count),
            "status": status,
            "pending": not converged,
            "abnormal": not converged,
        })
        return converged, (
            f"模板 {old_version}→{new_version} 差异 {diff_count} 个字段，"
            f"判定{status}：{plan}"
        ), dict(target)

    def affected_batches_of(self, diff_no: str) -> int:
        """输出差异条目影响批次数，供重解析位点范围规划消费。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") == diff_no:
                return self._int_value(row.get("影响批次数"))
        return 0

    # ---- 渠道采用单联动 -----------------------------------------------
    def find_diff_by_versions(self, old_version: str,
                              new_version: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if (row.get("旧模板版本") == old_version
                    and row.get("新模板版本") == new_version):
                return row
        return None

    def register_adoption_diff(self, diff_no: str, old_version: str,
                               new_version: str, changed_fields: list[str],
                               batch_no: str, adoption_no: str,
                               register_time: str) -> dict[str, Any]:
        """渠道采用单触发的差异条目：记录来源批次与关联采用单，初始待比对。"""
        rows = store.rows(self.MODULE)
        target = next((r for r in rows if r.get("差异编号") == diff_no), None)
        if target is None:
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            target = {"id": entry_id, "差异编号": diff_no}
            rows.append(target)
        target.update({
            "旧模板版本": old_version,
            "新模板版本": new_version,
            "差异字段": ",".join(changed_fields),
            "差异类别": f"渠道采用{len(changed_fields)}" if changed_fields else "渠道采用",
            "影响批次数": "1",
            "修正方案": "渠道采用单待模板差异页复核",
            "登记时间": register_time,
            "来源批次": batch_no,
            "关联采用单": adoption_no,
            "受影响渠道": "",
            "差异字段数": str(len(changed_fields)),
            "status": "待比对",
            "pending": True,
            "abnormal": False,
        })
        return target

    def link_adoption(self, diff_no: str, batch_no: str,
                      adoption_no: str) -> tuple[bool, str]:
        """把新的采用单挂到差异条目：来源批次与关联采用单逐条累积，可追回真实批次。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") != diff_no:
                continue
            batches = self._split_field(row.get("来源批次"))
            if batch_no and batch_no not in batches:
                batches.append(batch_no)
            adoptions = self._split_field(row.get("关联采用单"))
            if adoption_no and adoption_no not in adoptions:
                adoptions.append(adoption_no)
            channels = self._split_field(row.get("受影响渠道"))
            for adoption in store.rows("tmpl_adopt"):
                if adoption.get("采用单号") in adoptions:
                    channel = str(adoption.get("采用渠道") or "")
                    if channel and channel not in channels:
                        channels.append(channel)
            row["来源批次"] = ",".join(batches)
            row["关联采用单"] = ",".join(adoptions)
            row["受影响渠道"] = ",".join(channels)
            if len(batches) > 1:
                row["影响批次数"] = str(len(batches))
            return True, (
                f"差异 {diff_no} 已关联采用单 {adoption_no}，来源批次 {batch_no}"
            )
        return False, f"差异条目 {diff_no} 不存在"

    @staticmethod
    def _split_field(value: Any) -> list[str]:
        return [p.strip() for p in str(value or "").split(",") if p.strip()]

    def source_batches_of(self, diff_no: str) -> list[str]:
        """差异条目可追溯的来源批次清单，用于确认差异实际涵盖哪批。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") == diff_no:
                return self._split_field(row.get("来源批次"))
        return []

    def adoptions_of_diff(self, diff_no: str) -> list[dict[str, Any]]:
        """差异条目关联的全部采用单，供差异页核对受影响渠道。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") != diff_no:
                continue
            numbers = set(self._split_field(row.get("关联采用单")))
            return [
                dict(adoption) for adoption in store.rows("tmpl_adopt")
                if adoption.get("采用单号") in numbers
            ]
        return []

    def adoption_review(self, adoption_no: str) -> dict[str, Any]:
        """模板差异页按采用单复核：返回受影响渠道、版本、表达式与实际来源批次。"""
        adoption = next(
            (r for r in store.rows("tmpl_adopt") if r.get("采用单号") == adoption_no),
            None)
        if adoption is None:
            return {"采用单号": adoption_no, "ok": False, "message": "采用单不存在"}
        diff_no = str(adoption.get("差异编号") or "")
        diff = next(
            (r for r in store.rows(self.MODULE) if r.get("差异编号") == diff_no),
            None)
        linked = self.adoptions_of_diff(diff_no) if diff else []
        return {
            "采用单号": adoption_no,
            "ok": True,
            "采用渠道": adoption.get("采用渠道"),
            "关联批次": adoption.get("关联批次"),
            "模板编号": adoption.get("模板编号"),
            "派生版本": adoption.get("派生版本"),
            "金额表达式": adoption.get("金额表达式"),
            "差异编号": diff_no,
            "旧模板版本": diff.get("旧模板版本") if diff else "",
            "新模板版本": diff.get("新模板版本") if diff else "",
            "来源批次": self.source_batches_of(diff_no),
            "受影响渠道": sorted({
                str(a.get("采用渠道")) for a in linked if a.get("采用渠道")
            }),
            "关联采用单": [a.get("采用单号") for a in linked],
            "status": adoption.get("status"),
        }

    @staticmethod
    def _int_value(value: Any) -> int:
        text = str(value or "").strip()
        return int(text) if text.isdigit() else 0

    def diff_fields_of(self, diff_no: str) -> list[str]:
        """输出差异字段清单，供重解析时确定需要重新抽取的字段。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") == diff_no:
                return [f.strip() for f in str(row.get("差异字段") or "").split(",") if f.strip()]
        return []

    def requires_split_reparse(self, diff_no: str) -> tuple[bool, int]:
        """判断该差异是否必须分批重解析，返回 (是否分批, 建议分批数)。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") != diff_no:
                continue
            diff_count = self._int_value(row.get("差异字段数"))
            if diff_count <= self.MAX_SINGLE_PASS_FIELDS:
                return False, 1
            return True, -(-diff_count // self.MAX_SINGLE_PASS_FIELDS)
        return False, 1

    def unresolved_diffs(self) -> list[dict[str, Any]]:
        """列出偏离超限未收敛的差异条目，供治理看板消费。"""
        return [
            {
                "差异编号": r.get("差异编号"),
                "旧模板版本": r.get("旧模板版本"),
                "新模板版本": r.get("新模板版本"),
                "差异字段数": r.get("差异字段数"),
                "影响批次数": r.get("影响批次数"),
                "status": r.get("status"),
            }
            for r in store.rows(self.MODULE)
            if r.get("status") == "偏离超限"
        ]
