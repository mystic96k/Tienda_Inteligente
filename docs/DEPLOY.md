# Desplegar RetailIQ para abrirlo desde cualquier lugar

Elige según para qué lo necesitas:

| Opción | Cuándo | Tiempo | Costo |
|---|---|---|---|
| **A. Túnel (Cloudflare)** | Demo el día del hackathon desde tu Mac | 5 min | gratis |
| **B. VM con Docker + HTTPS** | Enlace estable para jueces (recomendada) | 30 min | ~US$6–24/mes |
| **C. Google Cloud Run + Cloud SQL** | Si piden "nube de verdad" | 1–2 h | de pago, escala a cero |

Antes de publicar cualquier cosa en internet, pon una clave de administrador en `.env`:

```
ADMIN_KEY=una-frase-larga-y-aleatoria
```

Sin esa clave cualquiera que abra el enlace podría pulsar "Recalcular" o aprobar acciones. Con la clave, ver el tablero sigue abierto, pero las acciones que cambian datos piden la clave (el navegador la pregunta una vez).
Si usas Gemini, la cuota de la API es tuya: el enlace público puede gastarla preguntando al copiloto.

## A. Túnel desde tu Mac (lo más rápido)

1. Levanta el proyecto: `docker compose up -d` (espera `ready: true` en http://localhost:8000/api/health).
2. Instala el túnel: `brew install cloudflared`.
3. Ábrelo: `cloudflared tunnel --url http://localhost:8080`.
4. Te imprime una URL `https://algo-aleatorio.trycloudflare.com`: esa la abre cualquiera.
5. Funciona mientras tu Mac esté encendida y la terminal abierta. La URL cambia en cada ejecución.

## B. Una VM (DigitalOcean, Hetzner, Lightsail…)

1. Crea una VM Ubuntu 24.04 con **4 GB de RAM** o más. Abre los puertos 80 y 443.
2. Entra por SSH e instala Docker: `curl -fsSL https://get.docker.com | sh`.
3. Clona el repo: `git clone https://github.com/mystic96k/Tienda_Inteligente && cd Tienda_Inteligente`.
4. Crea `.env` (`cp .env.example .env`) y define:
   ```
   DB_PASSWORD=otra-clave-larga
   ADMIN_KEY=una-frase-larga-y-aleatoria
   GEMINI_API_KEY=tu-clave
   GEMINI_MODEL=gemini-flash-lite-latest
   SITE_ADDRESS=retail.tudominio.com
   ```
   Si no tienes dominio, usa la IP de la VM con guiones y `.sslip.io`, por ejemplo IP `203.0.113.10` → `SITE_ADDRESS=203-0-113-10.sslip.io`. Caddy obtiene el certificado solo.
   Si tienes dominio, crea un registro A que apunte a la IP de la VM.
5. Arranca: `docker compose -f docker-compose.prod.yml up -d --build`.
6. La primera vez descarga los CSV, carga la base y entrena (varios minutos). Revisa con `docker compose -f docker-compose.prod.yml logs -f api` hasta ver que termina.
7. Abre `https://TU_SITE_ADDRESS`.

Actualizar después de cambios: `git pull && docker compose -f docker-compose.prod.yml up -d --build`.
Los datos y modelos viven en volúmenes de Docker y sobreviven.

## C. Google Cloud Run + Cloud SQL

Ver `cloud/README.md` y `cloud/deploy_gcp.sh`. Ese script **no se probó** en esta entrega: ejecútalo con tiempo antes del hackathon, no minutos antes.

## Lista antes de publicar

- [ ] `.env` **no** está en git (`git status` no debe mostrarlo). Si alguna vez subiste la clave de Gemini, genera una nueva.
- [ ] `ADMIN_KEY` definida.
- [ ] La base de datos no expone el puerto 5432 a internet (en `docker-compose.prod.yml` no se publica).
- [ ] Probado desde el celular con datos móviles, no con tu Wi-Fi.
- [ ] Tienes una captura o video del tablero por si falla el internet del lugar.
