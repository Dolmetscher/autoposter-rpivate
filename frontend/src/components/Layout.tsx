import React from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { getToken, setToken } from '../lib/api'

const links: { to: string; label: string; icon: string }[] = [
  { to: '/', label: 'Дашборд', icon: '📊' },
  { to: '/posts', label: 'Посты', icon: '📝' },
  { to: '/calendar', label: 'Календарь', icon: '🗓' },
  { to: '/analytics', label: 'Аналитика', icon: '📈' },
  { to: '/accounts', label: 'Аккаунты', icon: '🔗' },
  { to: '/projects', label: 'Проекты', icon: '📁' },
  { to: '/schedules', label: 'Расписания', icon: '⏰' },
  { to: '/crosspost', label: 'Кросспостинг', icon: '♻️' },
  { to: '/settings', label: 'Настройки', icon: '⚙️' },
]

export default function Layout() {
  const nav = useNavigate()
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
        {getToken() && (
          <a onClick={logout} style={{ cursor: 'pointer' }}>
            <span>🚪</span> Выйти
          </a>
        )}
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
