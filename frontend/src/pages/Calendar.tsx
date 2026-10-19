import React, { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useUserTz, zonedDay, zonedParts } from '../lib/tz'
import { Post } from '../lib/types'

interface MonthCell {
  key: string
  day: number
  inMonth: boolean
  posts: Post[]
}

const pad = (n: number) => String(n).padStart(2, '0')

export default function Calendar() {
  const nav = useNavigate()
  const tz = useUserTz()
  // месяц задаём числами (год/месяц), а не Date: календарная дата не зависит
  // от таймзоны, а Date привязан к часовому поясу браузера
  const [cursor, setCursor] = useState<{ y: number; m: number } | null>(null)
  const [posts, setPosts] = useState<Post[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (cursor || !tz) return
    const today = zonedParts(new Date().toISOString(), tz)
    setCursor({ y: Number(today.date.slice(0, 4)), m: Number(today.date.slice(5, 7)) - 1 })
  }, [tz, cursor])

  const y = cursor?.y ?? 0
  const m = cursor?.m ?? 0

  useEffect(() => {
    if (!cursor) return
    const from = `${cursor.y}-${pad(cursor.m + 1)}-01`
    const daysInMonth = new Date(Date.UTC(cursor.y, cursor.m + 1, 0)).getUTCDate()
    const to = `${cursor.y}-${pad(cursor.m + 1)}-${pad(daysInMonth)}`
    api.get<{ list: Post[] }>(`/api/web/posts?date_from=${from}&date_to=${to}&limit=100`)
      .then((d) => { setPosts(d.list); setError('') })
      .catch((e) => {
        // не показываем пустой календарь при ошибке — это выглядит как «пусто»
        setPosts([])
        setError(e instanceof Error ? e.message : 'Не удалось загрузить календарь')
      })
  }, [cursor])

  if (!cursor) return <div className="empty">Загрузка…</div>

  const firstWeekday = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7 // понедельник первым
  const startDate = new Date(Date.UTC(y, m, 1 - firstWeekday))
  const cells: MonthCell[] = []
  for (let i = 0; i < 42; i++) {
    const d = new Date(startDate.getTime() + i * 86400000)
    const key = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
    cells.push({
      key,
      day: d.getUTCDate(),
      inMonth: d.getUTCMonth() === m,
      posts: posts.filter((p) => zonedDay(p.publication_date, tz) === key),
    })
  }

  const monthName = new Date(Date.UTC(y, m, 1)).toLocaleString('ru-RU', {
    month: 'long', year: 'numeric', timeZone: 'UTC',
  })
  const todayKey = zonedParts(new Date().toISOString(), tz).date

  const shiftMonth = (delta: number) =>
    setCursor((c) => {
      if (!c) return c
      const total = c.y * 12 + c.m + delta
      return { y: Math.floor(total / 12), m: total % 12 }
    })

  return (
    <>
      {error && <div className="panel" style={{ marginBottom: 14, color: 'var(--danger)' }}>
        Ошибка загрузки календаря: {error}
      </div>}
      <div className="between">
        <div>
          <h1>Календарь публикаций</h1>
          <p className="subtitle" style={{ textTransform: 'capitalize' }}>{monthName}</p>
        </div>
        <div className="row">
          <button className="secondary" onClick={() => shiftMonth(-1)}>←</button>
          <button className="secondary" onClick={() => shiftMonth(1)}>→</button>
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
              const time = zonedParts(p.publication_date, tz).time
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
