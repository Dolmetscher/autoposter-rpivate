import React, { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api, getToken } from './lib/api'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Posts from './pages/Posts'
import PostEditor from './pages/PostEditor'
import CalendarPage from './pages/Calendar'
import Analytics from './pages/Analytics'
import Accounts from './pages/Accounts'
import Projects from './pages/Projects'
import Schedules from './pages/Schedules'
import Crosspost from './pages/Crosspost'
import Team from './pages/Team'
import SettingsPage from './pages/Settings'
import { Account, SourceInfo } from './lib/types'

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}

function Shell() {
  const location = useLocation()
  // читаем токен на каждом рендере: логин/логаут меняют location, а значит и этот флаг
  const token = getToken()
  const [sources, setSources] = useState<SourceInfo[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const metaLoaded = useRef(false)

  const reloadMeta = () => {
    api.get<{ list: SourceInfo[] }>('/api/web/sources')
      .then((d) => setSources(d.list)).catch(() => {})
    api.get<{ list: Account[] }>('/api/web/accounts')
      .then((d) => setAccounts(d.list)).catch(() => {})
  }

  useEffect(() => {
    if (token && !metaLoaded.current) {
      metaLoaded.current = true
      reloadMeta()
    }
    if (!token) {
      metaLoaded.current = false
      setSources([])
      setAccounts([])
    }
  }, [token, location.pathname])

  if (!token) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/posts" element={<Posts sources={sources} />} />
        <Route path="/posts/new" element={<PostEditor sources={sources} accounts={accounts} />} />
        <Route path="/posts/:id" element={<PostEditor sources={sources} accounts={accounts} />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/analytics" element={<Analytics sources={sources} />} />
        <Route path="/accounts" element={<Accounts sources={sources} reload={reloadMeta} />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/schedules" element={<Schedules />} />
          <Route path="/crosspost" element={<Crosspost sources={sources} accounts={accounts} />} />
          <Route path="/team" element={<Team />} />
          <Route path="/settings" element={<SettingsPage accounts={accounts} />} />
      </Route>
    </Routes>
  )
}
