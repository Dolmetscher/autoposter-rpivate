// Тема оформления: light | dark. Выбор хранится в localStorage, по умолчанию —
// системная (prefers-color-scheme). Сама тема применяется атрибутом
// data-theme на <html> (см. styles.css), index.html ставит его до загрузки
// React, чтобы страница не мигала белым при входе в тёмной теме.

export type Theme = 'light' | 'dark'

const THEME_KEY = 'autoposter_theme'

export function getTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* localStorage недоступен (приватный режим) — берём системную */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function setTheme(theme: Theme) {
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch { /* ignore */ }
  document.documentElement.dataset.theme = theme
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === 'dark' ? 'light' : 'dark'
  setTheme(next)
  return next
}
