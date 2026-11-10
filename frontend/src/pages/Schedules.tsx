import React, { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { Schedule } from '../lib/types'

const DAYS = [
  { n: 1, l: 'Пн' }, { n: 2, l: 'Вт' }, { n: 3, l: 'Ср' }, { n: 4, l: 'Чт' },
  { n: 5, l: 'Пт' }, { n: 6, l: 'Сб' }, { n: 7, l: 'Вс' },
]

const ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]

const toMin = (t: string) => {
  const [h, m] = (t || '').split(':').map(Number)
  return (h || 0) * 60 + (m || 0)
}

const fmtMin = (m: number) =>
  `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`

// «1 пост», «3 поста», «6 постов» — для названия-автогенератора
const pluralPosts = (n: number) => {
  const d10 = n % 10, d100 = n % 100
  if (d10 === 1 && d100 !== 11) return 'пост'
  if ([2, 3, 4].includes(d10) && ![12, 13, 14].includes(d100)) return 'поста'
  return 'постов'
}

// Базовая сетка: окно [от, до] делится на count равных шагов, первый слот —
// у начала окна (после округления получается, напр., 10:01), последний —
// за шаг до конца. Для превью — детерминированная, без случайного сдвига.
const baseTimes = (count: number, from: string, to: string): number[] => {
  const start = toMin(from)
  let end = toMin(to)
  if (end <= start) end = start + 60
  const n = Math.max(1, Math.min(24, Math.round(count) || 1))
  if (n === 1) return [Math.floor((start + end) / 2)]
  const step = (end - start) / n
  return Array.from({ length: n }, (_, i) =>
    Math.max(start, Math.min(end, Math.round(start + step * i))))
}

// Те же точки, но с лёгким случайным сдвигом (до ~7 минут) — время выглядит
// естественным (10:01, 12:08…), а не как точные ровные интервалы.
const genTimes = (count: number, from: string, to: string): string[] => {
  const start = toMin(from)
  let end = toMin(to)
  if (end <= start) end = start + 60
  const n = Math.max(1, Math.min(24, Math.round(count) || 1))
  if (n === 1) return [fmtMin(Math.floor((start + end) / 2))]
  const step = (end - start) / n
  const jmax = Math.max(1, Math.min(7, Math.round(step / 10)))
  const out: number[] = []
  for (let i = 0; i < n; i++) {
    const base = start + step * i
    const jitter = i === 0
      ? 1 + Math.floor(Math.random() * 4) // первый — чуть после начала окна
      : Math.round(Math.random() * 2 * jmax) - jmax
    out.push(Math.max(start, Math.min(end, Math.round(base) + jitter)))
  }
  out.sort((a, b) => a - b)
  for (let i = 1; i < out.length; i++) {
    if (out[i] <= out[i - 1]) out[i] = Math.min(end, out[i - 1] + 1) // развести совпавшие
  }
  return out.map(fmtMin)
}

function DaysRow({ days, onChange }: { days: number[]; onChange: (d: number[]) => void }) {
  return (
    <div className="row" style={{ gap: 4 }}>
      {DAYS.map((d) => (
        <button key={d.n}
                className="secondary"
                style={{
                  padding: '6px 9px',
                  background: days.includes(d.n) ? 'var(--primary)' : '#eef1f8',
                  color: days.includes(d.n) ? '#fff' : 'var(--text)',
                }}
                onClick={() => onChange(days.includes(d.n)
                  ? days.filter((x) => x !== d.n)
                  : [...days, d.n])}>{d.l}</button>
      ))}
    </div>
  )
}

export default function Schedules() {
  const [items, setItems] = useState<Schedule[]>([])
  const [name, setName] = useState('')
  const [slots, setSlots] = useState<{ days: number[]; time: string }[]>([
    { days: [1, 2, 3, 4, 5], time: '10:00' },
  ])

  // автосоздание
  const [autoName, setAutoName] = useState('')
  const [autoCount, setAutoCount] = useState(6)
  const [autoFrom, setAutoFrom] = useState('10:00')
  const [autoTo, setAutoTo] = useState('23:00')
  const [autoDays, setAutoDays] = useState<number[]>(ALL_DAYS)
  const [autoErr, setAutoErr] = useState('')

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

  const autoCreate = async () => {
    setAutoErr('')
    if (toMin(autoTo) <= toMin(autoFrom)) {
      setAutoErr('Время «до» должно быть позже времени «от»')
      return
    }
    if (autoDays.length === 0) {
      setAutoErr('Выберите хотя бы один день недели')
      return
    }
    const times = genTimes(autoCount, autoFrom, autoTo)
    const fallback = `Авто: ${times.length} ${pluralPosts(times.length)}/день (${autoFrom}–${autoTo})`
    await api.post('/api/web/schedules', {
      name: autoName.trim() || fallback,
      slots: times.map((t) => ({ days: autoDays, time: t })),
    })
    setAutoName('')
    load()
  }

  const del = async (id: number) => {
    if (!confirm('Удалить расписание?')) return
    await api.del(`/api/web/schedules/${id}`)
    load()
  }

  const updSlot = (i: number, patch: Partial<{ days: number[]; time: string }>) =>
    setSlots((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)))

  const windowOk = toMin(autoTo) > toMin(autoFrom)
  const preview = windowOk ? baseTimes(autoCount, autoFrom, autoTo).map(fmtMin).join(', ') : ''

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
            <DaysRow days={s.days} onChange={(days) => updSlot(i, { days })} />
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

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Автосоздание: равномерная сетка</h3>
        <p className="hint" style={{ marginTop: 0 }}>
          Укажите, сколько постов в сутки выпускать и в каком временном окне —
          слоты расставятся через примерно равные промежутки с лёгким случайным
          сдвигом, чтобы время выглядело естественным.
        </p>
        <div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
          <label className="field" style={{ width: 170 }}>
            Постов в сутки (1–24)
            <input type="number" min={1} max={24} value={autoCount}
                   onChange={(e) => setAutoCount(Math.max(1, Math.min(24, Number(e.target.value) || 1)))} />
          </label>
          <label className="field" style={{ width: 130 }}>
            Окно с
            <input type="time" value={autoFrom} onChange={(e) => setAutoFrom(e.target.value)} />
          </label>
          <label className="field" style={{ width: 130 }}>
            до
            <input type="time" value={autoTo} onChange={(e) => setAutoTo(e.target.value)} />
          </label>
        </div>
        <div className="row" style={{ margin: '8px 0' }}>
          <DaysRow days={autoDays} onChange={setAutoDays} />
        </div>
        <label className="field" style={{ maxWidth: 360, marginBottom: 12 }}>
          Название (необязательно)
          <input value={autoName} onChange={(e) => setAutoName(e.target.value)}
                 placeholder={windowOk
                   ? `Авто: ${Math.min(24, Math.round(autoCount) || 1)} ${pluralPosts(Math.min(24, Math.round(autoCount) || 1))}/день (${autoFrom}–${autoTo})`
                   : 'Авто: …'} />
        </label>
        {windowOk ? (
          <p className="hint">Примерная сетка: {preview}</p>
        ) : (
          <p className="hint" style={{ color: 'var(--danger)' }}>Время «до» должно быть позже времени «от»</p>
        )}
        {autoErr && <p className="hint" style={{ color: 'var(--danger)' }}>{autoErr}</p>}
        <button onClick={autoCreate}>Создать расписание</button>
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
