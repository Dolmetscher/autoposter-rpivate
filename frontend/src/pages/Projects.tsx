import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { Account, Page, Project, SourceInfo, sourceColor, sourceName } from '../lib/types'

// Короткие подписи бейджей соцсетей
const NET_SHORT: Record<string, string> = {
  vk: 'VK', ok: 'OK', facebook: 'FB', telegram: 'TG',
  instagram: 'IG', twitter: 'X', tiktok: 'TT', youtube: 'YT',
  wordpress: 'WP', linkedin: 'IN', rutube: 'RT', pinterest: 'PIN',
  threads: 'TH', tumblr: 'TM', dzen: 'ДЗЕН', max: 'MAX', mock: 'SB',
}

// Порядок блоков: сначала основные сети (как в редакторе постов), остальные — следом
const NET_ORDER = ['telegram', 'vk', 'ok', 'facebook']

function NetBadge({ network, color }: { network: string; color: string }) {
  const label = NET_SHORT[network] ?? network.slice(0, 3).toUpperCase()
  return (
    <span className="net-badge" style={{ background: color }} title={network}>
      {label || '?'}
    </span>
  )
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few
  return many
}

export default function Projects({ sources, accounts }: { sources: SourceInfo[]; accounts: Account[] }) {
  const [items, setItems] = useState<Project[]>([])
  const [name, setName] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [editPages, setEditPages] = useState<number[]>([])
  const [editSettings, setEditSettings] = useState<Record<string, unknown>>({})
  const [error, setError] = useState('')
  const [copiedId, setCopiedId] = useState<number | null>(null)

  const load = () => api.get<{ list: Project[] }>('/api/web/projects')
    .then((d) => setItems(d.list)).catch(() => {})
  useEffect(() => { load() }, [])

  // slug сети аккаунта: из network бэкенда, с фолбэком на справочник sources
  const netOf = (a: Account): string =>
    a.network || sources.find((s) => s.id === a.source_id)?.slug || ''
  const netColor = (a: Account): string => sourceColor(sources, a.source_id)

  // Все страницы пользователя с их аккаунтами (для имён и бейджей)
  const pageById = useMemo(() => {
    const m = new Map<number, { page: Page; account: Account }>()
    for (const a of accounts) for (const p of a.pages) m.set(p.id, { page: p, account: a })
    return m
  }, [accounts])

  // Блоки по соцсетям; внутри блока — страницы всех аккаунтов этой сети
  // (только те, куда можно публиковать)
  const groups = useMemo(() => {
    const byNet = new Map<string, Account[]>()
    for (const a of accounts) {
      const key = netOf(a) || `s${a.source_id}`
      if (!byNet.has(key)) byNet.set(key, [])
      byNet.get(key)!.push(a)
    }
    const keys = [...byNet.keys()].sort((x, y) => {
      const ix = NET_ORDER.indexOf(x), iy = NET_ORDER.indexOf(y)
      if (ix !== -1 || iy !== -1) return (ix === -1 ? 99 : ix) - (iy === -1 ? 99 : iy)
      return x.localeCompare(y)
    })
    return keys
      .map((key) => ({
        key,
        accounts: byNet.get(key)!,
        pages: byNet.get(key)!.flatMap((a) =>
          a.pages.filter((p) => p.can_post).map((p) => ({ page: p, account: a }))),
      }))
      .filter((g) => g.pages.length > 0)
  }, [accounts, sources])

  const create = async () => {
    if (!name.trim()) return
    try {
      await api.post('/api/web/projects', { name, settings: {}, page_ids: [] })
      setName('')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const save = async (id: number) => {
    // Аккаунт привязываем целиком, только если выбраны все его страницы:
    // тогда его новые страницы тоже автоматически попадут в проект.
    // Частично выбранные аккаунты живут в page_ids постранично.
    const account_ids = accounts
      .filter((a) => a.pages.length > 0 && a.pages.every((p) => editPages.includes(p.id)))
      .map((a) => a.id)
    try {
      await api.put(`/api/web/projects/${id}`, {
        name: editName, settings: editSettings, account_ids, page_ids: editPages,
      })
      setEditing(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const del = async (id: number) => {
    if (!confirm('Удалить проект? Посты останутся без проекта.')) return
    await api.del(`/api/web/projects/${id}`)
    load()
  }

  // Открытие редактора: в выбор попадают и прямые страницы проекта,
  // и страницы его аккаунтов (старые проекты, где проект = аккаунты)
  const openEdit = (p: Project) => {
    const ids = new Set<number>(p.page_ids ?? [])
    for (const aid of p.account_ids ?? []) {
      accounts.find((x) => x.id === aid)?.pages.forEach((pg) => ids.add(pg.id))
    }
    setEditing(p.id)
    setEditName(p.name)
    setEditPages([...ids])
    setEditSettings(p.settings ?? {})
  }

  // Список страниц проекта для режима просмотра
  const projectPageIds = (p: Project): number[] => {
    const ids = new Set<number>(p.page_ids ?? [])
    for (const aid of p.account_ids ?? []) {
      accounts.find((x) => x.id === aid)?.pages.forEach((pg) => ids.add(pg.id))
    }
    return [...ids]
  }

  const copyRss = async (p: Project) => {
    if (!p.rss_url) return
    try {
      await navigator.clipboard.writeText(p.rss_url)
    } catch {
      // clipboard недоступен (http) — выделение URL вручную остаётся вариантом
      window.prompt('Скопируйте ссылку на фид:', p.rss_url)
      return
    }
    setCopiedId(p.id)
    setTimeout(() => setCopiedId((cur) => (cur === p.id ? null : cur)), 2000)
  }

  return (
    <>
      <h1>Проекты</h1>
      <p className="subtitle">Группировка постов по темам и направлениям. Страницы проекта
        публикуются синхронно: пост с этим проектом уходит во все выбранные группы и каналы.</p>
      {error && <div className="error-text">{error}</div>}

      <div className="panel row">
        <input placeholder="Название проекта" value={name}
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => e.key === 'Enter' && create()} style={{ flex: 1 }} />
        <button onClick={create}>＋ Создать</button>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Название</th><th>Страницы</th><th></th></tr></thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>
                  {editing === p.id ? (
                    <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                  ) : p.name}
                </td>
                <td>
                  {editing === p.id ? (
                    <div>
                      {groups.map((g) => {
                        const head = g.accounts[0]
                        const blockIds = g.pages.map((x) => x.page.id)
                        const allSelected = blockIds.every((id) => editPages.includes(id))
                        // одинаковые имена страниц в блоке уточняем аккаунтом
                        const nameCount = new Map<string, number>()
                        for (const x of g.pages) {
                          nameCount.set(x.page.social_page_name,
                            (nameCount.get(x.page.social_page_name) ?? 0) + 1)
                        }
                        return (
                          <div key={g.key} style={{ marginBottom: 10 }}>
                            <div className="row" style={{ gap: 6, margin: '4px 0 2px' }}>
                              <span className="dot" style={{ background: netColor(head) }} />
                              <strong>{sourceName(sources, head.source_id)}</strong>
                              <span className="hint">
                                {blockIds.length} {plural(blockIds.length, 'страница', 'страницы', 'страниц')}
                              </span>
                              <button className="ghost" style={{ fontSize: 12, padding: '0 4px' }}
                                      onClick={() => setEditPages((ids) => allSelected
                                        ? ids.filter((x) => !blockIds.includes(x))
                                        : [...new Set([...ids, ...blockIds])])}>
                                {allSelected ? 'снять все' : 'выбрать все'}
                              </button>
                            </div>
                            {g.pages.map(({ page, account }) => {
                              const dup = (nameCount.get(page.social_page_name) ?? 0) > 1
                              return (
                                <label key={page.id} className="check" style={{ padding: '3px 0 3px 16px' }}>
                                  <input type="checkbox" checked={editPages.includes(page.id)}
                                         onChange={(e) => setEditPages((ids) =>
                                           e.target.checked ? [...ids, page.id] : ids.filter((x) => x !== page.id))} />
                                  {page.social_page_name || `Страница #${page.id}`}
                                  <span className="hint">
                                    ({page.page_kind}{dup ? ` · ${account.social_account_name}` : ''})
                                  </span>
                                </label>
                              )
                            })}
                          </div>
                        )
                      })}
                      {groups.length === 0 && (
                        <span className="hint">Нет подключённых страниц. Подключите аккаунт в разделе «Аккаунты».</span>
                      )}
                    </div>
                  ) : (
                    (() => {
                      const ids = projectPageIds(p)
                      return (
                        <>
                          {ids.length
                            ? ids.map((pid) => {
                                const rec = pageById.get(pid)
                                return rec ? (
                                  <div key={pid} className="row" style={{ gap: 6, padding: '1px 0' }}>
                                    <NetBadge network={netOf(rec.account)} color={netColor(rec.account)} />
                                    <span className="hint">{rec.page.social_page_name}</span>
                                  </div>
                                ) : (
                                  <div key={pid} className="hint">Страница #{pid}</div>
                                )
                              })
                            : <span className="hint">—</span>}
                          {p.rss_url && (
                            <div style={{ marginTop: 8, borderTop: '1px dashed var(--border)', paddingTop: 6, maxWidth: 420 }}>
                              <div className="hint" title="Дзен забирает посты проекта из этого фида (трансляция канала)">
                                Дзен: RSS-фид проекта
                              </div>
                              <div className="row" style={{ gap: 6 }}>
                                <code style={{ fontSize: 11, wordBreak: 'break-all', flex: 1 }}>{p.rss_url}</code>
                                <button className="ghost" style={{ fontSize: 12 }}
                                        title="Скопировать ссылку фида"
                                        onClick={() => copyRss(p)}>
                                  {copiedId === p.id ? '✓' : '📋'}
                                </button>
                              </div>
                              <div className="hint" style={{ fontSize: 11 }}>
                                Вставьте ссылку в настройках трансляции канала Дзена — посты проекта попадут туда автоматически.
                              </div>
                            </div>
                          )}
                        </>
                      )
                    })()
                  )}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                  {editing === p.id ? (
                    <>
                      <button className="ghost" onClick={() => save(p.id)}>Сохранить</button>
                      <button className="ghost" onClick={() => setEditing(null)}>✕</button>
                    </>
                  ) : (
                    <>
                      <button className="ghost" onClick={() => openEdit(p)}>✏️</button>
                      <button className="ghost" style={{ color: 'var(--danger)' }} onClick={() => del(p.id)}>✕</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && <div className="empty">Проектов пока нет</div>}
      </div>
    </>
  )
}
