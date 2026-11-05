import React, { useCallback, useEffect, useRef, useState } from 'react'
import { mdLinePrefix, mdWrap } from '../lib/mdapply'

interface MdCmd {
  id: string
  label: string
  cls: string
  title: string
  wrap?: readonly [string, string]
  linePrefix?: string
  placeholder?: string
}

// Кнопки совпадают с синтаксисом, который понимает бэкенд
// (backend/app/integrations/_mdfmt.py): Telegram рендерит разметку,
// VK/OK аккуратно снимают её.
const CMDS: MdCmd[] = [
  { id: 'bold', label: 'Ж', cls: 'md-b', title: 'Полужирный — **текст**', wrap: ['**', '**'] },
  { id: 'italic', label: 'К', cls: 'md-i', title: 'Курсив — *текст*', wrap: ['*', '*'] },
  { id: 'under', label: 'Ч', cls: 'md-u', title: 'Подчёркнутый — __текст__', wrap: ['__', '__'] },
  { id: 'strike', label: 'S', cls: 'md-s', title: 'Зачёркнутый — ~~текст~~', wrap: ['~~', '~~'] },
  { id: 'code', label: '‹/›', cls: 'md-code', title: 'Код — `текст`', wrap: ['`', '`'] },
  { id: 'link', label: '🔗', cls: '', title: 'Ссылка — [текст](https://…)', wrap: ['[', '](https://)'] },
  { id: 'head', label: 'H', cls: '', title: 'Заголовок — # текст', linePrefix: '# ' },
]

interface Props {
  value: string
  onChange: (v: string) => void
  rows?: number
  placeholder?: string
  style?: React.CSSProperties
}

/** Textarea со всплывающим меню Markdown: выделили текст — появилось меню. */
export default function MdTextarea({ value, onChange, rows, placeholder, style }: Props) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const pendingSel = useRef<[number, number] | null>(null)
  const mouse = useRef<{ x: number; y: number } | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)

  const hide = useCallback(() => setMenu(null), [])

  // После применения кнопки: восстанавливаем выделение в уже обновлённом
  // тексте (рендер с новым value происходит до этого эффекта).
  useEffect(() => {
    if (!pendingSel.current) return
    const ta = taRef.current
    if (!ta) return
    const [s, e] = pendingSel.current
    pendingSel.current = null
    ta.focus()
    ta.setSelectionRange(s, e)
  })

  const showForSelection = useCallback(() => {
    const ta = taRef.current
    if (!ta || ta.selectionStart === ta.selectionEnd) {
      hide()
      return
    }
    const m = mouse.current
    if (m) {
      // у курсора мыши, но не вылезая за края экрана
      setMenu({
        x: Math.min(Math.max(m.x, 160), window.innerWidth - 160),
        y: Math.max(m.y, 64),
      })
      return
    }
    // выделение с клавиатуры — центрируем над полем
    const r = ta.getBoundingClientRect()
    setMenu({
      x: Math.min(r.left + r.width / 2, Math.max(window.innerWidth - 160, 160)),
      y: Math.max(r.top, 64),
    })
  }, [hide])

  // Клик мимо меню и поля либо Escape — спрятать меню.
  useEffect(() => {
    if (!menu) return
    const onDown = (ev: MouseEvent) => {
      const t = ev.target as Node
      if (menuRef.current?.contains(t) || boxRef.current?.contains(t)) return
      hide()
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') hide()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [menu, hide])

  const apply = (cmd: MdCmd) => {
    const ta = taRef.current
    if (!ta) return
    const r = cmd.linePrefix
      ? mdLinePrefix(value, ta.selectionStart, ta.selectionEnd, cmd.linePrefix)
      : mdWrap(value, ta.selectionStart, ta.selectionEnd, cmd.wrap![0], cmd.wrap![1])
    pendingSel.current = [r.start, r.end]
    onChange(r.value)
    hide()
  }

  return (
    <div ref={boxRef} style={{ position: 'relative', ...style }}>
      <textarea
        ref={taRef}
        rows={rows}
        value={value}
        placeholder={placeholder}
        style={{ width: '100%' }}
        onChange={(e) => onChange(e.target.value)}
        onMouseDown={() => { mouse.current = null }}
        onMouseUp={(e) => {
          mouse.current = { x: e.clientX, y: e.clientY }
          showForSelection()
        }}
        onKeyUp={(e) => {
          const k = e.key
          const nav = k.startsWith('Arrow') || k === 'Shift' || k === 'Home' || k === 'End'
            || ((k === 'a' || k === 'A') && (e.ctrlKey || e.metaKey))
          if (nav) showForSelection()
          else hide()
        }}
        onScroll={hide}
        onBlur={hide}
      />
      {menu && (
        <div ref={menuRef} className="md-menu" style={{ left: menu.x, top: menu.y }}
             onMouseDown={(e) => e.preventDefault()}>
          {CMDS.map((c) => (
            <button key={c.id} type="button" className={c.cls} title={c.title}
                    onClick={() => apply(c)}>{c.label}</button>
          ))}
          <span className="md-menu-label">Markdown</span>
        </div>
      )}
    </div>
  )
}
