import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { fmtDateIn, useUserTz } from '../lib/tz'

interface Dash {
  counts: Record<string, number>
  upcoming: { id: number; publication_date: string; title: string }[]
  logs: { id: number; level: string; message: string; created_at: string }[]
}

export default function Dashboard() {
  const [data, setData] = useState<Dash | null>(null)
  const tz = useUserTz()
  useEffect(() => {
    api.get<Dash>('/api/web/dashboard').then(setData).catch(() => {})
  }, [])
  if (!data) return <div className="empty">Загрузка…</div>

  const c = data.counts
  return (
    <>
      <div className="between">
        <div>
          <h1>Дашборд</h1>
          <p className="subtitle">Обзор публикаций и подключений</p>
        </div>
        <Link to="/posts/new" className="btn">＋ Новый пост</Link>
      </div>
      <div className="grid c4" style={{ marginBottom: 20 }}>
        <div className="stat"><div className="num">{c.accounts ?? 0}</div><div className="lbl">Аккаунтов соцсетей</div></div>
        <div className="stat"><div className="num">{c.pages ?? 0}</div><div className="lbl">Групп и каналов</div></div>
        <div className="stat"><div className="num">{c.scheduled ?? 0}</div><div className="lbl">Запланировано</div></div>
        <div className="stat"><div className="num">{c.published ?? 0}</div><div className="lbl">Опубликовано</div></div>
      </div>

      <div className="grid c2">
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Ближайшие публикации</h3>
          {data.upcoming.length === 0 && <div className="empty">Ничего не запланировано</div>}
          {data.upcoming.map((u) => (
            <div key={u.id} className="between" style={{ padding: '7px 0', borderBottom: '1px solid var(--border)' }}>
              <Link to={`/posts/${u.id}`} className="clickable" style={{ color: 'var(--text)', textDecoration: 'none' }}>
                {u.title || '(без текста)'}
              </Link>
              <span className="hint">{fmtDateIn(u.publication_date, tz)}</span>
            </div>
          ))}
        </div>
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Журнал событий</h3>
          {data.logs.length === 0 && <div className="empty">Пока пусто</div>}
          {data.logs.map((l) => (
            <div key={l.id} style={{ padding: '7px 0', borderBottom: '1px solid var(--border)' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className={`badge ${l.level === 'success' ? 'ok' : l.level === 'error' ? 'err' : 'mut'}`}>
                  {l.level === 'success' ? 'успех' : l.level === 'error' ? 'ошибка' : 'инфо'}
                </span>
                <span className="hint">{fmtDateIn(l.created_at, tz)}</span>
              </div>
              <div style={{ marginTop: 4 }}>{l.message}</div>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
