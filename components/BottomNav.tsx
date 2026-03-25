'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useCart } from '@/lib/cart'

const NAV = [
  { href: '/marketplace', icon: 'home',         label: 'Tienda'   },
  { href: '/scan',        icon: 'photo_camera',  label: 'Escanear' },
  { href: '/carrito',     icon: 'shopping_cart', label: 'Carrito'  },
  { href: '/perfil',      icon: 'person',        label: 'Perfil'   },
]

export default function BottomNav() {
  const path = usePathname()
  const { cantidad } = useCart()

  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-slate-100 px-6 py-3 flex items-center justify-between z-50">
      {NAV.map(n => {
        const active = path.startsWith(n.href)
        return (
          <Link key={n.href} href={n.href} className={`flex flex-col items-center gap-1 relative ${active ? 'text-[#325926]' : 'text-slate-400'}`}>
            <span className={`material-symbols-outlined ${active ? 'fill-icon' : ''}`}>{n.icon}</span>
            <span className="text-[10px] font-bold uppercase tracking-tighter">{n.label}</span>
            {n.href === '/carrito' && cantidad > 0 && (
              <div className="absolute -top-1 -right-1 w-4 h-4 bg-[#325926] text-white text-[8px] flex items-center justify-center rounded-full font-bold">
                {cantidad}
              </div>
            )}
          </Link>
        )
      })}
    </nav>
  )
}
