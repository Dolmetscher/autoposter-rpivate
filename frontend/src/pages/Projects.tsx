import React, { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { Account, Project } from '../lib/types'

export default function Projects({ accounts }: { accounts: Account[] }) {
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
                      {accounts.map((a) => (
                        <label key={a.id} className="check" style={{ padding: '1px 0' }}>
                          <input type="checkbox" checked={editAccounts.includes(a.id)}
                                 onChange={(e) => setEditAccounts((ids) =>
                                   e.target.checked ? [...ids, a.id] : ids.filter((x) => x !== a.id))} />
                          {accountName(a.id)}
                        </label>
                      ))}
                      {accounts.length === 0 && <span className="hint">Нет подключённых аккаунтов</span>}
                    </div>
                  ) : (
                    (p.account_ids ?? []).length
                      ? (p.account_ids ?? []).map((id) => (
                        <div key={id} className="hint">{accountName(id)}</div>
                      ))
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
