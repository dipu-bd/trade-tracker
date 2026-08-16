import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { api, setAccessToken } from '@/api/client'
import type { AuthTokens } from '@/api/types'

interface User {
  id: number
  email: string
  display_name: string
}

interface AuthState {
  user: User | null
  ready: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  const load = useCallback(async () => {
    try {
      setUser(await api<User>('/auth/me'))
    } catch {
      setUser(null)
    }
  }, [])

  useEffect(() => {
    // The access token lives in memory only; the refresh cookie is what survives a reload.
    void (async () => {
      try {
        const tokens = await api<AuthTokens>('/auth/refresh', { method: 'POST' })
        setAccessToken(tokens.access_token)
        await load()
      } catch {
        setAccessToken(null)
      } finally {
        setReady(true)
      }
    })()
  }, [load])

  const value = useMemo<AuthState>(
    () => ({
      user,
      ready,
      login: async (email, password) => {
        const tokens = await api<AuthTokens>('/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        })
        setAccessToken(tokens.access_token)
        await load()
      },
      register: async (email, password, displayName) => {
        await api('/auth/register', {
          method: 'POST',
          body: JSON.stringify({ email, password, display_name: displayName }),
        })
        const tokens = await api<AuthTokens>('/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        })
        setAccessToken(tokens.access_token)
        await load()
      },
      logout: async () => {
        try {
          await api('/auth/logout', { method: 'POST' })
        } finally {
          setAccessToken(null)
          setUser(null)
        }
      },
    }),
    [user, ready, load],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth used outside AuthProvider')
  return context
}
