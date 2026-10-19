import React, { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { fmtDate, Post, sourceColor, sourceName, STATUS_RU, SourceInfo } from '../lib/types'

export default function Posts({ sources }: { sources: SourceInfo[] }) {
  const [posts, setPosts] = useState<Post[]>([])
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<number[]>([])
  const [myRole, setMyRole] = useState('owner')

  const location = useLocation()

  const load = async () => {
    const params = new URLSearchParams({ page: String(page) })
    if (filter) params.set('status', filter)
    const d = await api.get<{ list: Post[]; total_rows: number }>(
      `/api/web/posts?${params}`)
    setPosts(d.list)
    setTotal(d.total_rows)
  }
  useEffect(() => {
    load().catch(() => {})
    api.get<{ role: string }>('/api/web/team/my-role').then((r) => setMyRole(r.role)).catch(() => {})
  }, [page, filter, location.pathname])

  const canApprove = myRole === 'owner' || myRole === 'admin'

  const approve = async (id: number, decision: 'approved' | 'rejected') => {
    const comment = decision === 'rejected' ? (prompt('Причина отказа (необязательно):') ?? '') : ''
    await api.post(`/api/web/posts/${id}/approve`, { decision, comment })
    load()
  }

  const del = async (id: number) => {
    if (!confirm('Удалить пост?')) return
    await api.del(`/api/web/posts/${id}`)
    load()
  }

  const delSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Удалить постов: ${selected.length}?`)) return
    await Promise.all(selected.map((id) => api.del(`/api/web/posts/${id}`)))
    setSelected([])
    load()
  }

  const statusBadge = (s: string) => {
    const cls = s === 'published' ? 'ok' : s === 'failed' ? 'err'
      : s === 'scheduled' || s === 'draft' ? 'mut' : 'wait'
    return <span className={`badge ${cls}`}>{STATUS_RU[s] ?? s}</span>
  }

  const pages = Math.ceil(total / 50)

  return (
    <>
      <div className="between">
        <div>
          <h1>Посты</h1>
          <p className="subtitle">Всего: {total}</p>
        </div>
        <div className="row">
          {selected.length > 0 && <button className="danger" onClick={delSelected}>Удалить выбранные ({selected.length})</button>}
          <Link to="/posts/new" className="btn">＋ Новый пост</Link>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 14 }}>
        <select value={filter} onChange={(e) => { setFilter(e.target.value); setPage(1) }}>
          <option value="">Все статусы</option>
          <option value="scheduled">Запланированные</option>
          <option value="published">Опубликованные</option>
          <option value="failed">С ошибками</option>
          <option value="draft">Черновики</option>
        </select>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th style={{ width: 30 }}></th>
              <th>Текст</th>
              <th>Когда</th>
              <th>Куда</th>
              <th>Статус</th>
              <th>Согласование</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {posts.map((p) => {
              const main = p.texts.find((t) => t.source_id === 0)?.text
                ?? p.texts[0]?.text ?? '(без текста)'
              const ap = (p.settings?.approval ?? {}) as { required?: boolean; status?: string }
              return (
                <tr key={p.id}>
                  <td><input type="checkbox" checked={selected.includes(p.id)} onChange={(e) =>
                    setSelected((s) => e.target.checked ? [...s, p.id] : s.filter((x) => x !== p.id))} /></td>
                  <td>
                    <Link to={`/posts/${p.id}`} style={{ color: 'var(--text)', textDecoration: 'none', fontWeight: 500 }}>
                      {main.slice(0, 70)}{main.length > 70 ? '…' : ''}
                    </Link>
                    {(p.media_ids.length > 0 || p.document_ids.length > 0) && (
                      <div className="hint">📎 {p.media_ids.length + p.document_ids.length} вложений</div>
                    )}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(p.publication_date)}</td>
                  <td>
                    <div className="row" style={{ gap: 4 }}>
                      {Array.from(new Set(p.targets.map((t) => t.source_id))).map((sid) => (
                        <span key={sid} className="chip" title={sourceName(sources, sid)}>
                          <span className="dot" style={{ background: sourceColor(sources, sid) }} />
                          {sourceName(sources, sid)}
                        </span>
                      ))}
                      {p.targets.length === 0 && <span className="hint">—</span>}
                    </div>
                  </td>
                  <td>{statusBadge(p.status)}</td>
                  <td>
                    {(() => {
                      if (!ap.required) return null
                      if (ap.status === 'pending') return <span className="badge wait">На согласовании</span>
                      if (ap.status === 'approved') return <span className="badge ok">Одобрен</span>
                      if (ap.status === 'rejected') return <span className="badge err">Отклонён</span>
                      return null
                    })()}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {canApprove && ap.required && ap.status === 'pending' && (
                      <>
                        <button className="ghost" style={{ color: 'var(--ok)' }}
                                title="Одобрить"
                                onClick={() => approve(p.id, 'approved')}>✓</button>
                        <button className="ghost" style={{ color: 'var(--danger)' }}
                                title="Отклонить"
                                onClick={() => approve(p.id, 'rejected')}>✗</button>
                      </>
                    )}
                    <button className="ghost danger" style={{ color: 'var(--danger)' }} onClick={() => del(p.id)}>✕</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {posts.length === 0 && <div className="empty">Постов нет</div>}
      </div>

      {pages > 1 && (
        <div className="row" style={{ justifyContent: 'center' }}>
          <button className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>←</button>
          <span>Стр. {page} из {pages}</span>
          <button className="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>→</button>
        </div>
      )}
    </>
  )
}
