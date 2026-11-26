"""渠道采用接口：抽取模板页提交采用表单、模板差异页按采用单发起复核。

通用资源路由只处理列表/登记/动作；采用申请要核对权限与批次范围、
还要把采用单挂到模板差异条目，因此单独开一组领域接口。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services import service_for

router = APIRouter(prefix="/api/tmpl_adopt", tags=["渠道采用"])


class AdoptionRequest(BaseModel):
    """采用表单：采用渠道 + 模板编号 + 关联批次，其余字段可省略。"""

    channel: str = ""
    template_no: str = ""
    batch_id: str = ""
    adoption_no: str = ""
    applicant: str = ""
    apply_time: str = ""
    values: dict[str, Any] = Field(default_factory=dict)


def _from_payload(payload: AdoptionRequest) -> dict[str, Any]:
    """兼容前端直接提交表单值与通用 values 包装两种写法。"""
    merged: dict[str, Any] = dict(payload.values)
    alias = {
        "采用渠道": payload.channel,
        "模板编号": payload.template_no,
        "关联批次": payload.batch_id,
        "采用单号": payload.adoption_no,
        "申请人": payload.applicant,
        "申请时间": payload.apply_time,
    }
    for key, value in alias.items():
        if value:
            merged[key] = value
    return merged


@router.get("/form")
def adoption_form() -> dict[str, Any]:
    """采用表单的可选项：可共享合同模板、各渠道与可领取批次。"""
    templates = [
        {
            "模板编号": row.get("模板编号"),
            "模板名称": row.get("模板名称"),
            "版本号": row.get("版本号"),
            "归属方": row.get("归属方"),
            "金额表达式": row.get("金额表达式"),
            "status": row.get("status"),
        }
        for row in _rows("extract_tmpl") if row.get("归属方")
    ]
    channels = sorted({
        str(row.get("来源渠道")) for row in _rows("file_intake")
        if str(row.get("来源渠道") or "").startswith("contract-")
    })
    batches = [
        {
            "批次编号": row.get("批次编号"),
            "来源渠道": row.get("来源渠道"),
            "投递时间": row.get("投递时间"),
        }
        for row in _rows("file_intake")
        if str(row.get("来源渠道") or "").startswith("contract-")
    ]
    grants = [
        {
            "授权编号": row.get("授权编号"),
            "模板编号": row.get("模板编号"),
            "被授权渠道": row.get("被授权渠道"),
            "派生版本": row.get("派生版本"),
            "批次范围起": row.get("批次范围起"),
            "批次范围止": row.get("批次范围止"),
            "status": row.get("status"),
        }
        for row in _rows("tmpl_grant")
    ]
    return {"templates": templates, "channels": channels,
            "batches": batches, "grants": grants}


@router.post("/apply", response_model=ActionResult)
def apply_adoption(payload: AdoptionRequest) -> ActionResult:
    """提交采用申请：服务端核对有效权限与批次范围，返回可复核的渠道采用单。"""
    values = _from_payload(payload)
    service = service_for("tmpl_adopt")
    entry, missing = service.create_entry(values)
    if missing:
        return ActionResult(ok=False, message=missing[0], entry=None)
    return ActionResult(ok=True, message="渠道采用单已生成，可在模板差异页复核", entry=entry)


@router.get("/{adoption_no}/review")
def adoption_review(adoption_no: str) -> dict[str, Any]:
    """模板差异页按同一采用单核对受影响渠道与来源批次。"""
    service = service_for("tmpl_diff")
    review = service.adoption_review(adoption_no)
    if not review.get("ok"):
        raise HTTPException(status_code=404, detail="采用单不存在")
    return review


@router.get("/{adoption_no}/scope")
def adoption_scope(adoption_no: str) -> dict[str, Any]:
    adopt_svc = service_for("tmpl_adopt")
    entry = adopt_svc._find(adoption_no)
    if entry is None:
        raise HTTPException(status_code=404, detail="采用单不存在")
    channel = str(entry.get("采用渠道") or "")
    grant_svc = service_for("tmpl_grant")
    return {
        "采用单号": adoption_no,
        "范围": grant_svc.scopes_of_channel(channel),
        "撤回回执": [
            grant_svc.revocation_receipt(str(g.get("授权编号")))
            for g in _rows("tmpl_grant")
            if g.get("被授权渠道") == channel and g.get("status") == "已撤回"
        ],
    }


@router.get("/{adoption_no}/resume")
def adoption_resume(adoption_no: str) -> dict[str, Any]:
    """乙从撤回回执返回在途/下一批次时，返回服务端准许继续的下一步与实用表达式。"""
    adopt_svc = service_for("tmpl_adopt")
    entry = adopt_svc._find(adoption_no)
    if entry is None:
        raise HTTPException(status_code=404, detail="采用单不存在")
    grant_svc = service_for("tmpl_grant")
    channel = str(entry.get("采用渠道") or "")
    batch_no = str(entry.get("关联批次") or "")
    grant = grant_svc.find_grant(str(entry.get("模板编号") or ""), channel)
    receipt = (
        grant_svc.revocation_receipt(str(grant.get("授权编号")))
        if grant is not None and grant.get("status") == "已撤回" else {}
    )
    return {
        "采用单号": adoption_no,
        "撤回回执": receipt,
        "在途或下一批": grant_svc.resume_batch(channel, batch_no),
    }


def _rows(module: str) -> list[dict[str, Any]]:
    from app.store import store

    return store.rows(module)
