/**
 * ViveroOnline - Cliente JS común
 * - Maneja tokens localStorage
 * - Helper fetch() con Authorization Bearer
 * - Formato de precios COP
 * - Redirección si no autenticado
 */

const VO = {
    // ─────────────────── AUTH ───────────────────
    getToken() {
        return localStorage.getItem('vivero_access_token');
    },
    setTokens(access, refresh) {
        if (access) localStorage.setItem('vivero_access_token', access);
        if (refresh) localStorage.setItem('vivero_refresh_token', refresh);
    },
    clearTokens() {
        localStorage.removeItem('vivero_access_token');
        localStorage.removeItem('vivero_refresh_token');
    },
    requireAuth(redirectTo = '/auth/ingresar') {
        if (!this.getToken()) {
            window.location.href = redirectTo;
            return false;
        }
        return true;
    },

    // ─────────────────── FETCH ───────────────────
    async api(path, options = {}) {
        const token = this.getToken();
        const headers = {
            'Content-Type': 'application/json',
            ...(token && { 'Authorization': `Bearer ${token}` }),
            ...(options.headers || {}),
        };
        const res = await fetch(path, { ...options, headers });

        if (res.status === 401) {
            this.clearTokens();
            window.location.href = '/auth/ingresar';
            throw new Error('No autorizado');
        }

        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data.detail || `HTTP ${res.status}`);
        }
        return data;
    },

    async me() {
        return await this.api('/api/auth/me');
    },

    logout() {
        this.clearTokens();
        window.location.href = '/';
    },

    // ─────────────────── FORMATEO ───────────────────
    formatCOP(value) {
        const n = Number(value) || 0;
        if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
        if (n >= 1_000) return `$${Math.round(n / 1_000)}K`;
        return `$${n.toLocaleString('es-CO')}`;
    },

    formatCOPFull(value) {
        const n = Number(value) || 0;
        return `$${n.toLocaleString('es-CO')} COP`;
    },

    formatDate(iso) {
        if (!iso) return '';
        try {
            const d = new Date(iso);
            return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' });
        } catch {
            return iso.toString().substring(0, 10);
        }
    },

    // ─────────────────── UI HELPERS ───────────────────
    showToast(msg, type = 'info') {
        let toast = document.getElementById('vo-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'vo-toast';
            toast.className = 'fixed top-5 left-1/2 -translate-x-1/2 z-[9999] px-4 py-3 rounded-full shadow-lg text-sm font-semibold max-w-sm';
            document.body.appendChild(toast);
        }
        const colors = {
            info: 'bg-primary text-white',
            success: 'bg-green-600 text-white',
            error: 'bg-red-600 text-white',
        };
        toast.className = toast.className.replace(/bg-\S+|text-\S+/g, '').trim() + ' ' + colors[type];
        toast.textContent = msg;
        toast.style.opacity = '1';
        setTimeout(() => { toast.style.opacity = '0'; }, 3000);
    },
};

// Auto-logout si el token está roto
window.VO = VO;
