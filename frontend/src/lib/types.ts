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

// --- «Автоматизированная редакция» ---
export interface RedSource {
  id: number
  type: 'rss' | 'reddit' | 'bluesky' | 'tg' | 'vk'
  identifier: string
  title: string
  tags: string[]
  poll_interval_sec: number
  jitter_pct: number
  status: 'active' | 'degraded' | 'paused' | 'archived'
  fail_count: number
  last_error: string
  last_ok_at: string | null
  next_poll_at: string | null
  items_24h: number
}

export interface RedGuideline {
  id: number
  version: number
  content: string
  tags: string[]
  is_active: boolean
  created_at: string
}

export interface RedScore {
  significance: number | null
  matched_tags: string[]
  rationale: string
  digest_worthy: boolean
  category: string
  headline_ru: string
  summary_ru: string
  model: string
  guideline_version: number
}

export interface RedFeedItem {
  id: number
  source_id: number
  source_title: string
  source_type: string
  title: string
  url: string
  published_at: string | null
  fetched_at: string
  status: string
  digest_flag: boolean
  media_count: number
  score: RedScore | null
  feedback?: { action: string; note: string }[]
}

export interface RedDigest {
  id: number
  dtype: 'daily19' | 'noon13'
  period_start: string | null
  period_end: string | null
  draft_text: string
  draft_version: number
  status: 'draft' | 'approved' | 'published' | 'rejected' | 'stale'
  post_id: number | null
  published_at: string | null
  created_at: string
}

export interface RedSettings {
  red_channel_page_ids: number[]
  red_auto_publish: boolean
}

export const RED_STATUS_ICON: Record<string, string> = {
  active: '🟢', degraded: '🟡', paused: '⚪', archived: '⚫',
}
export const RED_STATUS_TEXT: Record<string, string> = {
  active: 'активен', degraded: 'деградировал', paused: 'пауза', archived: 'архив',
}
