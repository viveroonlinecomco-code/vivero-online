# ViveroOnline.com.co — Frontend MVP

Marketplace de plantas ornamentales para Cundinamarca con IA.

## Stack
- **Frontend**: Next.js 14 + TypeScript + Tailwind CSS
- **Backend/DB**: Supabase (PostgreSQL + Auth + Storage + Realtime)
- **Deploy**: Vercel (automático desde GitHub)

## Pantallas incluidas
| Ruta | Pantalla |
|---|---|
| `/` | Landing page |
| `/marketplace` | Catálogo de plantas (datos reales de Supabase) |
| `/producto/[id]` | Ficha de producto + Recomendación IA |
| `/scan` | Identificador IA con cámara |
| `/carrito` | Carrito + checkout |
| `/pedido/[id]` | Seguimiento de pedido en tiempo real |
| `/viverista` | Panel del viverista |
| `/login` | Auth con GitHub OAuth + email |

## Setup local

### 1. Clona e instala
```bash
git clone https://github.com/viveroonlinecomco-code/vivero-online.git
cd vivero-online
npm install
```

### 2. Variables de entorno
Copia `.env.local.example` como `.env.local` y rellena:
```bash
cp .env.local.example .env.local
```

Obtén los valores en: **Supabase → Settings → API**

```env
NEXT_PUBLIC_SUPABASE_URL=https://rjqnlmnjyfudklihmkym.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=tu_anon_key_aqui
NEXT_PUBLIC_APP_URL=http://localhost:3000
```

### 3. Corre en local
```bash
npm run dev
```
Abre http://localhost:3000

## Deploy en Vercel

1. En Vercel → tu proyecto → **Settings → Environment Variables**
2. Agrega las mismas 3 variables de `.env.local`
3. Cada `git push` a `main` despliega automáticamente

## Auth con GitHub

En **github.com/settings/applications** tu OAuth App debe tener:
- **Homepage URL**: `https://tu-proyecto.vercel.app`
- **Authorization callback URL**: `https://rjqnlmnjyfudklihmkym.supabase.co/auth/v1/callback`

## Base de datos

Tablas en Supabase (ya creadas):
`viveristas`, `viveros`, `catalogo_plantas`, `plantas`, `inventario`,
`clientes`, `transacciones_b2b`, `eventos_agente`, `pedidos`,
`pedido_items`, `identificaciones_ia`, `perfiles`
