'use client'
import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import { useCart } from '@/lib/cart'
import Toast from '@/components/Toast'
import { Planta } from '@/types'

export default function ProductoPage() {
  const { id } = useParams<{ id: string }>()
  const router  = useRouter()
  const { agregar } = useCart()
  const [planta, setPlanta] = useState<Planta | null>(null)
  const [loading, setLoading] = useState(true)
  const [fav, setFav]         = useState(false)
  const [toast, setToast]     = useState('')

  useEffect(() => {
    supabase.from('catalogo_plantas').select('*').eq('id', id).single()
      .then(({ data }) => { setPlanta(data); setLoading(false) })
  }, [id])

  if (loading) return <div className="min-h-screen bg-[#f7f7f6] flex items-center justify-center"><div className="spinner"/></div>
  if (!planta)  return (
    <div className="min-h-screen bg-[#f7f7f6] flex flex-col items-center justify-center gap-4">
      <span className="material-symbols-outlined text-5xl text-slate-300">search_off</span>
      <Link href="/marketplace" className="text-[#325926] font-semibold underline">Volver al marketplace</Link>
    </div>
  )

  const meta = planta.vision_metadata || {}

  return (
    <div className="min-h-screen bg-[#f7f7f6] pb-32">
      {toast && <Toast msg={toast} onDone={() => setToast('')} />}

      <header className="sticky top-0 z-20 bg-[#f7f7f6]/80 backdrop-blur-md px-4 py-4 flex items-center justify-between">
        <button onClick={() => router.back()} className="w-10 h-10 flex items-center justify-center rounded-full bg-white shadow-sm">
          <span className="material-symbols-outlined">arrow_back</span>
        </button>
        <h1 className="text-xl font-bold">Detalle Producto</h1>
        <button onClick={() => setFav(!fav)} className="w-10 h-10 flex items-center justify-center rounded-full bg-white shadow-sm">
          <span className="material-symbols-outlined"
            style={{ fontVariationSettings: fav ? "'FILL' 1" : "'FILL' 0", color: fav ? '#ef4444' : '#374151' }}>
            favorite
          </span>
        </button>
      </header>

      {/* Imagen */}
      <div className="px-4 mt-2">
        <div className="aspect-square w-full rounded-2xl overflow-hidden bg-[#325926]/10">
          {planta.imagen_url
            ? <img src={planta.imagen_url} alt={planta.nombre_comun} className="w-full h-full object-cover"/>
            : <div className="w-full h-full flex items-center justify-center">
                <span className="material-symbols-outlined text-[#325926]/20 text-9xl">potted_plant</span>
              </div>
          }
        </div>
      </div>

      <div className="px-4 mt-6 space-y-6">
        {/* Nombre y precio */}
        <div className="flex justify-between items-start">
          <div>
            <h2 className="text-3xl font-bold text-slate-900">{planta.nombre_comun}</h2>
            <p className="text-[#325926] italic mt-1">{planta.nombre_cientifico}</p>
          </div>
          <div className="text-right">
            <p className="text-3xl font-bold text-[#325926]">${planta.precio_cop?.toLocaleString('es-CO')}</p>
            <div className={`flex items-center justify-end gap-1 mt-1 text-sm font-semibold ${planta.stock_unidades > 0 ? 'text-emerald-600' : 'text-red-500'}`}>
              <span className="material-symbols-outlined text-sm">{planta.stock_unidades > 0 ? 'check_circle' : 'cancel'}</span>
              {planta.stock_unidades > 0 ? `En stock (${planta.stock_unidades})` : 'Sin stock'}
            </div>
          </div>
        </div>

        {/* Recomendación IA */}
        <div className="p-5 bg-[#325926]/5 rounded-2xl border border-[#325926]/10">
          <div className="flex items-center gap-2 mb-3">
            <span className="material-symbols-outlined text-[#325926]">auto_awesome</span>
            <h3 className="font-bold text-lg">Recomendación IA</h3>
          </div>
          <p className="text-slate-700 leading-relaxed">
            {meta.recomendacion || `Esta planta es ideal para espacios con luz indirecta en el clima de la Sabana de Bogotá. Ayuda a purificar el aire y requiere mantenimiento mínimo.`}
          </p>
        </div>

        {/* Descripción */}
        <div>
          <h3 className="text-xl font-bold mb-3">Descripción</h3>
          <p className="text-slate-600 leading-relaxed">
            {planta.descripcion || 'Hermosa planta ornamental cultivada con cuidado en los viveros de Cundinamarca.'}
          </p>
        </div>

        {/* Cuidados */}
        <div className="grid grid-cols-2 gap-3">
          {[
            { icon: 'wb_sunny',    label: 'Luz',         val: meta.luz       || 'Indirecta' },
            { icon: 'water_drop',  label: 'Riego',       val: meta.riego     || 'Semanal'   },
            { icon: 'thermostat',  label: 'Temperatura', val: '15–24 °C'                    },
            { icon: 'straighten',  label: 'Altura',      val: meta.altura    || '30–60 cm'  },
          ].map(c => (
            <div key={c.label} className="flex items-center gap-3 p-3 bg-white rounded-xl shadow-sm">
              <span className="material-symbols-outlined text-[#325926]">{c.icon}</span>
              <div>
                <p className="text-[10px] text-slate-400 uppercase font-bold">{c.label}</p>
                <p className="text-sm font-medium text-slate-900">{c.val}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* CTA fijo */}
      <div className="fixed bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-slate-200 p-4 z-50">
        <div className="max-w-lg mx-auto flex gap-3">
          <Link href="/carrito" className="flex items-center justify-center w-12 h-12 rounded-xl border border-[#325926]/20 text-[#325926]">
            <span className="material-symbols-outlined">shopping_cart</span>
          </Link>
          <button
            disabled={planta.stock_unidades === 0}
            onClick={() => {
              agregar({ id: planta.id, nombre_comun: planta.nombre_comun, precio_cop: planta.precio_cop, imagen_url: planta.imagen_url })
              setToast(`🌿 ${planta.nombre_comun} añadido al carrito`)
            }}
            className="flex-1 bg-[#325926] text-white py-3 rounded-xl font-bold text-base flex items-center justify-center gap-2 hover:bg-[#2d4f22] transition-colors disabled:opacity-40">
            <span className="material-symbols-outlined">add_shopping_cart</span>
            Agregar al carrito
          </button>
        </div>
      </div>
    </div>
  )
}
