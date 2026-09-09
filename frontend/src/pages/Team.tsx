import React, { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface Member {
  id: number
  member_user_id: number
  email: string
  role: string
  created_at: string
}

const ROLE_RU: Record<string, string> = {
  owner: 'Владелец', admin: 'Администратор', editor: 'Редактор', viewer: 'Наблюдатель',
}

export default function Team() {
  const [data, setData] = useState<{ owner_email: string; roles: Record<string, string>; list: Member[] } | null>(null)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('editor')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const load = () => api.get<{ owner_email: string; roles: Record<string, string>; list: Member[] }>('/api/web/team')
    .then(setData).catch((e) => setError(e instanceof Error ? e.message : 'Ошибка'))
  useEffect(() => { load() }, [])

  if (!data) return <div className="empty">Загрузка…</div>

  const invite = async () => {
    setError(''); setInfo('')
    try {
      await api.post('/api/web/team/invite', { email, role })
      setEmail('')
      setInfo('Участник добавлен — пусть войдёт со своим логином')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }

  const changeRole = async (id: number, r: string) => {
    await api.put(`/api/web/team/${id}`, { role: r })
    load()
  }

  const remove = async (id: number) => {
    if (!confirm('Удалить участника из команды?')) return
    await api.del(`/api/web/team/${id}`)
    load()
  }

  return (
    <>
      <h1>Команда</h1>
      <p className="subtitle">Владелец: {data.owner_email}. Участники входят со своими
        логинами и работают с данными команды в рамках роли.</p>
      {error && <div className="error-text">{error}</div>}
      {info && <div className="hint" style={{ color: 'var(--ok)', marginBottom: 10 }}>{info}</div>}

      <div className="panel row">
        <input placeholder="email зарегистрированного пользователя" value={email}
               onChange={(e) => setEmail(e.target.value)} style={{ flex: 1 }} />
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {Object.entries(data.roles).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <button onClick={invite}>＋ Пригласить</button>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Email</th><th>Роль</th><th>Права</th><th></th></tr></thead>
          <tbody>
            {data.list.map((m) => (
              <tr key={m.id}>
                <td>{m.email}</td>
                <td>
                  <select value={m.role} onChange={(e) => changeRole(m.id, e.target.value)}>
                    {Object.entries(data.roles).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </td>
                <td className="hint">
                  {m.role === 'admin' ? 'Весь контент; без команды и тарифа'
                    : m.role === 'editor' ? 'Посты, календарь, кросспостинг'
                    : 'Только просмотр'}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <button className="ghost" style={{ color: 'var(--danger)' }}
                          onClick={() => remove(m.id)}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.list.length === 0 && <div className="empty">Пока только вы — пригласите первого участника</div>}
      </div>
    </>
  )
}
