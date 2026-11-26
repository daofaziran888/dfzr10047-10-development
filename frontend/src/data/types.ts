/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: EntryRow | null
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type AdoptionTemplateOption = {
  模板编号: string
  模板名称: string
  版本号: string
  归属方: string
  金额表达式: string
  status: string
}

export type AdoptionForm = {
  templates: AdoptionTemplateOption[]
  channels: string[]
  batches: { 批次编号: string; 来源渠道: string; 投递时间: string }[]
  grants: {
    授权编号: string
    模板编号: string
    被授权渠道: string
    派生版本: string
    批次范围起: string
    批次范围止: string
    status: string
  }[]
}

export type AdoptionReview = {
  ok: boolean
  采用单号: string
  采用渠道: string
  关联批次: string
  模板编号: string
  派生版本: string
  金额表达式: string
  差异编号: string
  旧模板版本: string
  新模板版本: string
  来源批次: string[]
  受影响渠道: string[]
  关联采用单: string[]
  status: string
}

export type AdoptionScope = {
  采用单号: string
  范围: {
    已授权: Record<string, unknown>[]
    已领取: Record<string, unknown>[]
    撤回后不可再用: Record<string, unknown>[]
  }
  撤回回执: Record<string, unknown>[]
}

export type ResumeGuidance = {
  关联批次: string
  采用渠道: string
  允许继续: boolean
  模板编号: string
  派生版本: string
  金额表达式: string
  下一步: string
  撤回后沿用: boolean
}

export type AdoptionResume = {
  采用单号: string
  撤回回执: Record<string, unknown>
  在途或下一批: ResumeGuidance
}
