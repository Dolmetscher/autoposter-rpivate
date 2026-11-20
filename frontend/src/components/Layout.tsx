import React, { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { getToken, setToken } from '../lib/api'
import { getTheme, toggleTheme } from '../lib/theme'

const links: { to: string; label: string; icon: string }[] = [
  { to: '/', label: 'Дашборд', icon: '📊' },
  { to: '/posts', label: 'Посты', icon: '📝' },
  { to: '/calendar', label: 'Календарь', icon: '🗓' },
  { to: '/analytics', label: 'Аналитика', icon: '📈' },
  { to: '/accounts', label: 'Аккаунты', icon: '🔗' },
  { to: '/projects', label: 'Проекты', icon: '📁' },
  { to: '/schedules', label: 'Расписания', icon: '⏰' },
  { to: '/crosspost', label: 'Кросспостинг', icon: '♻️' },
  { to: '/team', label: 'Команда', icon: '👥' },
  { to: '/settings', label: 'Настройки', icon: '⚙️' },
]

export default function Layout({ secretsBroken, onDismissBanner }: {
  secretsBroken?: boolean
  onDismissBanner?: () => void
}) {
  const nav = useNavigate()
  const [theme, setThemeState] = useState<'light' | 'dark'>(() => getTheme())

  // tema могла быть проставлена инлайн-скриптом в index.html — синхронизируемся
  useEffect(() => { document.documentElement.dataset.theme = theme }, []) // eslint-disable-line

  const switchTheme = () => setThemeState(toggleTheme())

  const logout = () => {
    setToken(null)
    nav('/login')
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">Autoposter</div>
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.to === '/'}
                   className={({ isActive }) => (isActive ? 'active' : '')}>
            <span>{l.icon}</span> {l.label}
          </NavLink>
        ))}
        <div className="spacer" />
        <a onClick={switchTheme} title="Переключить тему оформления">
          <span>{theme === 'dark' ? '☀️' : '🌙'}</span> {theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
        </a>
        {getToken() && (
          <a onClick={logout}>
            <span>🚪</span> Выйти
          </a>
        )}
      </aside>
      <main className="main">
        {secretsBroken && (
          <div className="warn-banner" role="alert">
            <div>
              <b>⚠️ Сохранённые ключи не расшифровались.</b> Обычно это значит, что при
              обновлении стека сменился SECRET_KEY (или CREDENTIALS_SECRET) — приложение
              больше не видит токены аккаунтов и LLM-ключ, и публикации падают. Верните
              прежнее значение SECRET_KEY в переменных стека Portainer (или заново введите
              токены в разделе <Link to="/accounts">Аккаунты</Link> и ключ в{' '}
              <Link to="/settings">Настройках</Link>) — после этого всё заработает без
              переподключения.
            </div>
            {onDismissBanner && (
              <button className="close" title="Скрыть предупреждение"
                      aria-label="Скрыть предупреждение" onClick={onDismissBanner}>✕</button>
            )}
          </div>
        )}
        <Outlet />
      </main>
    </div>
  )
}
