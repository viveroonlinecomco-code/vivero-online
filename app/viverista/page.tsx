'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import { Planta } from '@/types'

export default function ViveristaDashboard() {
  const [plantas, setPlantas] = useState<Planta[]>([])
  const [loading, setLoading] = useState(true)
  const [user, setUser]       = useState<any>(null)

  useEffect(() => {
    async function cargar() {
      const { data: { user } } = await supabase.auth.getUser()
      setUser(user)
      if (!user) return
      const { data } = await supabase
        .from('catalogo_plantas')
        .select('*')
        .eq('viverista_id', user.id)
        .order('created_at', { ascending: false })
      if (data) setPlantas(data)
      setLoading(false)
    }
    cargar()
  }, [])

  const totalStock  = plantas.reduce((s, p) => s + (p.stock_unidades || 0), 0)
  const totalValor  = plantas.reduce((s, p) => s + (p.precio_cop || 0) * (p.stock_unidades || 0), 0)

  const IASugerencias = [
    { nombre: 'Monstera Deliciosa', precio: 35000, sugerido: 40000, tipo: 'up'   },
    { nombre: 'Echeveria Elegans',  precio: 8500,  sugerido: 8500,  tipo: 'ok'   },
    { nombre: 'Sansevieria',        precio: 22000, sugerido: 20000, tipo: 'down' },
  ]

  return (
    <div className="min-h-screen bg-[#f7f7f6] pb-24">
      <header className="sticky top-0 z-50 bg-[#f7f7f6]/80 backdrop-blur-md border-b border-[#325926]/10">
        <div className="flex items-center p-4 justify-between max-w-lg mx-auto">
          <span className="material-symbols-outlined text-[#325926] text-3xl">psychology_alt</span>
          <h1 className="text-xl font-bold flex-1 ml-3">Mi Vivero</h1>
          <button className="flex items-center justify-center rounded-full h-10 w-10 bg-[#325926]/10 text-[#325926]">
            <span className="material-symbols-outlined">account_circle</span>
          </button>
        </div>
      </header>

      <main className="max-w-lg mx-auto px-4 pt-6 space-y-6">

        {/* Stats */}
        <div className="grid grid-cols-2 gap-4">
          {[
            { label: 'Inventario Total', val: loading ? '...' : totalStock.toLocaleString(), trend: '+5%' },
            { label: 'Valor Inventario', val: loading ? '...' : `$${Math.round(totalValor/1000)}k`, trend: '+12%' },
          ].map(s => (
            <div key={s.label} className="flex flex-col gap-2 rounded-xl p-5 border border-[#325926]/10 bg-white shadow-sm">
              <p className="text-slate-500 text-sm font-medium">{s.label}</p>
              <p className="text-2xl font-bold text-slate-900">{s.val}</p>
              <div className="flex items-center gap-1 text-emerald-600 text-sm font-semibold">
                <span className="material-symbols-outlined text-sm">trending_up</span>{s.trend}
              </div>
            </div>
          ))}
        </div>

        {/* Gráfico demanda (visual) */}
        <div className="space-y-3">
          <h2 className="text-lg font-bold text-slate-900">Predicción de Demanda</h2>
          <div className="overflow-hidden rounded-xl border border-[#325926]/10 bg-white shadow-sm">
            <div className="bg-[#325926]/5 h-40 flex items-end gap-1.5 px-4 pt-4 justify-around">
              {[40,60,100,75,50,80,65].map((h,i) => (
                <div key={i} className="flex-1 rounded-t-md transition-all"
                  style={{ height: `${h}%`, background: `rgba(50,89,38,${0.25 + i*0.08})` }}/>
              ))}
            </div>
            <div className="flex justify-between px-4 py-1 text-[10px] text-slate-400 font-bold">
              {['L','M','X','J','V','S','D'].map(d => <span key={d}>{d}</span>)}
            </div>
            <div className="p-4 border-t border-slate-50">
              <p className="font-bold text-slate-900">Alta demanda esta semana</p>
              <p className="text-slate-500 text-sm mt-0.5">Suculentas y plantas de sombra en auge por cambio de temporada.</p>
            </div>
          </div>
        </div>

        {/* Precios IA */}
        <div className="space-y-3">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-bold text-slate-900">Precios sugeridos por IA</h2>
            <button className="text-[#325926] text-sm font-semibold">Ver todo</button>
          </div>
          {IASugerencias.map(item => (
            <div key={item.nombre} className="flex items-center gap-4 p-3 rounded-xl bg-white border border-[#325926]/5 shadow-sm">
              <div className="size-14 rounded-xl bg-[#325926]/5 flex items-center justify-center shrink-0">
                <span className="material-symbols-outlined text-[#325926]">potted_plant</span>
              </div>
              <div className="flex-1">
                <p className="font-semibold text-slate-900 text-sm">{item.nombre}</p>
                <p className="text-xs text-slate-400">Actual: ${item.precio.toLocaleString('es-CO')}</p>
              </div>
              <div className="text-right">
                <p className="text-[#325926] font-bold">${item.sugerido.toLocaleString('es-CO')}</p>
                <div className={`flex items-center justify-end gap-1 text-[10px] font-medium ${
                  item.tipo === 'up' ? 'text-emerald-600' : item.tipo === 'down' ? 'text-red-500' : 'text-slate-400'}`}>
                  <span className="material-symbols-outlined text-xs">auto_awesome</span>
                  {item.tipo === 'up'   ? `+$${((item.sugerido-item.precio)/1000).toFixed(0)}k sugerido` :
                   item.tipo === 'down' ? `-$${((item.precio-item.sugerido)/1000).toFixed(0)}k sugerido` : 'Precio óptimo'}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Catálogo real */}
        {plantas.length > 0 && (
          <div className="space-y-3">
            <div className="flex justify-between items-center">
              <h2 className="text-lg font-bold text-slate-900">Mi Catálogo</h2>
              <span className="text-xs text-slate-400">{plantas.length} plantas</span>
            </div>
            {plantas.slice(0,5).map(p => (
              <div key={p.id} className="flex items-center gap-3 p-3 rounded-xl bg-white border border-[#325926]/5 shadow-sm">
                <div className="size-14 rounded-xl overflow-hidden bg-slate-100 shrink-0">
                  {p.imagen_url
                    ? <img src={p.imagen_url} alt={p.nombre_comun} className="w-full h-full object-cover"/>
                    : <div className="w-full h-full flex items-center justify-center">
                        <span className="material-symbols-outlined text-slate-300 text-sm">potted_plant</span>
                      </div>
                  }
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-slate-900 text-sm truncate">{p.nombre_comun}</p>
                  <p className="text-xs text-slate-400">Stock: {p.stock_unidades} unid.</p>
                </div>
                <p className="text-[#325926] font-bold text-sm shrink-0">${p.precio_cop?.toLocaleString('es-CO')}</p>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* FAB */}
      <Link href="/viverista/nueva-planta"
        className="fixed bottom-20 right-5 flex size-14 items-center justify-center rounded-full bg-[#325926] text-white shadow-xl shadow-[#325926]/40 hover:scale-105 transition-transform z-50">
        <span className="material-symbols-outlined text-3xl">add</span>
      </Link>

      {/* Nav */}
      <nav className="fixed bottom-0 left-0 right-0 bg-white border-t border-[#325926]/10 px-4 pb-6 pt-2 z-40">
        <div className="max-w-lg mx-auto flex justify-around">
          {[
            { href: '/viverista',           icon: 'grid_view',       label: 'Inicio'     },
            { href: '/viverista/inventario', icon: 'potted_plant',    label: 'Inventario' },
            { href: '/viverista/pedidos',    icon: 'receipt_long',    label: 'Pedidos'    },
            { href: '/scan',                 icon: 'photo_camera',    label: 'Escanear'   },
          ].map(n => (
            <Link key={n.href} href={n.href} className="flex flex-col items-center gap-1 text-slate-400 hover:text-[#325926] transition-colors">
              <span className="material-symbols-outlined">{n.icon}</span>
              <p className="text-[10px] font-bold">{n.label}</p>
            </Link>
          ))}
        </div>
      </nav>
    </div>
  )
}
