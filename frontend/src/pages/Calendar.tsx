import React, { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { Post, STATUS_RU } from '../lib/types'

interface MonthCell {
  key: string
  day: number
  inMonth: boolean
  posts: Post[]
}

export default function Calendar() {
  const nav = useNavigate()
  const [cursor, setCursor] = useState(() => {
    const d = new Date()
    return new Date(d.getFullYear(), d.getMonth(), 1)
  })
  const [posts, setPosts] = useState<Post[]>([])

  useEffect(() => {
    const y = cursor.getFullYear()
    const m = cursor.getMonth()
    const from = new Date(y, m, 1).toISOString().slice(0, 10)
    const to = new Date(y, m + 1, 0).toISOString().slice(0, 10)
    api.get<{ list: Post[] }>(`/api/web/posts?date_from=${from}&date_to=${to}&limit=100`)
      .then((d) => setPosts(d.list))
      .catch(() => {})
  }, [cursor])

  const cells: MonthCell[] = []
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
  const startOffset = (first.getDay() + 6) % 7 // понедельник первым
  const daysInMonth = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate()
  const start = new Date(cursor.getFullYear(), cursor.getMonth(), 1 - startOffset)
  for (let i = 0; i < 42; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i)
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    cells.push({
      key,
      day: d.getDate(),
      inMonth: d.getMonth() === cursor.getMonth(),
      posts: posts.filter((p) => (p.publication_date ?? '').slice(0, 10) === key),
    })
  }

  const monthName = cursor.toLocaleString('ru-RU', { month: 'long', year: 'numeric' })
  const todayKey = new Date().toISOString().slice(0, 10)

  return (
    <>
      <div className="between">
        <div>
          <h1>Календарь публикаций</h1>
          <p className="subtitle" style={{ textTransform: 'capitalize' }}>{monthName}</p>
        </div>
        <div className="row">
          <button className="secondary" onClick={() =>
            setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}>←</button>
          <button className="secondary" onClick={() =>
            setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}>→</button>
          <Link to="/posts/new" className="btn">＋ Новый пост</Link>
        </div>
      </div>

      <div className="calendar">
        {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((d) => (
          <div key={d} className="cal-head">{d}</div>
        ))}
        {cells.map((c) => (
          <div key={c.key} className={`cal-cell ${c.inMonth ? '' : 'other'}`}
               style={c.key === todayKey ? { borderColor: 'var(--primary)' } : {}}>
            <div className="d">{c.day}</div>
            {c.posts.slice(0, 4).map((p) => {
              const cls = p.status === 'published' ? 'done' : p.status === 'failed' ? 'fail' : ''
              const time = (p.publication_date ?? '').slice(11, 16)
              return (
                <div key={p.id} className={`cal-event ${cls}`} title={p.texts[0]?.text?.slice(0, 100)}
                     onClick={() => nav(`/posts/${p.id}`)}>
                  {time && <strong>{time} </strong>}
                  {p.texts.find((t) => t.source_id === 0)?.text?.slice(0, 18)
                    ?? p.texts[0]?.text?.slice(0, 18)
                    ?? (p.media_ids.length ? `📎 ${p.media_ids.length}` : '(пусто)')}
                </div>
              )
            })}
            {c.posts.length > 4 && <div className="hint">+{c.posts.length - 4} ещё</div>}
          </div>
        ))}
      </div>
    </>
  )
}
