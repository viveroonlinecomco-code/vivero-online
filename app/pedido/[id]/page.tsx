'use client'
import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { Pedido } from '@/types'

const PASOS = [
  { estado: 'pendiente',   icon: 'check_circle',   label: 'Pedido Confirmado' },
  { estado: 'preparando',  icon: 'inventory_2',    label: 'Preparando'        },
  { estado: 'en_camino',   icon: 'local_shipping', label: 'En Camino'         },
  { estado: 'entregado',   icon: 'package_2',      label: 'Entregado'         },
]

const ORDEN = ['pendiente','confirmado','preparando','en_camino','entregado']

export default function SeguimientoPedido() {
  const { id }  = useParams<{ id: string }>()
  const router  = useRouter()
  const [pedido, setPedido] = useState<Pedido | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    supabase.from('pedidos').select('*').eq('id', id).single()
      .then(({ data }) => { setPedido(data); setLoading(false) })

    // Realtime — se actualiza cuando el viverista cambia el estado
    const channel = supabase
      .channel(`pedido-${id}`)
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'pedidos', filter: `id=eq.${id}` },
        payload => setPedido(payload.new as Pedido))
      .subscribe()

    return () => { supabase.removeChannel(channel) }
  }, [id])

  const pasoActual = ORDEN.indexOf(pedido?.estado || 'pendiente')
  const pct = Math.round(((pasoActual + 1) / ORDEN.length) * 100)

  if (loading) return <div className="min-h-screen bg-[#f7f7f6] flex items-center justify-center"><div className="spinner"/></div>

  return (
    <div className="min-h-screen bg-[#f7f7f6]">
      <header className="sticky top-0 z-50 bg-white px-4 py-4 flex items-center justify-between border-b border-slate-100">
        <button onClick={() => router.back()} className="flex size-10 items-center justify-center rounded-full hover:bg-[#325926]/10">
          <span className="material-symbols-outlined text-[#325926]">arrow_back</span>
        </button>
        <h2 className="text-lg font-bold">Detalles del Envío</h2>
        <button className="flex size-10 items-center justify-center rounded-full bg-[#325926]/10 text-[#325926]">
          <span className="material-symbols-outlined">chat_bubble</span>
        </button>
      </header>

      {/* Mapa placeholder */}
      <div className="mx-4 my-4 aspect-[16/9] rounded-2xl overflow-hidden bg-slate-200 relative">
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="material-symbols-outlined text-slate-300 text-8xl">map</span>
        </div>
        <div className="absolute top-1/3 left-1/3 bg-[#325926] text-white p-2 rounded-full shadow-lg">
          <span className="material-symbols-outlined text-sm">potted_plant</span>
        </div>
        <div className="absolute bottom-1/3 right-1/3 bg-white text-[#325926] p-2 rounded-full shadow-lg border-2 border-[#325926]">
          <span className="material-symbols-outlined text-sm">local_shipping</span>
        </div>
      </div>

      {/* Barra progreso */}
      <div className="mx-4 mb-4 p-4 bg-white rounded-2xl shadow-sm">
        <div className="flex justify-between items-center mb-3">
          <p className="font-bold text-slate-900">
            {pedido?.estado === 'entregado' ? '¡Pedido entregado!' : 'Tu pedido está en camino'}
          </p>
          <p className="text-[#325926] font-bold">{pct}%</p>
        </div>
        <div className="h-3 bg-[#325926]/10 rounded-full overflow-hidden">
          <div className="h-full bg-[#325926] rounded-full transition-all duration-700" style={{ width: `${pct}%` }}/>
        </div>
        <div className="flex items-center gap-2 mt-3">
          <span className="material-symbols-outlined text-[#325926] text-sm">schedule</span>
          <p className="text-slate-500 text-sm font-medium">Llegada estimada: 45–60 min</p>
        </div>
      </div>

      {/* Timeline */}
      <div className="mx-4 mb-4">
        <h2 className="text-2xl font-bold text-slate-900 mb-4">Estado del pedido</h2>
        <div className="grid grid-cols-[40px_1fr] gap-x-3">
          {PASOS.map((paso, i) => {
            const hecho   = i <= pasoActual
            const actual  = i === pasoActual
            const ultimo  = i === PASOS.length - 1
            return (
              <>
                <div key={`dot-${i}`} className="flex flex-col items-center">
                  <div className={`flex size-8 items-center justify-center rounded-full ${
                    hecho ? 'bg-[#325926] text-white' : 'bg-slate-100 text-slate-400'
                  } ${actual ? 'ring-4 ring-[#325926]/20' : ''}`}>
                    <span className="material-symbols-outlined text-[18px]">{paso.icon}</span>
                  </div>
                  {!ultimo && <div className={`w-0.5 h-10 ${hecho ? 'bg-[#325926]' : 'bg-slate-200'}`}/>}
                </div>
                <div key={`txt-${i}`} className={`pb-6 ${!ultimo ? '' : ''}`}>
                  <p className={`font-semibold ${hecho ? 'text-slate-900' : 'text-slate-400'}`}>{paso.label}</p>
                  {actual && <p className="text-[#325926] text-sm font-medium mt-0.5">Estado actual</p>}
                </div>
              </>
            )
          })}
        </div>
      </div>

      {/* Info pedido */}
      <div className="mx-4 p-4 bg-white rounded-2xl shadow-sm mb-4">
        <div className="flex justify-between items-center mb-4">
          <h3 className="font-bold text-lg">Resumen de compra</h3>
        </div>
        <div className="pt-4 border-t border-slate-100 flex justify-between">
          <span className="text-slate-500">Total Pagado</span>
          <span className="text-2xl font-black text-[#325926]">${pedido?.total_cop?.toLocaleString('es-CO')}</span>
        </div>
      </div>

      <div className="mx-4 mb-8">
        <button className="w-full bg-slate-100 text-slate-700 font-bold py-4 rounded-xl flex items-center justify-center gap-2">
          <span className="material-symbols-outlined">help_outline</span>
          ¿Necesitas ayuda con tu pedido?
        </button>
      </div>
    </div>
  )
}
