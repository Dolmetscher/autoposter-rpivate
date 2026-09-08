import React, { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, getToken } from '../lib/api'
import { Account, SourceInfo, sourceColor } from '../lib/types'

export default function Accounts({ sources, reload }: { sources: SourceInfo[]; reload: () => void }) {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [chosen, setChosen] = useState<number | null>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [params] = useSearchParams()

  const load = () => api.get<{ list: Account[] }>('/api/web/accounts')
    .then((d) => setAccounts(d.list)).catch(() => {})

  useEffect(() => {
    load()
    const err = params.get('error')
    const ok = params.get('connected')
    if (err) setError(decodeURIComponent(err))
    if (ok) setInfo(`Соцсеть подключена: ${ok}`)
  }, [])

  const connect = async () => {
    if (chosen == null) return
    setError('')
    setInfo('')
    try {
      const src = sources.find((s) => s.id === chosen)!
      // chat_refs идёт в extra (не токен)
      const extra: Record<string, string> = {}
      if (src.slug === 'telegram') {
        extra.chat_refs = fields.chat_refs ?? ''
      }
      const creds: Record<string, string> = {}
      for (const f of src.connect_fields ?? []) {
        if (f.name !== 'chat_refs' && fields[f.name]) creds[f.name] = fields[f.name]
      }
      await api.post('/api/web/accounts/connect', { source_id: chosen, credentials: creds, extra })
      setChosen(null)
      setFields({})
      setInfo('Аккаунт подключён')
      load()
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка подключения')
    }
  }

  const oauthStart = (slug: string) => {
    window.location.href = `/api/web/oauth/${slug}/start?token=${getToken()}`
  }

  const remove = async (id: number) => {
    if (!confirm('Отключить аккаунт и его группы?')) return
    await api.del(`/api/web/accounts/${id}`)
    load()
    reload()
  }

  const refresh = async (id: number) => {
    try {
      const r = await api.post<{ created: number }>(`/api/web/accounts/${id}/refresh`)
      setInfo(`Группы обновлены (новых: ${r.created})`)
      load()
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка обновления')
    }
  }

  const chosenSrc = sources.find((s) => s.id === chosen)

  return (
    <>
      <h1>Аккаунты соцсетей</h1>
      <p className="subtitle">Подключите профили, группы и каналы для публикации</p>
      {error && <div className="error-text">{error}</div>}
      {info && <div className="hint" style={{ color: 'var(--ok)', marginBottom: 10 }}>{info}</div>}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Доступные сети</h3>
        <div className="row">
          {sources.map((s) => (
            <button key={s.id} className="secondary"
                    style={{ opacity: s.implemented ? 1 : 0.45 }}
                    disabled={!s.implemented}
                    title={s.implemented ? '' : 'Будет добавлено в следующих версиях'}
                    onClick={() => { setChosen(s.id); setFields({}); setError(''); setInfo('') }}>
              <span className="dot" style={{ background: sourceColor(sources, s.id) }} />
              {s.name}
              {s.experimental ? ' ⚠️' : ''}
            </button>
          ))}
        </div>
        {chosenSrc && (
          <div style={{ marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
            <h4 style={{ margin: '0 0 10px' }}>Подключение: {chosenSrc.name}</h4>
            {chosenSrc.experimental && (
              <p className="hint">⚠️ Экспериментальная интеграция — может работать нестабильно.</p>
            )}
            {chosenSrc.oauth && (
              <div style={{ marginBottom: 10 }}>
                <button onClick={() => oauthStart(chosenSrc.slug)}>Подключить через OAuth</button>
                <span className="hint" style={{ marginLeft: 10 }}>или введите ключи вручную:</span>
              </div>
            )}
            {(chosenSrc.connect_fields ?? []).map((f) => (
              <label key={f.name} className="field" style={{ marginBottom: 10, maxWidth: 480 }}>
                {f.label}
                <input value={fields[f.name] ?? ''} required={f.required}
                       onChange={(e) => setFields((x) => ({ ...x, [f.name]: e.target.value }))} />
              </label>
            ))}
            <div className="row">
              <button onClick={connect}>Подключить</button>
              <button className="secondary" onClick={() => setChosen(null)}>Отмена</button>
            </div>
          </div>
        )}
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>Аккаунт</th><th>Сеть</th><th>Группы и каналы</th><th></th></tr>
          </thead>
          <tbody>
            {accounts.map((a) => {
              const src = sources.find((s) => s.id === a.source_id)
              return (
                <tr key={a.id}>
                  <td>
                    <div className="row" style={{ gap: 8 }}>
                      {a.social_account_photo
                        ? <img src={a.social_account_photo} width={28} height={28} style={{ borderRadius: '50%' }} alt="" />
                        : <span className="dot" style={{ background: sourceColor(sources, a.source_id), width: 14, height: 14 }} />}
                      {a.social_account_name || `Аккаунт #${a.id}`}
                    </div>
                  </td>
                  <td>
                    <span className="chip">
                      <span className="dot" style={{ background: src?.color }} />
                      {src?.name ?? a.source_id}
                    </span>
                  </td>
                  <td>
                    {a.pages.map((p) => (
                      <div key={p.id} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '2px 0' }}>
                        <span className="hint">#{p.social_page_id}</span> {p.social_page_name}
                      </div>
                    ))}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="ghost" onClick={() => refresh(a.id)}>🔄 Обновить</button>
                    <button className="ghost" style={{ color: 'var(--danger)' }} onClick={() => remove(a.id)}>✕</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {accounts.length === 0 && <div className="empty">Нет подключённых аккаунтов</div>}
      </div>
    </>
  )
}
