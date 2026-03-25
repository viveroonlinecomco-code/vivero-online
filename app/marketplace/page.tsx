'use client'
import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import { useCart } from '@/lib/cart'
import Toast from '@/components/Toast'
import BottomNav from '@/components/BottomNav'
import { Planta } from '@/types'

const CATS = ['Todo', 'Interior', 'Exterior', 'Árboles', 'Ornatos', 'Suculentas']

export default function Marketplace() {
  const [plantas, setPlantas]     = useState<Planta[]>([])
  const [loading, setLoading]     = useState(true)
  const [busqueda, setBusqueda]   = useState('')
  const [cat, setCat]             = useState('Todo')
  const [favs, setFavs]           = useState<Set<string>>(new Set())
  const [toast, setToast]         = useState('')
  const { agregar } = useCart()

  useEffect(() => {
    supabase
      .from('catalogo_plantas')
      .select('*')
      .order('created_at', { ascending: false })
      .then(({ data }) => { if (data) setPlantas(data); setLoading(false) })
  }, [])

  const toggleFav = useCallback((id: string) => {
    setFavs(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  }, [])

  const lista = plantas.filter(p =>
    p.nombre_comun?.toLowerCase().includes(busqueda.toLowerCase()) ||
    p.nombre_cientifico?.toLowerCase().includes(busqueda.toLowerCase())
  )

  return (
    <div className="min-h-screen bg-[#f7f7f6] pb-24">
      {toast && <Toast msg={toast} onDone={() => setToast('')} />}

      {/* Header */}
      <header className="sticky top-0 z-40 bg-[#f7f7f6]/80 backdrop-blur-md px-4 pt-4 pb-3">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="bg-[#325926]/10 p-2 rounded-full">
              <span className="material-symbols-outlined text-[#325926]">potted_plant</span>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-slate-500 font-bold">Ubicación</p>
              <div className="flex items-center gap-1">
                <span className="text-sm font-semibold">Cajicá, Cundinamarca</span>
                <span className="material-symbols-outlined text-sm text-slate-400">keyboard_arrow_down</span>
              </div>
            </div>
          </div>
          <Link href="/login" className="w-10 h-10 flex items-center justify-center rounded-full bg-white shadow-sm border border-slate-100">
            <span className="material-symbols-outlined text-slate-600">person</span>
          </Link>
        </div>

        <div className="flex gap-2">
          <div className="flex-1 relative">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">search</span>
            <input
              className="w-full h-12 pl-10 pr-4 bg-white border-none rounded-xl text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-[#325926]/20"
              placeholder="Buscar plantas..."
              value={busqueda}
              onChange={e => setBusqueda(e.target.value)}
            />
          </div>
          <button className="w-12 h-12 flex items-center justify-center bg-[#325926] text-white rounded-xl shadow-lg shadow-[#325926]/20">
            <span className="material-symbols-outlined">tune</span>
          </button>
        </div>
      </header>

      {/* Categorías */}
      <div className="flex gap-3 px-4 py-4 overflow-x-auto no-scrollbar">
        {CATS.map(c => (
          <button key={c} onClick={() => setCat(c)}
            className={`flex h-10 shrink-0 items-center gap-2 rounded-full px-5 text-sm font-semibold transition-all ${
              cat === c ? 'bg-[#325926] text-white shadow-md' : 'bg-white border border-slate-100 text-slate-700'
            }`}>
            {c}
          </button>
        ))}
      </div>

      {/* Lista */}
      <main className="px-4 space-y-4">
        {loading && (
          <div className="flex justify-center py-20"><div className="spinner" /></div>
        )}

        {!loading && lista.length === 0 && (
          <div className="flex flex-col items-center py-20 gap-4 text-slate-400">
            <span className="material-symbols-outlined text-6xl">search_off</span>
            <p className="font-medium">No se encontraron plantas</p>
            <button onClick={() => setBusqueda('')} className="text-[#325926] font-semibold text-sm underline">
              Limpiar búsqueda
            </button>
          </div>
        )}

        {lista.map(p => (
          <div key={p.id} className="bg-white rounded-2xl overflow-hidden shadow-sm border border-slate-100">
            {/* Imagen */}
            <div className="relative h-52 w-full bg-slate-100">
              {p.imagen_url
                ? <img src={p.imagen_url} alt={p.nombre_comun} className="w-full h-full object-cover" />
                : <div className="w-full h-full flex items-center justify-center">
                    <span className="material-symbols-outlined text-slate-200 text-8xl">potted_plant</span>
                  </div>
              }
              <button onClick={() => toggleFav(p.id)}
                className="absolute top-3 right-3 w-9 h-9 bg-white/90 backdrop-blur rounded-full flex items-center justify-center shadow-sm">
                <span className="material-symbols-outlined text-xl"
                  style={{ fontVariationSettings: favs.has(p.id) ? "'FILL' 1" : "'FILL' 0",
                           color: favs.has(p.id) ? '#ef4444' : '#374151' }}>
                  favorite
                </span>
              </button>
              {p.stock_unidades > 0 && p.stock_unidades < 5 && (
                <div className="absolute bottom-3 left-3 bg-amber-500/90 backdrop-blur px-2 py-1 rounded-lg">
                  <span className="text-[10px] font-bold text-white uppercase">Últimas {p.stock_unidades}</span>
                </div>
              )}
              {p.stock_unidades === 0 && (
                <div className="absolute inset-0 bg-black/30 flex items-center justify-center">
                  <span className="bg-white/90 text-slate-700 text-xs font-bold px-3 py-1 rounded-full">Sin stock</span>
                </div>
              )}
            </div>

            {/* Info */}
            <div className="p-4">
              <div className="flex justify-between items-start mb-1">
                <h3 className="text-lg font-bold text-slate-900">{p.nombre_comun}</h3>
                <span className="text-lg font-bold text-[#325926]">
                  ${p.precio_cop?.toLocaleString('es-CO')}
                </span>
              </div>
              <p className="text-sm text-slate-500 italic mb-4">{p.nombre_cientifico}</p>
              <div className="flex items-center justify-between pt-3 border-t border-slate-50">
                <Link href={`/producto/${p.id}`} className="text-sm text-[#325926] font-semibold flex items-center gap-1 hover:underline">
                  Ver detalle <span className="material-symbols-outlined text-sm">arrow_forward</span>
                </Link>
                <button
                  disabled={p.stock_unidades === 0}
                  onClick={() => {
                    agregar({ id: p.id, nombre_comun: p.nombre_comun, precio_cop: p.precio_cop, imagen_url: p.imagen_url })
                    setToast(`🌿 ${p.nombre_comun} añadido al carrito`)
                  }}
                  className="bg-[#325926] text-white text-sm font-bold px-5 py-2 rounded-xl hover:bg-[#2d4f22] transition-colors disabled:opacity-40">
                  Agregar
                </button>
              </div>
            </div>
          </div>
        ))}
      </main>

      <BottomNav />
    </div>
  )
}
