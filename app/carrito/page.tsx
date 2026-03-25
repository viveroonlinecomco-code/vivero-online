'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useCart } from '@/lib/cart'
import { supabase } from '@/lib/supabase'
import Toast from '@/components/Toast'
import BottomNav from '@/components/BottomNav'

export default function Carrito() {
  const { items, quitar, limpiar, total } = useCart()
  const router = useRouter()
  const [direccion, setDireccion] = useState('')
  const [loading, setLoading]     = useState(false)
  const [toast, setToast]         = useState('')

  async function confirmarPedido() {
    if (!direccion.trim()) { setToast('⚠️ Ingresa tu dirección de entrega'); return }
    if (items.length === 0) return
    setLoading(true)

    const { data: { user } } = await supabase.auth.getUser()
    if (!user) { router.push('/login'); return }

    const { data: pedido, error } = await supabase
      .from('pedidos')
      .insert({
        viverista_id: items[0].id, // simplificado para MVP
        estado: 'pendiente',
        total_cop: total,
        direccion_entrega: direccion,
      })
      .select()
      .single()

    if (error || !pedido) { setToast('Error al crear el pedido'); setLoading(false); return }

    await supabase.from('pedido_items').insert(
      items.map(i => ({
        pedido_id: pedido.id,
        catalogo_id: i.id,
        cantidad: i.cantidad,
        precio_unitario_cop: i.precio_cop,
      }))
    )

    limpiar()
    setToast('✅ ¡Pedido confirmado!')
    setTimeout(() => router.push(`/pedido/${pedido.id}`), 1500)
    setLoading(false)
  }

  return (
    <div className="min-h-screen bg-[#f7f7f6] pb-24">
      {toast && <Toast msg={toast} onDone={() => setToast('')} />}

      <header className="sticky top-0 z-40 bg-[#f7f7f6]/80 backdrop-blur-md px-4 py-4 flex items-center gap-3 border-b border-[#325926]/10">
        <button onClick={() => router.back()} className="w-10 h-10 flex items-center justify-center rounded-full bg-white shadow-sm">
          <span className="material-symbols-outlined">arrow_back</span>
        </button>
        <h1 className="text-xl font-bold flex-1">Mi Carrito</h1>
        {items.length > 0 && (
          <button onClick={limpiar} className="text-sm text-red-500 font-semibold">Vaciar</button>
        )}
      </header>

      <main className="px-4 py-4 space-y-3">
        {items.length === 0 && (
          <div className="flex flex-col items-center py-20 gap-4 text-slate-400">
            <span className="material-symbols-outlined text-6xl">shopping_cart</span>
            <p className="font-medium">Tu carrito está vacío</p>
            <button onClick={() => router.push('/marketplace')}
              className="bg-[#325926] text-white px-6 py-3 rounded-xl font-bold text-sm hover:bg-[#2d4f22] transition-colors">
              Ver Marketplace
            </button>
          </div>
        )}

        {items.map(item => (
          <div key={item.id} className="flex items-center gap-4 p-4 bg-white rounded-2xl shadow-sm border border-slate-100">
            <div className="w-16 h-16 rounded-xl overflow-hidden bg-slate-100 shrink-0">
              {item.imagen_url
                ? <img src={item.imagen_url} alt={item.nombre_comun} className="w-full h-full object-cover"/>
                : <div className="w-full h-full flex items-center justify-center">
                    <span className="material-symbols-outlined text-slate-300">potted_plant</span>
                  </div>
              }
            </div>
            <div className="flex-1">
              <p className="font-semibold text-slate-900">{item.nombre_comun}</p>
              <p className="text-sm text-slate-500">x{item.cantidad}</p>
            </div>
            <div className="text-right">
              <p className="font-bold text-[#325926]">${(item.precio_cop * item.cantidad).toLocaleString('es-CO')}</p>
              <button onClick={() => quitar(item.id)} className="text-xs text-red-400 mt-1 hover:text-red-600">
                Quitar
              </button>
            </div>
          </div>
        ))}

        {items.length > 0 && (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5 space-y-4">
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-2">Dirección de entrega</label>
              <input
                value={direccion}
                onChange={e => setDireccion(e.target.value)}
                placeholder="Ej: Cra 5 #12-34, Cajicá"
                className="w-full h-12 px-4 bg-[#f7f7f6] rounded-xl border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-[#325926]/20"
              />
            </div>
            <div className="flex justify-between items-center pt-3 border-t border-slate-100">
              <span className="text-slate-500 font-medium">Total</span>
              <span className="text-2xl font-black text-[#325926]">${total.toLocaleString('es-CO')}</span>
            </div>
            <button
              onClick={confirmarPedido}
              disabled={loading}
              className="w-full h-14 bg-[#325926] text-white rounded-xl font-bold text-base flex items-center justify-center gap-2 hover:bg-[#2d4f22] transition-colors disabled:opacity-60">
              {loading
                ? <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"/>
                : <span className="material-symbols-outlined">shopping_cart_checkout</span>
              }
              Confirmar Pedido
            </button>
          </div>
        )}
      </main>

      <BottomNav />
    </div>
  )
}
