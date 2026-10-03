import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { Account } from '../lib/types'

interface LlmConfig { base_url: string; api_key: string; model: string; prompt: string }
interface NotifyConfig {
  bot_token: string
  chat_id: string | null
  on_error: boolean
  on_success: boolean
  inbox_page_ids: number[]
}
interface SettingsData { timezone: string; llm_config: LlmConfig; notify: NotifyConfig }
interface TokenRow { id: number; name: string; created_at: string; last_used_at: string | null }
interface Plan { name: string; accounts: number; posts_month: number; sources: number; price: string; price_month?: number; price_year?: number }
interface PlansData { plans: Record<string, Plan>; current: string; usage: { accounts: number; posts_month: number; sources: number } }
interface BillingSub {
  id: number; plan: string; period: string; status: string; provider: string
  amount_kop: number; created_at: string | null; paid_at: string | null; valid_until: string | null
}
interface BillingData { plans: Record<string, Plan>; current_plan: string; provider_configured: boolean; subscriptions: BillingSub[] }

const TIMEZONES = ['Europe/Moscow', 'Europe/Kaliningrad', 'Europe/Samara', 'Asia/Yekaterinburg',
  'Asia/Novosibirsk', 'Asia/Krasnoyarsk', 'Asia/Irkutsk', 'Asia/Vladivostok', 'UTC']

export default function SettingsPage({ accounts }: { accounts: Account[] }) {
  const [data, setData] = useState<SettingsData | null>(null)
  const [tokens, setTokens] = useState<TokenRow[]>([])
  const [plans, setPlans] = useState<PlansData | null>(null)
  const [bill, setBill] = useState<BillingData | null>(null)
  const [newToken, setNewToken] = useState('')
  const [info, setInfo] = useState('')
  const [error, setError] = useState('')

  const load = () => {
    api.get<SettingsData>('/api/web/settings').then(setData).catch(() => {})
    api.get<{ list: TokenRow[] }>('/api/web/settings/tokens').then((d) => setTokens(d.list)).catch(() => {})
    api.get<PlansData>('/api/web/settings/plans').then(setPlans).catch(() => {})
    api.get<BillingData>('/api/web/billing').then(setBill).catch(() => {})
  }
  useEffect(() => { load() }, [])

  const allPages = useMemo(
    () => accounts.flatMap((a) => a.pages.map((p) => ({ ...p, src: a.source_id }))),
    [accounts],
  )

  if (!data) return <div className="empty">Загрузка…</div>

  const pay = async (plan: string, period: 'month' | 'year') => {
    setError(''); setInfo('')
    try {
      const r = await api.post<{ confirmation_url: string }>('/api/web/billing/pay',
        { plan, period })
      window.location.href = r.confirmation_url
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка оплаты')
    }
  }

  const activateManually = async (plan: string, period: 'month' | 'year') => {
    setError(''); setInfo('')
    try {
      await api.post('/api/web/billing/activate', { plan, period })
      setInfo(`Тариф активирован вручную (${period === 'year' ? 'год' : 'месяц'})`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка активации')
    }
  }

  const save = async () => {
    setInfo('')
    setError('')
    try {
      const r = await api.put<SettingsData>('/api/web/settings', data)
      setData(r)
      setInfo('Настройки сохранены')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const testNotify = async () => {
    setInfo('')
    setError('')
    try {
      await api.post('/api/web/settings/notify/test')
      setInfo('Тестовое сообщение отправлено в Telegram')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const createToken = async () => {
    try {
      const r = await api.post<{ token: string }>('/api/web/settings/tokens',
        { name: 'API-токен' })
      setNewToken(r.token)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const delToken = async (id: number) => {
    if (!confirm('Удалить токен? Приложения потеряют доступ.')) return
    await api.del(`/api/web/settings/tokens/${id}`)
    load()
  }

  const llm = data.llm_config
  const notify = data.notify ?? ({ bot_token: '', chat_id: null, on_error: true, on_success: false, inbox_page_ids: [] } as NotifyConfig)

  return (
    <>
      <h1>Настройки</h1>
      <p className="subtitle">Профиль, AI-рерайт, Telegram-бот и доступ по API</p>
      {error && <div className="error-text">{error}</div>}
      {info && <div className="hint" style={{ color: 'var(--ok)', marginBottom: 10 }}>{info}</div>}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Профиль</h3>
        <label className="field" style={{ maxWidth: 300 }}>
          Часовой пояс (для расписаний)
          <select value={data.timezone} onChange={(e) => setData({ ...data, timezone: e.target.value })}>
            {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
          </select>
        </label>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>AI-рерайт (для кросспостинга)</h3>
        <p className="hint" style={{ marginTop: 0 }}>
          Любой OpenAI-совместимый провайдер: OpenAI, YandexGPT, GigaChat-шлюз, vLLM, Ollama.
        </p>
        <div className="grid c2">
          <label className="field">
            Base URL (…/v1)
            <input value={llm.base_url ?? ''} placeholder="https://api.openai.com/v1"
                   onChange={(e) => setData({ ...data, llm_config: { ...llm, base_url: e.target.value } })} />
          </label>
          <label className="field">
            API-ключ
            <input value={llm.api_key ?? ''} placeholder="sk-…"
                   onChange={(e) => setData({ ...data, llm_config: { ...llm, api_key: e.target.value } })} />
          </label>
          <label className="field">
            Модель
            <input value={llm.model ?? ''} placeholder="gpt-4o-mini"
                   onChange={(e) => setData({ ...data, llm_config: { ...llm, model: e.target.value } })} />
          </label>
        </div>
        <label className="field" style={{ marginTop: 10 }}>
          Промпт рерайта
          <textarea rows={3} value={llm.prompt ?? ''}
                    onChange={(e) => setData({ ...data, llm_config: { ...llm, prompt: e.target.value } })} />
        </label>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Telegram-бот: уведомления и «входящие»</h3>
        <p className="hint" style={{ marginTop: 0 }}>
          Создайте бота у @BotFather, вставьте токен и отправьте ему <code>/start</code> — чат привяжется
          автоматически. Затем пересылайте боту посты: он создаст черновик или опубликует в «входящие».
        </p>
        <div className="grid c2">
          <label className="field">
            Токен бота
            <input value={notify.bot_token ?? ''} placeholder="123456:ABC-DEF…"
                   onChange={(e) => setData({ ...data, notify: { ...notify, bot_token: e.target.value } })} />
          </label>
          <label className="field">
            Привязанный чат
            <input value={notify.chat_id ? String(notify.chat_id) : ''}
                   placeholder="отправьте /start боту" disabled />
          </label>
        </div>
        <div className="row" style={{ margin: '10px 0' }}>
          <label className="check">
            <input type="checkbox" checked={notify.on_error !== false}
                   onChange={(e) => setData({ ...data, notify: { ...notify, on_error: e.target.checked } })} />
            Уведомлять об ошибках публикаций
          </label>
          <label className="check">
            <input type="checkbox" checked={notify.on_success === true}
                   onChange={(e) => setData({ ...data, notify: { ...notify, on_success: e.target.checked } })} />
            Уведомлять об успешных публикациях
          </label>
        </div>
        <div style={{ marginBottom: 8, fontWeight: 500 }}>«Входящие»: публиковать сообщения бота сразу в:</div>
        {allPages.map((p) => (
          <label key={p.id} className="check" style={{ padding: '3px 0' }}>
            <input type="checkbox" checked={(notify.inbox_page_ids ?? []).includes(p.id)} onChange={(e) =>
              setData((d) => d && ({
                ...d,
                notify: {
                  ...notify,
                  inbox_page_ids: e.target.checked
                    ? [...(notify.inbox_page_ids ?? []), p.id]
                    : (notify.inbox_page_ids ?? []).filter((x) => x !== p.id),
                },
              }))} />
            {p.social_page_name}
          </label>
        ))}
        {allPages.length === 0 && <div className="hint">Сначала подключите аккаунты в разделе «Аккаунты»</div>}
        <div className="row" style={{ marginTop: 10 }}>
          <button className="secondary" onClick={testNotify}>Отправить тестовое сообщение</button>
        </div>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Тариф</h3>
        {plans && (
          <>
            <div className="hint" style={{ marginBottom: 10 }}>
              Использовано за месяц: аккаунтов {plans.usage.accounts}, постов {plans.usage.posts_month},
              {' '}источников кросспостинга {plans.usage.sources}
            </div>
            <div className="grid c4">
              {Object.entries(plans.plans).map(([key, p]) => (
                <div key={key} className="stat"
                     style={key === plans.current ? { borderColor: 'var(--primary)', borderWidth: 2 } : {}}>
                  <div className="between">
                    <strong>{p.name}</strong>
                    {key === plans.current && <span className="badge ok">текущий</span>}
                  </div>
                  <div className="num" style={{ fontSize: 18 }}>{p.price}</div>
                  <div className="lbl">аккаунтов: {p.accounts} · постов/мес: {p.posts_month} · источников: {p.sources}</div>
                  {key !== plans.current && (
                    <button className="secondary" style={{ marginTop: 8, width: '100%', justifyContent: 'center' }}
                            onClick={async () => {
                              try {
                                await api.post('/api/web/settings/plan', { plan: key })
                                setInfo(`Тариф изменён на «${p.name}»`)
                                load()
                              } catch (e) {
                                setError(e instanceof Error ? e.message : 'Ошибка')
                              }
                            }}>Переключиться</button>
                  )}
                  {key !== 'free' && (
                    <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                      {bill?.provider_configured ? (
                        <div className="row" style={{ gap: 6 }}>
                          <button style={{ flex: 1, justifyContent: 'center' }}
                                  onClick={() => pay(key, 'month')}>Месяц</button>
                          <button className="secondary" style={{ flex: 1, justifyContent: 'center' }}
                                  title="Оплата за год (10 месяцев)"
                                  onClick={() => pay(key, 'year')}>Год −17%</button>
                        </div>
                      ) : (
                        <div className="hint" style={{ marginBottom: 6 }}>
                          Онлайн-оплата не настроена (ЮKassa). Доступна ручная активация:
                        </div>
                      )}
                      <button className="ghost" style={{ width: '100%', justifyContent: 'center' }}
                              title="Деньги получены вне системы (переводом) — активировать вручную"
                              onClick={() => activateManually(key, 'month')}>Активировать вручную</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
            {bill && bill.subscriptions.length > 0 && (
              <table style={{ marginTop: 14 }}>
                <thead>
                  <tr><th>Тариф</th><th>Период</th><th>Сумма</th><th>Статус</th><th>Действует до</th><th>Способ</th></tr>
                </thead>
                <tbody>
                  {bill.subscriptions.map((x) => {
                    const cls = x.status === 'active' ? 'ok' : x.status === 'pending' ? 'wait'
                      : x.status === 'canceled' || x.status === 'expired' ? 'err' : 'mut'
                    const ru: Record<string, string> = {
                      active: 'активна', pending: 'ожидает оплаты', canceled: 'отменена', expired: 'истекла'}
                    return (
                      <tr key={x.id}>
                        <td>{bill.plans[x.plan]?.name ?? x.plan}</td>
                        <td>{x.period === 'year' ? 'год' : 'месяц'}</td>
                        <td>{(x.amount_kop / 100).toLocaleString('ru-RU')} ₽</td>
                        <td><span className={`badge ${cls}`}>{ru[x.status] ?? x.status}</span></td>
                        <td>{x.valid_until ? new Date(x.valid_until + 'Z').toLocaleDateString('ru-RU') : '—'}</td>
                        <td className="hint">{x.provider === 'manual' ? 'вручную' : 'ЮKassa'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>

      <div className="panel">
        <div className="between">
          <h3 style={{ margin: 0 }}>API-токены</h3>
          <button onClick={createToken}>＋ Создать токен</button>
        </div>
        <p className="hint">Используйте как <code>Authorization: Bearer ap_…</code> —
          формат запросов как у hooppy: <code>GET /api/accounts</code>, <code>POST /api/posts</code> и т.д.
          Полная схема: <code>/docs</code> (Swagger).</p>
        {newToken && (
          <div>
            <div className="hint">Скопируйте токен — он показывается только один раз:</div>
            <div className="token-box">{newToken}</div>
          </div>
        )}
        <table>
          <thead><tr><th>Название</th><th>Создан</th><th>Последнее использование</th><th></th></tr></thead>
          <tbody>
            {tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td className="hint">{new Date(t.created_at + 'Z').toLocaleString('ru-RU')}</td>
                <td className="hint">{t.last_used_at ? new Date(t.last_used_at + 'Z').toLocaleString('ru-RU') : '—'}</td>
                <td><button className="ghost" style={{ color: 'var(--danger)' }} onClick={() => delToken(t.id)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {tokens.length === 0 && <div className="empty">Токенов нет</div>}
      </div>

      <button onClick={save} style={{ marginTop: 4 }}>Сохранить настройки</button>
    </>
  )
}
