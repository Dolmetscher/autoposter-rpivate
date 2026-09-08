import React, { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../lib/api'

export default function Login() {
  const nav = useNavigate()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (mode === 'register') {
        await api.post('/api/web/auth/register', { email, password })
      }
      const data = await api.post<{ access_token: string }>(
        '/api/web/auth/login', { email, password })
      setToken(data.access_token)
      nav('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ошибка соединения')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>Autoposter</h1>
        <p className="subtitle">Автопостинг во все соцсети</p>
        <input type="email" placeholder="Email" value={email}
               onChange={(e) => setEmail(e.target.value)} required />
        <input type="password" placeholder="Пароль (мин. 6 символов)" value={password}
               onChange={(e) => setPassword(e.target.value)} required minLength={6} />
        {error && <div className="error-text">{error}</div>}
        <button disabled={busy}>
          {mode === 'login' ? 'Войти' : 'Зарегистрироваться'}
        </button>
        <p style={{ textAlign: 'center', marginTop: 14 }} className="hint">
          {mode === 'login' ? 'Нет аккаунта? ' : 'Уже есть аккаунт? '}
          <a href="#" onClick={(e) => {
            e.preventDefault()
            setMode(mode === 'login' ? 'register' : 'login')
            setError('')
          }}>
            {mode === 'login' ? 'Создать' : 'Войти'}
          </a>
        </p>
      </form>
    </div>
  )
}
