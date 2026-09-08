export interface SourceInfo {
  id: number
  slug: string
  name: string
  implemented: boolean
  experimental: boolean
  oauth: boolean
  capabilities: string[]
  color: string
  connect_fields?: { name: string; label: string; required: boolean }[]
}

export interface Account {
  id: number
  source_id: number
  social_account_id: string
  social_account_name: string
  social_account_photo: string
  status: string
  pages: Page[]
}

export interface Page {
  id: number
  account_id: number
  source_id: number
  social_page_id: string
  social_page_name: string
  social_page_photo: string
  page_kind: string
  can_post: boolean
}

export interface MediaFile {
  id: string
  type: 'photo' | 'video' | 'document'
  name: string
  text?: string
  title?: string
  description?: string
  folder: string
  file_path: string
  file_thumbnail_path?: string
  size?: number
  seconds?: number | null
  updated_date?: string
}

export interface PostTarget {
  id: number
  page_id: number
  source_id: number
  status: string
  external_url: string
  error: string
  attempts: number
  published_at: string | null
  page_name: string
}

export interface Post {
  id: number
  status: string
  publication_when_type: number
  publication_date: string | null
  project_id: number | null
  schedule_id: number | null
  settings: Record<string, unknown>
  repeat_type: number
  created_at: string
  texts: { source_id: number; text: string }[]
  media_ids: string[]
  document_ids: string[]
  targets: PostTarget[]
}

export interface Project {
  id: number
  name: string
  settings: Record<string, unknown>
}

export interface Schedule {
  id: number
  name: string
  slots: { days: number[]; time: string }[]
}

export interface CrosspostSource {
  id: number
  source_id: number
  name: string
  source_ref: string
  target_page_ids: number[]
  rewrite_enabled: boolean
  only_with_media: boolean
  keyword_filter: string
  poll_interval_min: number
  active: boolean
  last_polled_at: string | null
}

export interface LogEntry {
  id: number
  level: string
  message: string
  created_at: string
}

export const STATUS_RU: Record<string, string> = {
  draft: 'Черновик',
  scheduled: 'Запланирован',
  publishing: 'Публикуется',
  published: 'Опубликован',
  partial: 'Частично',
  failed: 'Ошибка',
  canceled: 'Отменён',
}

export function sourceName(sources: SourceInfo[], id: number): string {
  return sources.find((s) => s.id === id)?.name ?? `Сеть ${id}`
}

export function sourceColor(sources: SourceInfo[], id: number): string {
  return sources.find((s) => s.id === id)?.color ?? '#888'
}

export function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso.includes('T') ? iso + (iso.endsWith('Z') ? '' : 'Z') : iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
