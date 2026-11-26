import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  AdoptionForm,
  AdoptionResume,
  AdoptionReview,
  AdoptionScope,
  EntryRow,
  OverviewResult,
  PageResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  if (key === 'tmpl_adopt') return await applyAdoption(values)
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

/** 采用表单的可选项：可共享合同模板、渠道与批次。 */
export async function loadAdoptionForm(): Promise<AdoptionForm> {
  const response = await request('/api/tmpl_adopt/form')
  if (!response.ok) throw new Error('采用表单选项读取失败')
  return await response.json()
}

/** 提交采用申请：服务端核对权限与批次范围后返回渠道采用单。 */
export async function applyAdoption(
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request('/api/tmpl_adopt/apply', {
    method: 'POST',
    body: JSON.stringify({
      channel: values['采用渠道'] ?? '',
      template_no: values['模板编号'] ?? '',
      batch_id: values['关联批次'] ?? '',
      applicant: values['申请人'] ?? '',
      apply_time: values['申请时间'] ?? '',
      values,
    }),
  })
  return await response.json()
}

/** 模板差异页按同一采用单核对受影响渠道与来源批次。 */
export async function loadAdoptionReview(adoptionNo: string): Promise<AdoptionReview> {
  const response = await request(`/api/tmpl_adopt/${encodeURIComponent(adoptionNo)}/review`)
  if (!response.ok) throw new Error('采用单复核信息读取失败')
  return await response.json()
}

/** 采用单所属渠道的三类范围与撤回回执。 */
export async function loadAdoptionScope(adoptionNo: string): Promise<AdoptionScope> {
  const response = await request(`/api/tmpl_adopt/${encodeURIComponent(adoptionNo)}/scope`)
  if (!response.ok) throw new Error('采用范围读取失败')
  return await response.json()
}

/** 撤回回执返回在途批次：服务端准许继续的下一步与实用表达式。 */
export async function loadAdoptionResume(adoptionNo: string): Promise<AdoptionResume> {
  const response = await request(`/api/tmpl_adopt/${encodeURIComponent(adoptionNo)}/resume`)
  if (!response.ok) throw new Error('撤回回执读取失败')
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}
