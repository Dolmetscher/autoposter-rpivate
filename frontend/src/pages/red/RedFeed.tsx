import React, { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { RedFeedItem } from '../../lib/types'

const SIG_BADGE = (sig: number | null) => {
  if (sig === null || sig === undefined) return { cls: 'mut', label: '—' }
  if (sig >= 4) return { cls: 'warn', label: `🔥 ${sig.toFixed(1)}` }
  if (sig >= 3) return { cls: 'ok', label: sig.toFixed(1) }
  return { cls: 'mut', label: sig.toFixed(1) }
}

const STATUS_TEXT: Record<string, string> = {
  new: 'новый', scored: 'оценён', filtered: 'отфильтрован', review: 'ручная проверка',
  clustered: 'дубль кластера', alerted: 'алерт отправлен', digest: 'в дайджесте',
  posted: 'опубликован', ignored: 'игнор',
}

export default function RedFeed() {
  const [items, setItems] = useState<RedFeedItem[]>([])
  const [sig, setSig] = useState('')
  const [hours, setHours] = useState(72)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [open, setOpen] = useState<number | null>(null)

  const load = () => {
    const p = new URLSearchParams({ hours: String(hours) })
    if (sig) p.set('significance', sig)
    if (status) p.set('status', status)
    api.get<{ list: RedFeedItem[] }>(`/api/web/red/items?${p.toString()}`)
      .then((d) => setItems(d.list)).catch((e) => setError(String(e.message ?? e)))
  }
  useEffect(() => { load() }, [sig, hours, status])

  const act = async (path: string) => { await api.post(path); load() }

  return (
    <>
      <div className="between">
        <div>
          <h2>Лента материалов</h2>
          <p className="subtitle">Оценки AI-скорера по гайдлайнам; горячее — от 4.0</p>
        </div>
        <div className="row">
          <select value={sig} onChange={(e) => setSig(e.target.value)}>
            <option value="">любая оценка</option>
            <option value="gte:4">≥ 4.0 (горячее)</option>
            <option value="gte:3">≥ 3.0</option>
          </select>
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={24}>за 24 ч</option>
            <option value={72}>за 72 ч</option>
            <option value={168}>за неделю</option>
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">все статусы</option>
            {Object.entries(STATUS_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>
      {error && <div className="error-text">{error}</div>}

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Оценка</th><th>Материал</th><th>Источник</th><th>Статус</th><th></th></tr></thead>
          <tbody>
            {items.map((x) => {
              const badge = SIG_BADGE(x.score?.significance ?? null)
              const expanded = open === x.id
              return (
                <React.Fragment key={x.id}>
                  <tr>
                    <td><span className={`badge ${badge.cls}`}>{badge.label}</span></td>
                    <td>
                      <strong>{x.score?.headline_ru || x.title}</strong>
                      {x.score?.summary_ru && <div className="hint">{x.score.summary_ru}</div>}
                      <div className="hint">
                        {x.source_title} · {new Date(x.fetched_at + 'Z').toLocaleString('ru-RU')}
                        {x.url && <> · <a href={x.url} target="_blank" rel="noreferrer">ссылка ↗</a></>}
                        {x.media_count ? ` · 🖼 ${x.media_count}` : ''}
                      </div>
                    </td>
                    <td className="hint">{x.score?.category || ''}</td>
                    <td><span className={`badge ${x.status === 'posted' ? 'ok' : x.status === 'ignored' ? 'mut' : ''}`}>
                      {STATUS_TEXT[x.status] || x.status}</span>
                      {x.digest_flag && <div className="hint">📅 в дайджесте</div>}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button className="ghost" onClick={() => setOpen(expanded ? null : x.id)}>
                        {expanded ? '▲' : '▼'}
                      </button>
                      <button className="ghost" title="В ближайший дайджест"
                              onClick={() => act(`/api/web/red/items/${x.id}/digest`)}>📅</button>
                      <button className="ghost" title="Игнорировать кластер"
                              onClick={() => act(`/api/web/red/items/${x.id}/ignore`)}>🗑</button>
                    </td>
                  </tr>
                  {expanded && (
                    <tr>
                      <td />
                      <td colSpan={4}>
                        {x.score ? (
                          <div className="hint" style={{ lineHeight: 1.6 }}>
                            <div><strong>Обоснование:</strong> {x.score.rationale}</div>
                            <div><strong>Теги:</strong> {x.score.matched_tags.join(', ') || '—'}
                              {' · '}<strong>Модель:</strong> {x.score.model}
                              {' · '}<strong>Гайдлайны:</strong> v{x.score.guideline_version}</div>
                            <div><strong>Digest-worthy:</strong> {x.score.digest_worthy ? 'да' : 'нет'}</div>
                            {x.title && x.score.headline_ru && x.title !== x.score.headline_ru &&
                              <div><strong>Оригинал:</strong> {x.title}</div>}
                          </div>
                        ) : <div className="hint">Материал ещё не оценён</div>}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
        {items.length === 0 && <div className="empty">Материалов нет — добавьте источники и дождитесь поллинга</div>}
      </div>
    </>
  )
}
