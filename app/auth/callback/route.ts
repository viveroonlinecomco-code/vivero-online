import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'
import { NextResponse } from 'next/server'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')

  if (code) {
    const cookieStore = cookies()
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          get(name: string) { return cookieStore.get(name)?.value },
          set(name: string, value: string, options: any) {
            try { cookieStore.set({ name, value, ...options }) } catch {}
          },
          remove(name: string, options: any) {
            try { cookieStore.set({ name, value: '', ...options }) } catch {}
          },
        },
      }
    )

    await supabase.auth.exchangeCodeForSession(code)

    const { data: { user } } = await supabase.auth.getUser()
    if (user) {
      const { data: perfil } = await supabase
        .from('perfiles')
        .select('rol')
        .eq('id', user.id)
        .single()

      const rol = perfil?.rol || user.user_metadata?.rol || 'buyer'
      if (rol === 'nursery_owner') return NextResponse.redirect(`${origin}/viverista`)
      if (rol === 'landscaper')    return NextResponse.redirect(`${origin}/paisajista`)
      return NextResponse.redirect(`${origin}/marketplace`)
    }
  }

  return NextResponse.redirect(`${origin}/login`)
}
