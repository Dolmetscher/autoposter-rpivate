import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { Account, Project, SourceInfo, sourceColor, sourceName } from '../lib/types'

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
  const [editAccounts, setEditAccounts] = useState<number[]>([])
  const [editSettings, setEditSettings] = useState<Record<string, unknown>>({})
  const [error, setError] = useState('')

  const load = () => api.get<{ list: Project[] }>('/api/web/projects')
    .then((d) => setItems(d.list)).catch(() => {})
  useEffect(() => { load() }, [])

  // slug сети аккаунта: из network бэкенда, с фолбэком на справочник sources
  const netOf = (a: Account): string =>
    a.network || sources.find((s) => s.id === a.source_id)?.slug || ''
  const netColor = (a: Account): string => sourceColor(sources, a.source_id)

  // Аккаунты, сгруппированные по соцсетям: Telegram, ВКонтакте, ОК, FB и т.д.
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
    return keys.map((key) => ({ key, accounts: byNet.get(key)! }))
  }, [accounts, sources])

  const create = async () => {
    if (!name.trim()) return
    try {
      await api.post('/api/web/projects', { name, settings: {}, account_ids: [] })
      setName('')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const save = async (id: number) => {
    try {
      await api.put(`/api/web/projects/${id}`, {
        name: editName, settings: editSettings, account_ids: editAccounts,
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

  const accountName = (id: number) => {
    const a = accounts.find((x) => x.id === id)
    return a ? a.social_account_name || `Аккаунт #${a.id}` : `Аккаунт #${id}`
  }

  return (
    <>
      <h1>Проекты</h1>
      <p className="subtitle">Группировка постов по темам и направлениям. Аккаунты проекта
        публикуются синхронно: пост с этим проектом уходит во все их группы и каналы.</p>
      {error && <div className="error-text">{error}</div>}

      <div className="panel row">
        <input placeholder="Название проекта" value={name}
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => e.key === 'Enter' && create()} style={{ flex: 1 }} />
        <button onClick={create}>＋ Создать</button>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Название</th><th>Аккаунты</th><th></th></tr></thead>
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
                        return (
                          <div key={g.key} style={{ marginBottom: 10 }}>
                            <div className="row" style={{ gap: 6, margin: '4px 0 2px' }}>
                              <span className="dot" style={{ background: netColor(head) }} />
                              <strong>{sourceName(sources, head.source_id)}</strong>
                              <span className="hint">{g.accounts.length}</span>
                            </div>
                            {g.accounts.map((a) => (
                              <label key={a.id} className="check" style={{ padding: '3px 0 3px 16px' }}>
                                <input type="checkbox" checked={editAccounts.includes(a.id)}
                                       onChange={(e) => setEditAccounts((ids) =>
                                         e.target.checked ? [...ids, a.id] : ids.filter((x) => x !== a.id))} />
                                <NetBadge network={netOf(a)} color={netColor(a)} />
                                {a.social_account_name || `Аккаунт #${a.id}`}
                                <span className="hint">
                                  {a.pages.length} {plural(a.pages.length, 'страница', 'страницы', 'страниц')}
                                </span>
                              </label>
                            ))}
                          </div>
                        )
                      })}
                      {accounts.length === 0 && <span className="hint">Нет подключённых аккаунтов</span>}
                    </div>
                  ) : (
                    (p.account_ids ?? []).length
                      ? (p.account_ids ?? []).map((id) => {
                          const a = accounts.find((x) => x.id === id)
                          return (
                            <div key={id} className="row" style={{ gap: 6, padding: '1px 0' }}>
                              {a && <NetBadge network={netOf(a)} color={netColor(a)} />}
                              <span className="hint">{accountName(id)}</span>
                            </div>
                          )
                        })
                      : <span className="hint">—</span>
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
                      <button className="ghost" onClick={() => {
                        setEditing(p.id)
                        setEditName(p.name)
                        setEditAccounts(p.account_ids ?? [])
                        setEditSettings(p.settings ?? {})
                      }}>✏️</button>
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
