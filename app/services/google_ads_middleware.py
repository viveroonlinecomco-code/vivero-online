"""Middleware que inyecta Google Ads tag + banner de cookies (Ley 1581).

Se ejecuta en todas las respuestas HTML del sitio. NO toca respuestas
JSON, API, imágenes ni archivos estáticos.

CONSENT MODE V2:
- Por defecto todos los consents empiezan en DENIED
- Google Ads carga pero NO manda datos personales
- Cuando el usuario acepta el banner, se llama gtag('consent', 'update', ...)
  y ahí sí empieza a trackear con cookies
- Esto cumple Ley 1581/2012 (habeas data colombiana) porque el consentimiento
  es INFORMADO y PREVIO

ID de conversiones: AW-433175134 (cuenta Google Ads de ViveroOnline)
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_GOOGLE_ADS_ID = "AW-433175134"

# HTML a inyectar antes de </body> en cada página
_INJECT_HTML = f"""
<!-- Google Ads + Consent Mode v2 (ViveroOnline · Ley 1581/2012) -->
<script async src="https://www.googletagmanager.com/gtag/js?id={_GOOGLE_ADS_ID}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}

  // Consent Mode v2 — todo DENIED hasta que usuario acepta banner
  gtag('consent', 'default', {{
    'ad_storage': 'denied',
    'ad_user_data': 'denied',
    'ad_personalization': 'denied',
    'analytics_storage': 'denied',
    'wait_for_update': 500
  }});

  gtag('js', new Date());
  gtag('config', '{_GOOGLE_ADS_ID}');

  // Si el usuario ya dio consentimiento previamente, restaurarlo
  (function() {{
    try {{
      const consent = localStorage.getItem('vo_cookie_consent');
      if (consent === 'accepted') {{
        gtag('consent', 'update', {{
          'ad_storage': 'granted',
          'ad_user_data': 'granted',
          'ad_personalization': 'granted',
          'analytics_storage': 'granted'
        }});
      }}
    }} catch (e) {{}}
  }})();
</script>

<!-- Banner de cookies (Ley 1581/2012) -->
<div id="vo-cookie-banner" style="display:none;position:fixed;bottom:0;left:0;right:0;background:#1f2937;color:white;padding:16px 20px;box-shadow:0 -2px 10px rgba(0,0,0,0.15);z-index:9999;font-family:system-ui,sans-serif;font-size:14px;line-height:1.5">
  <div style="max-width:1200px;margin:0 auto;display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between">
    <div style="flex:1;min-width:280px">
      🌱 Usamos cookies para mejorar tu experiencia y mostrarte anuncios relevantes.
      Al aceptar, permitís también las de Google Ads para medir conversiones.
      <a href="/habeas-data" style="color:#93c5fd;text-decoration:underline">Ver política de datos</a>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button onclick="voRejectCookies()" style="background:transparent;color:white;border:1px solid #6b7280;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px">
        Rechazar
      </button>
      <button onclick="voAcceptCookies()" style="background:#22c55e;color:white;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:600;font-size:13px">
        Aceptar
      </button>
    </div>
  </div>
</div>

<script>
  (function() {{
    // Mostrar banner solo si no hay decisión previa
    try {{
      if (!localStorage.getItem('vo_cookie_consent')) {{
        document.getElementById('vo-cookie-banner').style.display = 'block';
      }}
    }} catch (e) {{}}
  }})();

  function voAcceptCookies() {{
    try {{ localStorage.setItem('vo_cookie_consent', 'accepted'); }} catch(e) {{}}
    if (typeof gtag === 'function') {{
      gtag('consent', 'update', {{
        'ad_storage': 'granted',
        'ad_user_data': 'granted',
        'ad_personalization': 'granted',
        'analytics_storage': 'granted'
      }});
    }}
    document.getElementById('vo-cookie-banner').style.display = 'none';
  }}

  function voRejectCookies() {{
    try {{ localStorage.setItem('vo_cookie_consent', 'rejected'); }} catch(e) {{}}
    document.getElementById('vo-cookie-banner').style.display = 'none';
  }}
</script>
"""


class GoogleAdsMiddleware(BaseHTTPMiddleware):
    """Inyecta Google Ads tag + banner de cookies antes de </body>.

    Solo modifica respuestas HTML. Ignora JSON, imágenes, archivos estáticos.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Solo procesar respuestas HTML exitosas
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("text/html"):
            return response

        if response.status_code != 200:
            return response

        # Leer body original
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Inyectar antes del cierre de body
        html = body.decode("utf-8", errors="ignore")
        marker = "</body>"
        if marker in html:
            html = html.replace(marker, _INJECT_HTML + marker, 1)
        else:
            # Fallback: si no hay </body>, agregar al final
            html = html + _INJECT_HTML

        new_body = html.encode("utf-8")

        # Nueva respuesta con body modificado
        headers = dict(response.headers)
        headers["content-length"] = str(len(new_body))

        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=headers,
            media_type=content_type,
        )
