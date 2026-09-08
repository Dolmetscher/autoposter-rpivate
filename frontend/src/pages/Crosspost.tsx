import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { Account, CrosspostSource, sourceColor, sourceName, SourceInfo } from '../lib/types'

export default function Crosspost({ sources, accounts }: { sources: SourceInfo[]; accounts: Account[] }) {
  const [items, setItems] = useState<CrosspostSource[]>([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({
    source_id: 9, source_ref: '', name: '', rewrite_enabled: false,
    only_with_media: false, keyword_filter: '', poll_interval_min: 15,
    target_page_ids: [] as number[],
  })
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const load = () => api.get<{ list: CrosspostSource[] }>('/api/web/crosspost')
    .then((d) => setItems(d.list)).catch(() => {})
  useEffect(() => { load() }, [])

  const crosspostable = sources.filter((s) => [1, 9, 17].includes(s.id))
  const allPages = useMemo(
    () => accounts.flatMap((a) => a.pages.map((p) => ({ ...p, src: a.source_id }))),
    [accounts],
  )

  const create = async () => {
    setError('')
    if (!form.source_ref.trim()) { setError('Укажите источник'); return }
    if (form.target_page_ids.length === 0) { setError('Выберите, куда копировать'); return }
    try {
      await api.post('/api/web/crosspost', form)
      setCreating(false)
      setForm({ ...form, source_ref: '', name: '', target_page_ids: [] })
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const run = async (id: number) => {
    setInfo('')
    setError('')
    try {
      const r = await api.post<{ created: number }>(`/api/web/crosspost/${id}/run`)
      setInfo(`Скопировано постов: ${r.created}`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка опроса')
    }
  }

  const toggleActive = async (x: CrosspostSource) => {
    await api.put(`/api/web/crosspost/${x.id}`, { ...x })
    load()
  }

  const del = async (id: number) => {
    if (!confirm('Удалить источник?')) return
    await api.del(`/api/web/crosspost/${id}`)
    load()
  }

  return (
    <>
      <div className="between">
        <div>
          <h1>Кросспостинг</h1>
          <p className="subtitle">Автоматическое копирование постов из чужих каналов к себе</p>
        </div>
        <button onClick={() => setCreating(!creating)}>＋ Добавить источник</button>
      </div>
      {error && <div className="error-text">{error}</div>}
      {info && <div className="hint" style={{ color: 'var(--ok)', marginBottom: 10 }}>{info}</div>}

      {creating && (
        <div className="panel">
          <div className="grid c2">
            <label className="field">
              Соцсеть источника
              <select value={form.source_id} onChange={(e) => setForm({ ...form, source_id: Number(e.target.value) })}>
                {crosspostable.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label className="field">
              Источник ({form.source_id === 1 ? 'id группы ВК, напр. -12345' :
                         form.source_id === 9 ? '@канал или t.me/канал' :
                         'channel_id YouTube (UC…)'})
              <input value={form.source_ref} onChange={(e) => setForm({ ...form, source_ref: e.target.value })} />
            </label>
            <label className="field">
              Название (необязательно)
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="field">
              Интервал опроса, минут
              <input type="number" min={5} value={form.poll_interval_min}
                     onChange={(e) => setForm({ ...form, poll_interval_min: Number(e.target.value) })} />
            </label>
          </div>
          <label className="field" style={{ margin: '10px 0' }}>
            Слова-фильтры через запятую (пусто = все посты)
            <input value={form.keyword_filter} onChange={(e) => setForm({ ...form, keyword_filter: e.target.value })} />
          </label>
          <div className="row" style={{ marginBottom: 12 }}>
            <label className="check">
              <input type="checkbox" checked={form.rewrite_enabled}
                     onChange={(e) => setForm({ ...form, rewrite_enabled: e.target.checked })} />
              Рерайт текстов через AI
            </label>
            <label className="check">
              <input type="checkbox" checked={form.only_with_media}
                     onChange={(e) => setForm({ ...form, only_with_media: e.target.checked })} />
              Только с фото/видео
            </label>
          </div>
          <div style={{ marginBottom: 8, fontWeight: 500 }}>Куда копировать:</div>
          {allPages.map((p) => (
            <label key={p.id} className="check" style={{ padding: '3px 0' }}>
              <input type="checkbox" checked={form.target_page_ids.includes(p.id)} onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  target_page_ids: e.target.checked
                    ? [...f.target_page_ids, p.id]
                    : f.target_page_ids.filter((x) => x !== p.id),
                }))} />
              <span className="dot" style={{ background: sourceColor(sources, p.src) }} />
              {p.social_page_name}
            </label>
          ))}
          {allPages.length === 0 && <div className="hint">Сначала подключите аккаунты в разделе «Аккаунты»</div>}
          <div className="row" style={{ marginTop: 12 }}>
            <button onClick={create}>Создать</button>
            <button className="secondary" onClick={() => setCreating(false)}>Отмена</button>
          </div>
        </div>
      )}

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Источник</th><th>Настройки</th><th>Куда</th><th>Опрошен</th><th></th></tr></thead>
          <tbody>
            {items.map((x) => (
              <tr key={x.id}>
                <td>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="dot" style={{ background: sourceColor(sources, x.source_id) }} />
                    <strong>{x.name || x.source_ref}</strong>
                  </div>
                  <div className="hint">{sourceName(sources, x.source_id)} · {x.source_ref}</div>
                </td>
                <td>
                  <div className="hint">
                    каждые {x.poll_interval_min} мин
                    {x.rewrite_enabled ? ' · рерайт AI' : ''}
                    {x.only_with_media ? ' · только с медиа' : ''}
                    {x.keyword_filter ? ` · фильтр: ${x.keyword_filter}` : ''}
                  </div>
                  <span className={`badge ${x.active ? 'ok' : 'mut'}`}>{x.active ? 'активен' : 'пауза'}</span>
                </td>
                <td>
                  {x.target_page_ids.map((pid) => {
                    const p = allPages.find((y) => y.id === pid)
                    return <div key={pid} className="hint">{p?.social_page_name ?? `#${pid}`}</div>
                  })}
                </td>
                <td className="hint">{x.last_polled_at ? new Date(x.last_polled_at + 'Z').toLocaleString('ru-RU') : '—'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="ghost" onClick={() => run(x.id)}>▶ Проверить</button>
                  <button className="ghost" onClick={() => api.put(`/api/web/crosspost/${x.id}`, { ...x, active: !x.active }).then(load)}>
                    {x.active ? '⏸' : '▶'}
                  </button>
                  <button className="ghost" style={{ color: 'var(--danger)' }} onClick={() => del(x.id)}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && <div className="empty">Источников пока нет</div>}
      </div>
    </>
  )
}
