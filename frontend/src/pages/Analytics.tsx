import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { fmtDate, sourceColor, sourceName, SourceInfo } from '../lib/types'

interface Row {
  post_id: number
  target_id: number
  source_id: number
  page_name: string
  published_at: string | null
  external_url: string
  views: number
  likes: number
  reposts: number
  comments: number
  stats_at: string | null
}

interface Totals { posts: number; views: number; likes: number; reposts: number; comments: number }
interface Data { list: Row[]; totals: { all: Totals; by_source: Record<string, Totals> } }

const PERIODS = [
  { days: 7, label: '7 дней' },
  { days: 30, label: '30 дней' },
  { days: 90, label: '90 дней' },
]

export default function Analytics({ sources }: { sources: SourceInfo[] }) {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<Data | null>(null)

  useEffect(() => {
    api.get<Data>(`/api/web/analytics?days=${days}`).then(setData).catch(() => {})
  }, [days])

  if (!data) return <div className="empty">Загрузка…</div>
  const t = data.totals.all
  const hasStats = data.list.some((r) => r.stats_at)

  return (
    <>
      <div className="between">
        <div>
          <h1>Аналитика</h1>
          <p className="subtitle">Метрики опубликованных постов (обновляются ежечасно)</p>
        </div>
        <div className="row">
          {PERIODS.map((p) => (
            <button key={p.days} className={days === p.days ? '' : 'secondary'}
                    onClick={() => setDays(p.days)}>{p.label}</button>
          ))}
        </div>
      </div>

      {!hasStats && data.list.length > 0 && (
        <div className="panel hint">
          Снимки метрик ещё не собирались — подождите час или запустите тик планировщика.
        </div>
      )}

      <div className="grid c4" style={{ marginBottom: 20 }}>
        <div className="stat"><div className="num">{t.posts}</div><div className="lbl">Публикаций</div></div>
        <div className="stat"><div className="num">{t.views.toLocaleString('ru-RU')}</div><div className="lbl">Просмотров</div></div>
        <div className="stat"><div className="num">{t.likes.toLocaleString('ru-RU')}</div><div className="lbl">Лайков</div></div>
        <div className="stat"><div className="num">{(t.reposts + t.comments).toLocaleString('ru-RU')}</div><div className="lbl">Репостов + комментариев</div></div>
      </div>

      {Object.keys(data.totals.by_source).length > 1 && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>По соцсетям</h3>
          <div className="row">
            {Object.entries(data.totals.by_source).map(([sid, v]) => (
              <span key={sid} className="chip" style={{ padding: '6px 12px' }}>
                <span className="dot" style={{ background: sourceColor(sources, Number(sid)) }} />
                {sourceName(sources, Number(sid))}: {v.views.toLocaleString('ru-RU')} просм., {v.likes.toLocaleString('ru-RU')} лайков
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Опубликовано</th><th>Сеть</th><th>Страница</th>
              <th>Просмотры</th><th>Лайки</th><th>Репосты</th><th>Коммент.</th><th></th>
            </tr>
          </thead>
          <tbody>
            {data.list.map((r) => (
              <tr key={r.target_id}>
                <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.published_at)}</td>
                <td>
                  <span className="chip">
                    <span className="dot" style={{ background: sourceColor(sources, r.source_id) }} />
                    {sourceName(sources, r.source_id)}
                  </span>
                </td>
                <td>{r.page_name}</td>
                <td>{r.views.toLocaleString('ru-RU')}</td>
                <td>{r.likes.toLocaleString('ru-RU')}</td>
                <td>{r.reposts.toLocaleString('ru-RU')}</td>
                <td>{r.comments.toLocaleString('ru-RU')}</td>
                <td>{r.external_url && <a href={r.external_url} target="_blank" rel="noreferrer" className="ghost">↗</a>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.list.length === 0 && (
          <div className="empty">Нет опубликованных постов за выбранный период. <Link to="/posts/new">Создать пост</Link></div>
        )}
      </div>
    </>
  )
}
