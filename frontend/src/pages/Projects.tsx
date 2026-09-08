import React, { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { Project } from '../lib/types'

export default function Projects() {
  const [items, setItems] = useState<Project[]>([])
  const [name, setName] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editName, setEditName] = useState('')

  const load = () => api.get<{ list: Project[] }>('/api/web/projects')
    .then((d) => setItems(d.list)).catch(() => {})
  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim()) return
    await api.post('/api/web/projects', { name, settings: {} })
    setName('')
    load()
  }

  const save = async (id: number) => {
    await api.put(`/api/web/projects/${id}`, { name: editName, settings: {} })
    setEditing(null)
    load()
  }

  const del = async (id: number) => {
    if (!confirm('Удалить проект? Посты останутся без проекта.')) return
    await api.del(`/api/web/projects/${id}`)
    load()
  }

  return (
    <>
      <h1>Проекты</h1>
      <p className="subtitle">Группировка постов по темам и направлениям</p>

      <div className="panel row">
        <input placeholder="Название проекта" value={name}
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => e.key === 'Enter' && create()} style={{ flex: 1 }} />
        <button onClick={create}>＋ Создать</button>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Название</th><th></th></tr></thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>
                  {editing === p.id ? (
                    <div className="row">
                      <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                      <button className="secondary" onClick={() => save(p.id)}>Сохранить</button>
                    </div>
                  ) : p.name}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <button className="ghost" onClick={() => { setEditing(p.id); setEditName(p.name) }}>✏️</button>
                  <button className="ghost" style={{ color: 'var(--danger)' }} onClick={() => del(p.id)}>✕</button>
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
