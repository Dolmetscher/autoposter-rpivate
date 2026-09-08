import React, { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { Schedule } from '../lib/types'

const DAYS = [
  { n: 1, l: 'Пн' }, { n: 2, l: 'Вт' }, { n: 3, l: 'Ср' }, { n: 4, l: 'Чт' },
  { n: 5, l: 'Пт' }, { n: 6, l: 'Сб' }, { n: 7, l: 'Вс' },
]

export default function Schedules() {
  const [items, setItems] = useState<Schedule[]>([])
  const [name, setName] = useState('')
  const [slots, setSlots] = useState<{ days: number[]; time: string }[]>([
    { days: [1, 2, 3, 4, 5], time: '10:00' },
  ])

  const load = () => api.get<{ list: Schedule[] }>('/api/web/schedules')
    .then((d) => setItems(d.list)).catch(() => {})
  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim() || slots.length === 0) return
    await api.post('/api/web/schedules', { name, slots })
    setName('')
    setSlots([{ days: [1, 2, 3, 4, 5], time: '10:00' }])
    load()
  }

  const del = async (id: number) => {
    if (!confirm('Удалить расписание?')) return
    await api.del(`/api/web/schedules/${id}`)
    load()
  }

  const updSlot = (i: number, patch: Partial<{ days: number[]; time: string }>) =>
    setSlots((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)))

  return (
    <>
      <h1>Расписания</h1>
      <p className="subtitle">Посты с типом «по расписанию» публикуются в ближайший свободный слот</p>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Новое расписание</h3>
        <label className="field" style={{ maxWidth: 360, marginBottom: 12 }}>
          Название
          <input value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Напр. По будням утром" />
        </label>
        {slots.map((s, i) => (
          <div key={i} className="row" style={{ marginBottom: 8 }}>
            <div className="row" style={{ gap: 4 }}>
              {DAYS.map((d) => (
                <button key={d.n}
                        className="secondary"
                        style={{
                          padding: '6px 9px',
                          background: s.days.includes(d.n) ? 'var(--primary)' : '#eef1f8',
                          color: s.days.includes(d.n) ? '#fff' : 'var(--text)',
                        }}
                        onClick={() => updSlot(i, {
                          days: s.days.includes(d.n)
                            ? s.days.filter((x) => x !== d.n)
                            : [...s.days, d.n],
                        })}>{d.l}</button>
              ))}
            </div>
            <input type="time" value={s.time} onChange={(e) => updSlot(i, { time: e.target.value })} />
            {slots.length > 1 && (
              <button className="ghost" style={{ color: 'var(--danger)' }}
                      onClick={() => setSlots((x) => x.filter((_, j) => j !== i))}>✕</button>
            )}
          </div>
        ))}
        <div className="row">
          <button className="secondary" onClick={() => setSlots((s) => [...s, { days: [1, 2, 3, 4, 5], time: '18:00' }])}>
            ＋ Добавить слот
          </button>
          <button onClick={create}>Создать</button>
        </div>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Название</th><th>Слоты</th><th></th></tr></thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>
                  {s.slots.map((sl, i) => (
                    <div key={i} className="hint">
                      {sl.time} · {sl.days.length === 7
                        ? 'ежедневно'
                        : sl.days.map((d) => DAYS.find((x) => x.n === d)?.l).join(', ')}
                    </div>
                  ))}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <button className="ghost" style={{ color: 'var(--danger)' }} onClick={() => del(s.id)}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && <div className="empty">Расписаний пока нет</div>}
      </div>
    </>
  )
}
