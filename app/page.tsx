import Link from 'next/link'

export default function Home() {
  return (
    <div className="min-h-screen bg-[#f7f7f6]">
      <header className="sticky top-0 z-50 bg-[#f7f7f6]/80 backdrop-blur-md border-b border-[#325926]/10">
        <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-[#325926] text-3xl">energy_savings_leaf</span>
            <h2 className="text-lg font-bold tracking-tight">ViveroOnline.com.co</h2>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/login" className="text-sm font-semibold text-slate-700 hover:text-[#325926] transition-colors">
              Iniciar Sesión
            </Link>
            <Link href="/marketplace" className="bg-[#325926] text-white text-sm font-bold px-4 py-2 rounded-full hover:bg-[#2d4f22] transition-colors">
              Explorar
            </Link>
          </div>
        </div>
      </header>

      <main>
        {/* Hero */}
        <section className="py-10 md:py-20 px-4 max-w-7xl mx-auto">
          <div className="flex flex-col gap-8 md:flex-row md:items-center">
            <div
              className="w-full md:w-1/2 aspect-video rounded-2xl overflow-hidden shadow-2xl shadow-[#325926]/20 bg-cover bg-center"
              style={{ backgroundImage: "url('https://images.unsplash.com/photo-1585320806297-9794b3e4eeae?w=800&q=80')" }}
            />
            <div className="flex flex-col gap-6 md:w-1/2">
              <span className="inline-block px-3 py-1 bg-[#325926]/10 text-[#325926] text-xs font-bold uppercase tracking-wider rounded-full w-fit">
                Innovación de la horticultura en Cundinamarca, Colombia
              </span>
              <h1 className="text-4xl md:text-6xl font-black leading-tight tracking-tight text-slate-900">
                Ecosistema Plantas Ornamentales IA
              </h1>
              <p className="text-slate-600 text-lg leading-relaxed">
                Transformando los viveros de Cundinamarca con inteligencia artificial y gestión inteligente de inventario.
              </p>
              <div className="flex flex-wrap gap-4">
                <Link href="/login?rol=nursery_owner"
                  className="flex-1 md:flex-none min-w-[160px] flex items-center justify-center rounded-full h-14 px-8 bg-[#325926] text-white text-base font-bold hover:scale-105 transition-transform shadow-lg shadow-[#325926]/30">
                  Soy Viverista
                </Link>
                <Link href="/marketplace"
                  className="flex-1 md:flex-none min-w-[160px] flex items-center justify-center rounded-full h-14 px-8 bg-[#325926]/10 text-[#325926] text-base font-bold hover:bg-[#325926]/20 transition-colors">
                  Comprar Plantas
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* Métricas */}
        <section className="max-w-7xl mx-auto px-4 py-8">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              { label: 'Viveros Activos', value: '500+', trend: '+12%' },
              { label: 'Plantas Entregadas', value: '10k+', trend: '+25%' },
              { label: 'Eficiencia Operativa', value: '40%', trend: '+30%' },
            ].map(m => (
              <div key={m.label} className="flex flex-col gap-2 rounded-xl p-8 bg-white border border-[#325926]/5 shadow-sm">
                <p className="text-slate-500 text-sm font-semibold uppercase tracking-wider">{m.label}</p>
                <div className="flex items-baseline gap-2">
                  <p className="text-4xl font-black text-slate-900">{m.value}</p>
                  <p className="text-green-600 text-sm font-bold flex items-center gap-0.5">
                    <span className="material-symbols-outlined text-sm">trending_up</span>{m.trend}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Features */}
        <section className="max-w-7xl mx-auto px-4 py-16">
          <h2 className="text-3xl md:text-5xl font-black leading-tight mb-4 text-slate-900">
            Tecnología que hace crecer tu negocio
          </h2>
          <p className="text-slate-600 text-lg mb-12 max-w-2xl">
            Potenciamos el sector del viverismo en Colombia con herramientas inteligentes para comercializar plantas ornamentales.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {[
              { icon: 'psychology',     title: 'Automatización con IA',  desc: 'Predicción precisa de demanda estacional y optimización de inventario.' },
              { icon: 'local_shipping', title: 'Logística Integrada',    desc: 'Rastreo en tiempo real y rutas optimizadas para cada planta.' },
              { icon: 'monitoring',     title: 'Análisis Predictivo',    desc: 'Métricas en tiempo real sobre crecimiento y tendencias de mercado.' },
            ].map(f => (
              <div key={f.title} className="flex flex-col gap-6 rounded-2xl border border-[#325926]/10 bg-white p-8 hover:shadow-xl transition-shadow">
                <div className="w-14 h-14 bg-[#325926]/10 rounded-xl flex items-center justify-center">
                  <span className="material-symbols-outlined text-[#325926] text-3xl">{f.icon}</span>
                </div>
                <div>
                  <h3 className="text-xl font-bold text-slate-900 mb-2">{f.title}</h3>
                  <p className="text-slate-600 leading-relaxed">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* CTA */}
        <section className="max-w-7xl mx-auto px-4 py-20">
          <div className="bg-[#325926] rounded-3xl p-8 md:p-16 flex flex-col items-center gap-6 text-white text-center relative overflow-hidden">
            <div className="absolute inset-0 opacity-10 bg-[radial-gradient(circle_at_center,white,transparent)]" />
            <h2 className="text-3xl md:text-5xl font-black max-w-2xl relative z-10">
              ¿Listo para llevar tu vivero al siguiente nivel?
            </h2>
            <div className="flex flex-col md:flex-row gap-4 relative z-10">
              <Link href="/login?rol=nursery_owner" className="bg-white text-[#325926] px-8 py-4 rounded-full font-bold text-lg hover:bg-slate-100 transition-colors">
                Empezar Ahora
              </Link>
              <Link href="/marketplace" className="bg-white/20 border border-white/30 text-white px-8 py-4 rounded-full font-bold text-lg hover:bg-white/10 transition-colors">
                Ver Marketplace
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="bg-white border-t border-[#325926]/10 py-12">
        <div className="max-w-7xl mx-auto px-4 grid grid-cols-1 md:grid-cols-3 gap-8">
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[#325926] text-3xl">energy_savings_leaf</span>
              <h2 className="text-xl font-bold">ViveroOnline.com.co</h2>
            </div>
            <p className="text-slate-500 text-sm">Revolucionando la cadena de suministro de plantas vivas en Cundinamarca.</p>
          </div>
          <div>
            <h4 className="font-bold mb-4">Plataforma</h4>
            <ul className="space-y-2 text-slate-500 text-sm">
              <li><Link href="/marketplace" className="hover:text-[#325926] transition-colors">Marketplace</Link></li>
              <li><Link href="/scan" className="hover:text-[#325926] transition-colors">Identificador IA</Link></li>
              <li><Link href="/login?rol=nursery_owner" className="hover:text-[#325926] transition-colors">Viveros Afiliados</Link></li>
            </ul>
          </div>
          <div>
            <h4 className="font-bold mb-4">Empresa</h4>
            <ul className="space-y-2 text-slate-500 text-sm">
              <li><a href="#" className="hover:text-[#325926] transition-colors">Sobre Nosotros</a></li>
              <li><a href="#" className="hover:text-[#325926] transition-colors">Contacto</a></li>
            </ul>
          </div>
        </div>
        <div className="max-w-7xl mx-auto px-4 mt-12 pt-8 border-t border-[#325926]/5 text-center text-slate-400 text-sm">
          © 2025 ViveroOnline.com.co — Todos los derechos reservados.
        </div>
      </footer>
    </div>
  )
}
