import React, { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { RED_STATUS_ICON, RED_STATUS_TEXT, RedSource } from '../../lib/types'

const TYPES = [
  { id: 'rss', name: 'RSS/Atom', hint: 'URL фида, напр. https://blog.example/feed' },
  { id: 'reddit', name: 'Reddit', hint: 'сабреддит, напр. r/MachineLearning' },
  { id: 'bluesky', name: 'Bluesky', hint: '@name.bsky.social или поисковый запрос' },
  { id: 'tg', name: 'Telegram', hint: 'публичный канал, напр. @durov' },
  { id: 'vk', name: 'ВКонтакте', hint: 'id паблика, напр. -12345' },
]

interface Preview {
  title: string
  url: string
  media_count: number
}

export default function RedSources() {
  const [items, setItems] = useState<RedSource[]>([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ type: 'rss', identifier: '', title: '', tags: '' })
  const [preview, setPreview] = useState<Preview[] | null>(null)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [diag, setDiag] = useState<{ id: number; rows: { ok: boolean; error: string; duration_ms: number; created_at: string }[] } | null>(null)

  const load = () => api.get<{ list: RedSource[] }>('/api/web/red/sources')
    .then((d) => setItems(d.list)).catch(() => {})
  useEffect(() => { load() }, [])

  const runPreview = async () => {
    setError(''); setPreview(null); setInfo('')
    if (!form.identifier.trim()) { setError('Укажите идентификатор источника'); return }
    try {
      const r = await api.post<{ items: Preview[] }>('/api/web/red/sources/preview', form)
      setPreview(r.items)
    } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка проверки') }
  }

  const create = async () => {
    setError(''); setInfo('')
    try {
      await api.post('/api/web/red/sources', {
        ...form, tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
      })
      setCreating(false); setForm({ ...form, identifier: '', title: '', tags: '' })
      setPreview(null); setInfo('Источник добавлен')
      load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка') }
  }

  const run = async (id: number) => {
    setError(''); setInfo('')
    try {
      const r = await api.post<{ created: number }>(`/api/web/red/sources/${id}/run`)
      setInfo(`Новых материалов: ${r.created}`)
      load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка опроса') }
  }

  const toggle = async (x: RedSource) => {
    await apiFetch(`/api/web/red/sources/${x.id}`, 'PATCH', {
      type: x.type, identifier: x.identifier,
      title: x.title, tags: x.tags,
      status: x.status === 'paused' ? 'active' : 'paused',
    })
    load()
  }

  const archive = async (id: number) => {
    if (!confirm('Архивировать источник? Поллинг остановится.')) return
    await api.del(`/api/web/red/sources/${id}`)
    load()
  }

  const showDiag = async (id: number) => {
    const r = await apiFetch(`/api/web/red/sources/${id}/diagnostic`, 'GET') as
      { source: RedSource; checks: { ok: boolean; error: string; duration_ms: number; created_at: string }[] }
    setDiag({ id, rows: r.checks })
  }

  return (
    <>
      <div className="between">
        <div>
          <h2>Источники мониторинга</h2>
          <p className="subtitle">Поллинг каждые 5–10 минут с джиттером; деградация видна по индикатору</p>
        </div>
        <button onClick={() => { setCreating(!creating); setPreview(null); setError('') }}>＋ Добавить</button>
      </div>
      {error && <div className="error-text">{error}</div>}
      {info && <div className="hint" style={{ color: 'var(--ok)', marginBottom: 10 }}>{info}</div>}

      {creating && (
        <div className="panel">
          <div className="grid c2">
            <label className="field">
              Тип источника
              <select value={form.type} onChange={(e) => { setForm({ ...form, type: e.target.value }); setPreview(null) }}>
                {TYPES.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </label>
            <label className="field">
              Идентификатор ({TYPES.find((t) => t.id === form.type)?.hint})
              <input value={form.identifier} onChange={(e) => setForm({ ...form, identifier: e.target.value })} />
            </label>
            <label className="field">
              Название (необязательно)
              <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </label>
            <label className="field">
              Теги через запятую (для префильтра и алертов)
              <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="ии, стартапы" />
            </label>
          </div>
          <div className="row" style={{ margin: '10px 0' }}>
            <button className="secondary" onClick={runPreview}>🔍 Тестовый сбор</button>
          </div>
          {preview !== null && (
            preview.length ? (
              <div className="hint" style={{ marginBottom: 10 }}>
                Предпросмотр последних материалов:
                <ul style={{ margin: '6px 0 0 18px' }}>
                  {preview.map((p, i) => (
                    <li key={i}>{p.title}{p.media_count ? ` · медиа: ${p.media_count}` : ''}</li>
                  ))}
                </ul>
              </div>
            ) : <div className="hint" style={{ marginBottom: 10 }}>Источник отвечает, но материалов не вернул</div>
          )}
          <div className="row">
            <button onClick={create}>Сохранить</button>
            <button className="secondary" onClick={() => setCreating(false)}>Отмена</button>
          </div>
        </div>
      )}

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Источник</th><th>Теги</th><th>Статус</th><th>За 24 ч</th><th></th></tr></thead>
          <tbody>
            {items.map((x) => (
              <tr key={x.id}>
                <td>
                  <strong>{x.title}</strong>
                  <div className="hint">{x.type} · {x.identifier} · каждые {Math.round(x.poll_interval_sec / 60)} мин</div>
                  {x.last_error && <div className="hint" style={{ color: 'var(--danger)' }}>⚠ {x.last_error}</div>}
                </td>
                <td className="hint">{x.tags.join(', ') || '—'}</td>
                <td>
                  <span className={`badge ${x.status === 'active' ? 'ok' : x.status === 'degraded' ? 'warn' : 'mut'}`}>
                    {RED_STATUS_ICON[x.status]} {RED_STATUS_TEXT[x.status]}
                  </span>
                  <div className="hint">после сбора: {x.last_ok_at ? new Date(x.last_ok_at + 'Z').toLocaleString('ru-RU') : '—'}</div>
                </td>
                <td>{x.items_24h}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="ghost" onClick={() => run(x.id)}>▶ Собрать</button>
                  <button className="ghost" onClick={() => showDiag(x.id)}>🔍 Диагностика</button>
                  {x.status !== 'archived' && (
                    <button className="ghost" onClick={() => toggle(x)}>{x.status === 'paused' ? '▶' : '⏸'}</button>
                  )}
                  <button className="ghost" style={{ color: 'var(--danger)' }} onClick={() => archive(x.id)}>🗄</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && <div className="empty">Источников пока нет — добавьте RSS, Reddit, Bluesky, Telegram или VK</div>}
      </div>

      {diag && (
        <div className="panel">
          <div className="between">
            <strong>Диагностика: последние 20 сборов</strong>
            <button className="ghost" onClick={() => setDiag(null)}>✕</button>
          </div>
          <table>
            <thead><tr><th>Когда</th><th>Результат</th><th>Длительность</th><th>Ошибка</th></tr></thead>
            <tbody>
              {diag.rows.map((c, i) => (
                <tr key={i}>
                  <td className="hint">{new Date(c.created_at + 'Z').toLocaleString('ru-RU')}</td>
                  <td>{c.ok ? '✅ успех' : '❌ ошибка'}</td>
                  <td className="hint">{c.duration_ms} мс</td>
                  <td className="hint" style={{ color: c.ok ? undefined : 'var(--danger)' }}>{c.error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {diag.rows.length === 0 && <div className="empty">Сборов ещё не было</div>}
        </div>
      )}
    </>
  )
}

// мелкий хелпер для PATCH/GET с телом (lib.api не имеет patch)
async function apiFetch(path: string, method: string, body?: unknown) {
  const token = localStorage.getItem('autoposter_token')
  const resp = await fetch(path, {
    method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}),
               ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) throw new Error(`Ошибка ${resp.status}`)
  return resp.json()
}
