'use client'
import { useState, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { supabase } from '@/lib/supabase'

const ROLES = [
  { id: 'buyer',         label: 'Comprador',  icon: 'shopping_bag', desc: 'Compra plantas para tu hogar o empresa' },
  { id: 'nursery_owner', label: 'Viverista',  icon: 'storefront',   desc: 'Vende y gestiona tu inventario' },
  { id: 'landscaper',    label: 'Paisajista', icon: 'landscape',    desc: 'Proyectos de gran escala' },
]

function LoginForm() {
  const router = useRouter()
  const params = useSearchParams()
  const [modo, setModo] = useState<'login' | 'registro'>('login')
  const [rol, setRol] = useState(params.get('rol') || 'buyer')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [nombre, setNombre] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  async function handleGitHub() {
    setLoading(true)
    await supabase.auth.signInWithOAuth({
      provider: 'github',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
        queryParams: { rol },
      },
    })
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')

    if (modo === 'login') {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password })
      if (error) { setError('Email o contraseña incorrectos'); setLoading(false); return }
      const r = data.user?.user_metadata?.rol || 'buyer'
      router.push(r === 'nursery_owner' ? '/viverista' : r === 'landscaper' ? '/paisajista' : '/marketplace')
    } else {
      const { error } = await supabase.auth.signUp({
        email, password,
        options: { data: { nombre, rol } },
      })
      if (error) { setError(error.message); setLoading(false); return }
      setOk('¡Registro exitoso! Revisa tu email para confirmar tu cuenta.')
      setModo('login')
    }
    setLoading(false)
  }

  return (
    <div className="min-h-screen bg-[#f7f7f6] flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center gap-3 mb-8">
          <div className="w-14 h-14 bg-[#325926] rounded-2xl flex items-center justify-center shadow-lg shadow-[#325926]/30">
            <span className="material-symbols-outlined text-white text-3xl">energy_savings_leaf</span>
          </div>
          <h1 className="text-2xl font-black text-slate-900">ViveroOnline</h1>
          <p className="text-slate-500 text-sm">Cundinamarca, Colombia</p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
          {/* Toggle */}
          <div className="flex bg-slate-100 rounded-xl p-1 mb-6">
            {(['login', 'registro'] as const).map(m => (
              <button key={m} onClick={() => setModo(m)}
                className={`flex-1 py-2 rounded-lg text-sm font-semibold transition-all ${modo === m ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500'}`}>
                {m === 'login' ? 'Iniciar sesión' : 'Registrarse'}
              </button>
            ))}
          </div>

          {/* Selector rol (solo registro) */}
          {modo === 'registro' && (
            <div className="mb-5">
              <p className="text-sm font-semibold text-slate-700 mb-3">¿Cómo usarás ViveroOnline?</p>
              <div className="grid grid-cols-3 gap-2">
                {ROLES.map(r => (
                  <button key={r.id} onClick={() => setRol(r.id)}
                    className={`flex flex-col items-center gap-2 p-3 rounded-xl border-2 transition-all ${rol === r.id ? 'border-[#325926] bg-[#325926]/5' : 'border-slate-100'}`}>
                    <span className={`material-symbols-outlined ${rol === r.id ? 'text-[#325926]' : 'text-slate-400'}`}>{r.icon}</span>
                    <span className={`text-[11px] font-semibold ${rol === r.id ? 'text-[#325926]' : 'text-slate-500'}`}>{r.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* GitHub OAuth */}
          <button onClick={handleGitHub} disabled={loading}
            className="w-full h-12 flex items-center justify-center gap-3 border border-slate-200 rounded-xl font-semibold text-sm text-slate-700 hover:bg-slate-50 transition-colors mb-4">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
            </svg>
            Continuar con GitHub
          </button>

          <div className="flex items-center gap-3 my-4">
            <div className="flex-1 h-px bg-slate-100" />
            <span className="text-xs text-slate-400 font-medium">o con email</span>
            <div className="flex-1 h-px bg-slate-100" />
          </div>

          {/* Formulario email */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {modo === 'registro' && (
              <input type="text" value={nombre} onChange={e => setNombre(e.target.value)} required
                placeholder="Nombre completo"
                className="w-full h-12 px-4 bg-[#f7f7f6] rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-[#325926]/20" />
            )}
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} required
              placeholder="tu@email.com"
              className="w-full h-12 px-4 bg-[#f7f7f6] rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-[#325926]/20" />
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required
              placeholder="Contraseña (mín. 6 caracteres)" minLength={6}
              className="w-full h-12 px-4 bg-[#f7f7f6] rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-[#325926]/20" />

            {error && <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-sm text-red-600">{error}</div>}
            {ok    && <div className="bg-green-50 border border-green-200 rounded-xl p-3 text-sm text-green-700">{ok}</div>}

            <button type="submit" disabled={loading}
              className="w-full h-12 bg-[#325926] text-white rounded-xl font-bold text-base hover:bg-[#2d4f22] transition-colors disabled:opacity-60 flex items-center justify-center gap-2">
              {loading && <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
              {modo === 'login' ? 'Iniciar sesión' : 'Crear cuenta'}
            </button>
          </form>
        </div>

        <p className="text-center text-sm text-slate-500 mt-5">
          <Link href="/marketplace" className="text-[#325926] font-semibold underline">Explorar sin cuenta →</Link>
        </p>
      </div>
    </div>
  )
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>
}
