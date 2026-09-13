import React, { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { RedGuideline } from '../../lib/types'

const TEMPLATES: Record<string, string> = {
  'Технологии / AI': `## Тематика
Технологии и искусственный интеллект: большие языковые модели, агентные системы, железо для ИИ, крупные релизы и исследования.

## Целевая аудитория
Разработчики и продакт-менеджеры, следящие за индустрией.

## Тон
Деловой, без хайпа; факты и цифры.

## Приоритетные темы
- релизы флагманских моделей
- значимые open-source проекты
- сделки и раунды ИИ-стартапов от $50M

## Запреты
- кликбейт и «шок-контент»
- слухи без подтверждения
- вакансии и курсы`,

  Крипто: `## Тематика
Криптовалюты и Web3: регулирование, крупные сделки, инфраструктура, инциденты безопасности.

## Тон
Нейтральный, без финансовых советов.

## Приоритетные темы
- регулирование (SEC, MiCA, ЦБ)
- взломы и потери > $10M
- запуск значимых протоколов

## Запреты
- прогнозы цен и сигналы
- реклама токенов`,

  Бизнес: `## Тематика
Бизнес и стартапы: раунды, M&A, стратегии, рынки.

## Тон
Деловой аналитический.

## Приоритетные темы
- раунды от $20M
- сделки M&A
- смены CEO в крупных компаниях

## Запреты
- инфоцыганские «истории успеха»`,
}

export default function RedGuidelines() {
  const [versions, setVersions] = useState<RedGuideline[]>([])
  const [content, setContent] = useState('')
  const [tags, setTags] = useState('')
  const [rescore, setRescore] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const load = () => api.get<{ list: RedGuideline[] }>('/api/web/red/guidelines')
    .then((d) => {
      setVersions(d.list)
      if (d.list.length && !content) {
        setContent(d.list[0].content)
        setTags(d.list[0].tags.join(', '))
      }
    }).catch(() => {})
  useEffect(() => { load() }, [])

  const save = async () => {
    setError(''); setInfo('')
    if (content.length > 5000) { setError('Максимум 5000 символов'); return }
    try {
      const r = await api.post<{ version: number; rescored: number }>(
        '/api/web/red/guidelines', {
          content, rescore,
          tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        })
      setInfo(`Сохранена версия ${r.version}` + (r.rescored ? `, поставлено на перескор: ${r.rescored}` : ''))
      load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка') }
  }

  return (
    <>
      <div className="between">
        <div>
          <h2>Редакционные гайдлайны</h2>
          <p className="subtitle">Промпт AI-скорера: тематика, тон, приоритеты, запреты (до 5000 символов)</p>
        </div>
      </div>
      {error && <div className="error-text">{error}</div>}
      {info && <div className="hint" style={{ color: 'var(--ok)', marginBottom: 10 }}>{info}</div>}

      <div className="panel">
        <label className="field">
          Шаблон для быстрого старта
          <select value="" onChange={(e) => { if (e.target.value && TEMPLATES[e.target.value]) setContent(TEMPLATES[e.target.value]) }}>
            <option value="">— выбрать —</option>
            {Object.keys(TEMPLATES).map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={14}
          style={{ width: '100%', fontFamily: 'monospace', fontSize: 13 }}
          placeholder="## Тематика&#10;## Целевая аудитория&#10;## Тон&#10;## Приоритетные темы&#10;## Запреты"
        />
        <div className="hint" style={{ margin: '6px 0' }}>{content.length}/5000</div>
        <div className="grid c2">
          <label className="field">
            Теги для префильтра через запятую (материал без этих слов не пойдёт в LLM)
            <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="ии, модели, стартапы" />
          </label>
          <label className="check" style={{ alignSelf: 'end', paddingBottom: 10 }}>
            <input type="checkbox" checked={rescore} onChange={(e) => setRescore(e.target.checked)} />
            Перескорить материалы за 72 часа новой версией
          </label>
        </div>
        <div className="row">
          <button onClick={save}>💾 Сохранить новую версию</button>
        </div>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Версия</th><th>Создана</th><th>Статус</th><th>Символов</th></tr></thead>
          <tbody>
            {versions.map((g) => (
              <tr key={g.id} style={{ cursor: 'pointer' }}
                  onClick={() => { setContent(g.content); setTags(g.tags.join(', ')) }}>
                <td><strong>v{g.version}</strong></td>
                <td className="hint">{new Date(g.created_at + 'Z').toLocaleString('ru-RU')}</td>
                <td><span className={`badge ${g.is_active ? 'ok' : 'mut'}`}>{g.is_active ? 'активна' : 'архив'}</span></td>
                <td className="hint">{g.content.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {versions.length === 0 && <div className="empty">Гайдлайнов ещё нет — AI-скоринг начнёт работать после первого сохранения</div>}
      </div>
    </>
  )
}
