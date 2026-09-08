import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { Account, MediaFile, Post, Project, Schedule, SourceInfo, sourceColor, sourceName } from '../lib/types'

interface Props {
  sources: SourceInfo[]
  accounts: Account[]
}

const WHEN = [
  { id: 1, label: 'Сейчас' },
  { id: 2, label: 'По дате' },
  { id: 3, label: 'По расписанию' },
]

export default function PostEditor({ sources, accounts }: Props) {
  const { id } = useParams()
  const nav = useNavigate()
  const editing = Boolean(id)
  const fileRef = useRef<HTMLInputElement>(null)

  const [text, setText] = useState('')
  const [perSource, setPerSource] = useState<{ source_id: number; text: string }[]>([])
  const [media, setMedia] = useState<MediaFile[]>([])
  const [docs, setDocs] = useState<MediaFile[]>([])
  const [pageIds, setPageIds] = useState<number[]>([])
  const [whenType, setWhenType] = useState(1)
  const [date, setDate] = useState('')
  const [time, setTime] = useState('12:00')
  const [scheduleId, setScheduleId] = useState<number | ''>('')
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState<number | ''>('')
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(editing)

  const allPages = useMemo(
    () => accounts.flatMap((a) => a.pages.map((p) => ({ ...p, account: a }))),
    [accounts],
  )

  useEffect(() => {
    api.get<{ list: Schedule[] }>('/api/web/schedules').then((d) => setSchedules(d.list))
    api.get<{ list: Project[] }>('/api/web/projects').then((d) => setProjects(d.list))
  }, [])

  useEffect(() => {
    if (!editing) return
    api.get<Post>(`/api/web/posts/${id}`).then((p) => {
      setText(p.texts.find((t) => t.source_id === 0)?.text ?? '')
      setPerSource(p.texts.filter((t) => t.source_id !== 0))
      setPageIds(p.targets.map((t) => t.page_id))
      setWhenType(p.publication_when_type === 4 ? 2 : p.publication_when_type)
      if (p.publication_date) {
        const d = new Date(p.publication_date + (p.publication_date.endsWith('Z') ? '' : 'Z'))
        setDate(d.toISOString().slice(0, 10))
        setTime(d.toISOString().slice(11, 16))
      }
      setScheduleId(p.schedule_id ?? '')
      setProjectId(p.project_id ?? '')
      setSettings(p.settings ?? {})
      const ids = [...p.media_ids, ...p.document_ids]
      Promise.all(ids.map((mid) => api.get<MediaFile>(`/api/web/files/${mid}/raw`).catch(() => null)))
      // raw не отдаёт метаданные; получаем их списком
      api.get<{ list: MediaFile[] }>('/api/web/files').then((fl) => {
        const byId = new Map(fl.list.map((m) => [m.id, m]))
        setMedia(p.media_ids.map((mid) => byId.get(mid)).filter(Boolean) as MediaFile[])
        setDocs(p.document_ids.map((did) => byId.get(did)).filter(Boolean) as MediaFile[])
      })
      setLoading(false)
    })
  }, [editing, id])

  const upload = async (files: FileList | null, kind: 'media' | 'doc') => {
    if (!files) return
    for (const f of Array.from(files)) {
      try {
        const path = kind === 'media'
          ? (f.type.startsWith('image/') || f.type.startsWith('video/') ? '/api/web/files/media/upload' : '/api/web/files/documents/upload')
          : '/api/web/files/documents/upload'
        const res = await api.upload<MediaFile>(path, f)
        if (res.type === 'document') setDocs((d) => [...d, res])
        else setMedia((m) => [...m, res])
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      }
    }
  }

  const save = async (asDraft: boolean) => {
    setError('')
    if (!text.trim() && media.length === 0 && docs.length === 0) {
      setError('Добавьте текст или вложения')
      return
    }
    if (pageIds.length === 0) {
      setError('Выберите хотя бы одну группу/канал')
      return
    }
    if (whenType === 2 && !date) {
      setError('Укажите дату публикации')
      return
    }
    if (whenType === 3 && !scheduleId) {
      setError('Выберите расписание')
      return
    }
    setBusy(true)
    try {
      const payload = {
        texts: [
          ...(text.trim() ? [{ source_id: 0, text }] : []),
          ...perSource.filter((t) => t.text.trim()),
        ],
        media_ids: media.map((m) => m.id),
        document_ids: docs.map((d) => d.id),
        page_ids: pageIds,
        settings,
        publication_when_type: whenType,
        publication_date: whenType === 2 && date ? `${date}T${time}:00` : null,
        schedule_id: whenType === 3 ? scheduleId : null,
        project_id: projectId || null,
        status: asDraft ? 'draft' : 'scheduled',
      }
      if (editing) await api.put(`/api/web/posts/${id}`, payload)
      else await api.post('/api/web/posts', payload)
      nav('/posts')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка сохранения')
    } finally {
      setBusy(false)
    }
  }

  const publishNow = async () => {
    if (!editing) {
      setError('Сначала сохраните пост')
      return
    }
    setBusy(true)
    try {
      await api.post(`/api/web/posts/${id}/publish`)
      nav('/posts')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка публикации')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <div className="empty">Загрузка…</div>

  const set = (k: string, v: unknown) => setSettings((s) => ({ ...s, [k]: v }))
  const bool = (k: string) => Boolean(settings[k])

  return (
    <>
      <div className="between">
        <div>
          <h1>{editing ? 'Редактирование поста' : 'Новый пост'}</h1>
          <p className="subtitle">Текст, вложения и цели публикации</p>
        </div>
        <div className="row">
          <button className="secondary" disabled={busy} onClick={() => save(true)}>Сохранить черновик</button>
          <button disabled={busy} onClick={() => save(false)}>Сохранить</button>
        </div>
      </div>
      {error && <div className="error-text">{error}</div>}

      <div className="editor-grid">
        <div>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Текст</h3>
            <textarea rows={7} value={text} placeholder="Текст поста для всех соцсетей…"
                      onChange={(e) => setText(e.target.value)} />
            <details style={{ marginTop: 12 }}>
              <summary className="clickable hint">Отдельный текст для конкретной сети</summary>
              {perSource.map((t, i) => (
                <div key={i} className="row" style={{ margin: '10px 0', alignItems: 'flex-start' }}>
                  <select value={t.source_id} onChange={(e) => {
                    const v = Number(e.target.value)
                    setPerSource((p) => p.map((x, j) => (j === i ? { ...x, source_id: v } : x)))
                  }}>
                    {sources.filter((s) => s.implemented).map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                  <textarea rows={3} style={{ flex: 1 }} value={t.text}
                            placeholder={`Текст только для ${sourceName(sources, t.source_id)}…`}
                            onChange={(e) => {
                              const v = e.target.value
                              setPerSource((p) => p.map((x, j) => (j === i ? { ...x, text: v } : x)))
                            }} />
                  <button className="danger" onClick={() => setPerSource((p) => p.filter((_, j) => j !== i))}>✕</button>
                </div>
              ))}
              <button className="secondary" onClick={() => {
                const used = new Set(perSource.map((t) => t.source_id))
                const s = sources.find((x) => x.implemented && !used.has(x.id))
                if (s) setPerSource((p) => [...p, { source_id: s.id, text: '' }])
              }}>＋ Добавить</button>
            </details>
          </div>

          <div className="panel">
            <div className="between">
              <h3 style={{ margin: 0 }}>Фото, видео, документы</h3>
              <div className="row">
                <input ref={fileRef} type="file" multiple hidden
                       onChange={(e) => { upload(e.target.files, 'media'); e.target.value = '' }} />
                <button className="secondary" onClick={() => fileRef.current?.click()}>＋ Загрузить</button>
              </div>
            </div>
            <div className="media-thumbs">
              {[...media, ...docs].map((m) => (
                <div key={m.id} className="thumb"
                     style={m.type === 'photo' ? { backgroundImage: `url(/api/web/files/${m.id}/raw)` } : {}}>
                  {m.type !== 'photo' ? (m.type === 'video' ? '🎬' : '📄') + (m.seconds ? ` ${m.seconds}с` : '') : ''}
                  <div className="x" onClick={() => {
                    setMedia((x) => x.filter((i) => i.id !== m.id))
                    setDocs((x) => x.filter((i) => i.id !== m.id))
                  }}>✕</div>
                </div>
              ))}
            </div>
            {media.length === 0 && docs.length === 0 && (
              <div className="empty" style={{ padding: '16px 0' }}>Перетащите файлы через кнопку загрузки</div>
            )}
          </div>
        </div>

        <div>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Куда публиковать</h3>
            {allPages.length === 0 && (
              <div className="hint">Нет подключённых групп. Подключите аккаунт в разделе «Аккаунты».</div>
            )}
            {accounts.map((a) => (
              <div key={a.id} style={{ marginBottom: 12 }}>
                <div className="row" style={{ gap: 6, marginBottom: 6 }}>
                  <span className="dot" style={{ background: sourceColor(sources, a.source_id) }} />
                  <strong>{sourceName(sources, a.source_id)}</strong>
                  <span className="hint">{a.social_account_name}</span>
                </div>
                {a.pages.map((p) => (
                  <label key={p.id} className="check" style={{ padding: '4px 0 4px 16px' }}>
                    <input type="checkbox" checked={pageIds.includes(p.id)} onChange={(e) => {
                      setPageIds((ids) =>
                        e.target.checked ? [...ids, p.id] : ids.filter((x) => x !== p.id))
                    }} />
                    {p.social_page_name}
                    <span className="hint">({p.page_kind})</span>
                  </label>
                ))}
              </div>
            ))}
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Когда</h3>
            <div className="row">
              {WHEN.map((w) => (
                <label key={w.id} className="check">
                  <input type="radio" name="when" checked={whenType === w.id}
                         onChange={() => setWhenType(w.id)} /> {w.label}
                </label>
              ))}
            </div>
            {whenType === 2 && (
              <div className="row" style={{ marginTop: 10 }}>
                <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
                <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
              </div>
            )}
            {whenType === 3 && (
              <select style={{ marginTop: 10, width: '100%' }} value={scheduleId}
                      onChange={(e) => setScheduleId(Number(e.target.value))}>
                <option value="">— выберите —</option>
                {schedules.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            )}
            <label className="field" style={{ marginTop: 12 }}>
              Проект
              <select value={projectId} onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : '')}>
                <option value="">— без проекта —</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Настройки</h3>
            <div className="settings-checks">
              <label className="check"><input type="checkbox" checked={bool('pin_post')} onChange={(e) => set('pin_post', e.target.checked)} /> Закрепить пост</label>
              <label className="check"><input type="checkbox" checked={bool('disable_comments')} onChange={(e) => set('disable_comments', e.target.checked)} /> Отключить комментарии</label>
              <label className="check"><input type="checkbox" checked={bool('publish_as_story')} onChange={(e) => set('publish_as_story', e.target.checked)} /> Публиковать как историю</label>
              <label className="check"><input type="checkbox" checked={bool('publish_as_clips')} onChange={(e) => set('publish_as_clips', e.target.checked)} /> ВК: как клипы</label>
              <label className="check"><input type="checkbox" checked={bool('share_clips_to_feed')} onChange={(e) => set('share_clips_to_feed', e.target.checked)} /> ВК: дублировать клипы в ленту</label>
              <label className="check"><input type="checkbox" checked={bool('publish_as_shorts')} onChange={(e) => set('publish_as_shorts', e.target.checked)} /> YouTube: как Shorts</label>
              <label className="check"><input type="checkbox" checked={bool('publish_as_reel')} onChange={(e) => set('publish_as_reel', e.target.checked)} /> Instagram: как Reels</label>
              <label className="check"><input type="checkbox" checked={bool('publish_as_carousel')} onChange={(e) => set('publish_as_carousel', e.target.checked)} /> Как карусель (несколько фото)</label>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <label className="field" style={{ flex: 1 }}>
                Автоудаление через (дней)
                <input type="number" min={0} value={Number(settings.delete_post_day ?? 0)}
                       onChange={(e) => set('delete_post_day', Number(e.target.value))} />
              </label>
              <label className="field" style={{ width: 90 }}>
                часов
                <input type="number" min={0} max={23} value={Number(settings.delete_post_hour ?? 0)}
                       onChange={(e) => set('delete_post_hour', Number(e.target.value))} />
              </label>
            </div>
            <label className="field" style={{ marginTop: 12 }}>
              Повтор
              <select value={Number(settings.repeat_type ?? 0)} onChange={(e) => set('repeat_type', Number(e.target.value))}>
                <option value={0}>Без повтора</option>
                <option value={1}>Ежедневно</option>
                <option value={2}>Еженедельно</option>
                <option value={3}>Ежемесячно</option>
              </select>
            </label>
          </div>

          {editing && (
            <div className="panel">
              <button style={{ width: '100%', justifyContent: 'center' }} disabled={busy}
                      onClick={publishNow}>Опубликовать сейчас</button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
