import axios from 'axios'

// dev 走 vite 代理 (/api → 后端), 生产同源 (FastAPI 托管 dist)
const baseURL = import.meta.env.DEV ? '/api' : ''

export const api = axios.create({ baseURL, timeout: 600000 })

export interface QueryRequest {
  query: string
  project_id: string
  history?: string[]
  env?: string
  alias_mode?: string
  no_thinking?: boolean
  rule_only?: boolean
  budget?: number
}

export async function fusionQuery(req: QueryRequest, timeout?: number) {
  const { data } = await api.post('/fusion/query', req, { timeout })
  return data
}

export async function fusionEnum(hint: string, project_id: string, alias_mode?: string, summarize = true, timeout?: number) {
  const { data } = await api.post('/fusion/enum', { hint, project_id, alias_mode, summarize }, { timeout })
  return data
}

export async function fusionExperience(query: string, project_id: string, env: string, timeout?: number) {
  const { data } = await api.post('/fusion/experience', { query, project_id, env }, { timeout })
  return data
}

export async function listProjects() {
  const { data } = await api.get('/projects')
  return data
}

export async function fetchQueryHistory(projectId: string, limit = 1) {
  const { data } = await api.post('/fusion/query-history', { project_id: projectId, limit })
  return data
}

export async function clearQueryHistory(projectId: string) {
  const { data } = await api.post('/fusion/query-history/clear', { project_id: projectId })
  return data
}

export async function registerProject(p: Record<string, string>) {
  const { data } = await api.post('/projects', p)
  return data
}

export async function deleteProject(id: string) {
  const { data } = await api.delete(`/projects/${id}`)
  return data
}

export async function uploadDocument(
  project_id: string,
  file: File,
  subdir: string,
) {
  const form = new FormData()
  form.append('file', file)
  form.append('subdir', subdir)
  const { data } = await api.post(`/projects/${project_id}/documents`, form)
  return data
}

export async function listJobs(project_id: string) {
  const { data } = await api.get(`/projects/${project_id}/documents`)
  return data
}

export async function health() {
  const { data } = await api.get('/health')
  return data
}
