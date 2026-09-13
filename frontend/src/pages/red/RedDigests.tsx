import React, { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { Account, Page, RedDigest, RedSettings } from '../../lib/types'

const STATUS: Record<string, { cls: string; label: string }> = {
  draft: { cls: '', label: 'черновик' },
  approved: { cls: 'ok', label: 'одобрен' },
  published: { cls: 'ok', label: 'опубликован' },
  rejected: { cls: 'mut', label: 'отклонён' },
  stale: { cls: 'mut', label: 'устарел' },
}

export default function RedDigests({ accounts }: { accounts: Account[] }) {
  const [items, setItems] = useState<RedDigest[]>([])
  const [settings, setSettings] = useState<RedSettings>({ red_channel_page_ids: [], red_auto_publish: false })
  const [editing, setEditing] = useState<number | null>(null)
  const [draftText, setDraftText] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const load = () => {
    api.get<{ list: RedDigest[] }>('/api/web/red/digests').then((d) => setItems(d.list)).catch(() => {})
    api.get<RedSettings>('/api/web/red/settings').then(setSettings).catch(() => {})
  }
  useEffect(() => { load() }, [])

  const allPages: (Page & { src: number })[] = accounts.flatMap(
    (a) => a.pages.map((p) => ({ ...p, src: a.source_id })))

  const saveSettings = async (next: RedSettings) => {
    setSettings(next)
    try { await api.post('/api/web/red/settings', next) } catch (e) { setError(String(e)) }
  }

  const approve = async (id: number) => {
    setError(''); setInfo('')
    try {
      await api.post(`/api/web/red/digests/${id}/approve`)
      setInfo('Дайджест отправлен в публикацию')
      load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка') }
  }

  const reject = async (id: number) => {
    await api.post(`/api/web/red/digests/${id}/reject`)
    load()
  }

  const saveText = async (id: number) => {
    try {
      await api.put(`/api/web/red/digests/${id}/text`, { draft_text: draftText })
      setEditing(null)
      setInfo('Черновик обновлён (версия инкрементирована)')
      load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка') }
  }

  return (
    <>
      <div className="between">
        <div>
          <h2>Дайджесты</h2>
          <p className="subtitle">Ежедневно в 19:00 (± условный выпуск в 13:00 при ≥3 горячих материалах)</p>
        </div>
      </div>
      {error && <div className="error-text">{error}</div>}
      {info && <div className="hint" style={{ color: 'var(--ok)', marginBottom: 10 }}>{info}</div>}

      <div className="panel">
        <strong>Куда публиковать дайджесты и алерты «В канал»</strong>
        <div className="hint" style={{ margin: '4px 0 8px' }}>
          Выберите страницы из подключённых аккаунтов (Telegram-канал, VK-группа…)
        </div>
        {allPages.map((p) => (
          <label key={p.id} className="check" style={{ padding: '3px 0' }}>
            <input
              type="checkbox"
              checked={settings.red_channel_page_ids.includes(p.id)}
              onChange={(e) => saveSettings({
                ...settings,
                red_channel_page_ids: e.target.checked
                  ? [...settings.red_channel_page_ids, p.id]
                  : settings.red_channel_page_ids.filter((x) => x !== p.id),
              })} />
            {p.social_page_name}
          </label>
        ))}
        {allPages.length === 0 && <div className="hint">Сначала подключите аккаунты в разделе «Аккаунты»</div>}
        <label className="check" style={{ marginTop: 8 }}>
          <input type="checkbox" checked={settings.red_auto_publish}
                 onChange={(e) => saveSettings({ ...settings, red_auto_publish: e.target.checked })} />
          Публиковать дайджесты автоматически, без одобрения (по умолчанию выключено)
        </label>
      </div>

      {items.map((d) => {
        const st = STATUS[d.status] ?? { cls: '', label: d.status }
        return (
          <div key={d.id} className="panel">
            <div className="between">
              <div>
                <strong>Дайджест #{d.id}</strong>{' '}
                <span className={`badge ${st.cls}`}>{st.label}</span>{' '}
                <span className="badge mut">{d.dtype === 'daily19' ? '19:00' : '13:00'}</span>
                <div className="hint">
                  создан {new Date(d.created_at + 'Z').toLocaleString('ru-RU')} · версия {d.draft_version}
                  {d.published_at && ` · опубликован ${new Date(d.published_at + 'Z').toLocaleString('ru-RU')}`}
                </div>
              </div>
              {d.status === 'draft' && (
                <div className="row">
                  <button onClick={() => approve(d.id)}>✅ Публиковать</button>
                  <button className="secondary" onClick={() => {
                    setEditing(editing === d.id ? null : d.id)
                    setDraftText(d.draft_text)
                  }}>✏️ Редактировать</button>
                  <button className="secondary" onClick={() => reject(d.id)}>❌ Отклонить</button>
                </div>
              )}
            </div>
            {editing === d.id ? (
              <>
                <textarea value={draftText} onChange={(e) => setDraftText(e.target.value)}
                          rows={16} style={{ width: '100%', fontSize: 13 }} />
                <div className="row" style={{ marginTop: 8 }}>
                  <button onClick={() => saveText(d.id)}>Сохранить</button>
                  <button className="secondary" onClick={() => setEditing(null)}>Отмена</button>
                </div>
              </>
            ) : (
              <pre style={{ whiteSpace: 'pre-wrap', margin: '10px 0 0', fontSize: 13, lineHeight: 1.5 }}>
                {d.draft_text}
              </pre>
            )}
          </div>
        )
      })}
      {items.length === 0 && (
        <div className="empty">Дайджестов пока нет — первый соберётся в 19:00 по вашему часовому поясу</div>
      )}
    </>
  )
}
