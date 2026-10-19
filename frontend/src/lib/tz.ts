// Работа с датами публикации.
//
// Бэкенд хранит publication_date в naive-UTC, а пользователь задаёт время
// публикации в своей таймзоне (Europe/Moscow по умолчанию). Поэтому дату
// из API нужно переводить в таймзону пользователя, а не в таймзону браузера:
// иначе редактор показывает и сохраняет время со сдвигом на разницу часов.
import { useEffect, useState } from 'react'
import { api } from './api'

let cached: string | null = null

/** Таймзона пользователя из настроек (один запрос за сессию). */
export async function userTimezone(): Promise<string> {
  if (cached) return cached
  try {
    const d = await api.get<{ timezone: string }>('/api/web/settings')
    cached = d.timezone || 'UTC'
  } catch {
    cached = 'UTC'
  }
  return cached
}

/** Хук с таймзоной пользователя ('' — ещё не загружена). */
export function useUserTz(): string {
  const [tz, setTz] = useState<string>(cached ?? '')
  useEffect(() => {
    if (!cached) userTimezone().then(setTz)
    else setTz(cached)
  }, [])
  return tz
}

function toDate(iso: string): Date | null {
  const raw = iso.includes('T') ? iso : `${iso}T00:00:00`
  const d = new Date(raw.endsWith('Z') ? raw : `${raw}Z`)
  return isNaN(d.getTime()) ? null : d
}

function parts(d: Date, tz?: string): Record<string, string> {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    ...(tz ? { timeZone: tz } : {}),
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  })
  const out: Record<string, string> = {}
  for (const p of fmt.formatToParts(d)) out[p.type] = p.value
  return out
}

/**
 * Дата и время (YYYY-MM-DD / HH:MM) в таймзоне пользователя.
 * Значение из API считается naive-UTC.
 */
export function zonedParts(iso: string | null, tz?: string): { date: string; time: string } {
  if (!iso) return { date: '', time: '' }
  const d = toDate(iso)
  if (!d) return { date: '', time: '' }
  const p = parts(d, tz)
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` }
}

/** Дата в формате YYYY-MM-DD в таймзоне пользователя. */
export function zonedDay(iso: string | null, tz?: string): string {
  return zonedParts(iso, tz).date
}

/** Форматирование даты для интерфейса в таймзоне пользователя. */
export function fmtDateIn(iso: string | null, tz?: string): string {
  if (!iso) return '—'
  const d = toDate(iso)
  if (!d) return iso
  return d.toLocaleString('ru-RU', {
    ...(tz ? { timeZone: tz } : {}),
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
