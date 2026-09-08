// В веб-сборке пути относительные (прокси vite/nginx). В APK бэкенд задаётся
// переменной VITE_API_BASE при сборке (напр. https://autoposter-api.onrender.com)
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

const TOKEN_KEY = 'autoposter_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown,
                          isForm = false): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  let payload: BodyInit | undefined
  if (body !== undefined) {
    if (isForm) {
      payload = body as FormData
    } else {
      headers['Content-Type'] = 'application/json'
      payload = JSON.stringify(body)
    }
  }
  const resp = await fetch(`${API_BASE}${path}`, { method, headers, body: payload })
  if (resp.status === 401 && !path.startsWith('/api/web/auth/')) {
    setToken(null)
    window.location.href = '/login'
    throw new ApiError(401, 'Требуется вход')
  }
  if (!resp.ok) {
    let msg = `Ошибка ${resp.status}`
    try {
      const data = await resp.json()
      msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data)
    } catch { /* ignore */ }
    throw new ApiError(resp.status, msg)
  }
  return resp.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>('POST', path, form, true)
  },
}
