"""
PS5 Price Tracker - Chile  (v2 — URLs verificadas manualmente)
=============================================================
Fuentes:
  1. SoloTodo API  → publicapi.solotodo.com  (agrega Paris, Falabella, Ripley, Lider, etc.)
  2. MercadoLibre  → api.mercadolibre.com   (API pública oficial)
  
Flujo:
  fetch → comparar con historial → alertar por Telegram → guardar historial
"""

import json, time, re, random, logging, requests, os
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

# ── Configuración ──────────────────────────────────────────────────────────────
CONFIG_FILE  = Path("config.json")
HISTORY_FILE = Path("price_history.json")

# Activa con: set DEBUG_TRACKER=1 (Windows) / export DEBUG_TRACKER=1 (Linux/Mac)
LOG_LEVEL = logging.DEBUG if os.environ.get("DEBUG_TRACKER") else logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("tracker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────
def load_json(path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_price(text: str):
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None

def fmt(p: int) -> str:
    return f"${p:,.0f}".replace(",", ".")

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": random.choice(UA_LIST),
                       "Accept-Language": "es-CL,es;q=0.9"})
    return s

# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 1 — SoloTodo API pública
# Agrega tiendas: Paris, Falabella, Ripley, Lider, AbcDin, Hites y más.
# Documentación no oficial pero estable desde 2018.
# IDs obtenidos de solotodo.cl/playstation-5 (verificados mayo 2026).
# ══════════════════════════════════════════════════════════════════════════════
SOLOTODO_PRODUCTS = [
    # (product_id, nombre_legible)
    ("225751", "PS5 Slim 1TB Digital"),
    ("226313", "PS5 Slim 1TB Blu-Ray"),
    ("364198", "PS5 Slim 825GB Digital"),
    ("364195", "PS5 Slim 825GB Digital + Bundle"),
    ("353268", "PS5 Slim 1TB Blu-Ray + Bundle"),
    ("257793", "PS5 Pro 2TB"),
]

# IDs numéricos de tiendas chilenas en SoloTodo
# 2=Lider, 3=Paris, 5=Falabella, 10=Ripley, 13=PC Factory, 27=AbcDin
SOLOTODO_STORES = "2,3,5,10,13,27"

def scrape_solotodo(session) -> list[dict]:
    """
    Usa la API REST de SoloTodo para obtener precios por producto.
    Endpoint: GET https://publicapi.solotodo.com/products/{id}/entities/
    Retorna los listados activos en tiendas chilenas con precio en CLP.
    """
    results = []

    for prod_id, prod_name in SOLOTODO_PRODUCTS:
        url = (
            f"https://publicapi.solotodo.com/products/{prod_id}/entities/"
            f"?stores={SOLOTODO_STORES}&ordering=active_registry__normal_price"
        )
        try:
            resp = session.get(url, timeout=15,
                               headers={"Accept": "application/json"})
            if not resp.ok:
                log.warning(f"SoloTodo {prod_id}: HTTP {resp.status_code} — {resp.text[:120]}")
                continue
            raw = resp.json()
            # La API puede devolver una lista directamente O {"results": [...]}
            if isinstance(raw, list):
                entities = raw
            elif isinstance(raw, dict):
                entities = raw.get("results", [])
            else:
                entities = []
            log.debug(f"  SoloTodo {prod_id}: {len(entities)} entidades, "
                      f"tipo respuesta={type(raw).__name__}")
            for e in entities:
                # Manejar tanto dict como estructura anidada
                if not isinstance(e, dict):
                    log.debug(f"  Entidad inesperada: {type(e)} — {str(e)[:80]}")
                    continue
                registry = e.get("active_registry") or {}
                price    = registry.get("offer_price") or registry.get("normal_price")
                store_data = e.get("store") or {}
                store    = store_data.get("name", "Tienda") if isinstance(store_data, dict) else str(store_data)
                ext_url  = e.get("external_url") or ""
                st_url   = f"https://www.solotodo.cl/products/{prod_id}"
                if price:
                    results.append({
                        "store": store,
                        "name":  prod_name,
                        "price": int(float(price)),   # API devuelve '559990.00'
                        "url":   ext_url or st_url,
                    })
        except Exception as ex:
            log.warning(f"SoloTodo prod {prod_id}: {ex}")

        time.sleep(0.6)   # cortesía a la API

    # Si no hubo resultados de la API, intentar con HTML scraping
    if not results:
        log.info("  API SoloTodo sin resultados, intentando HTML...")
        results = _scrape_solotodo_html(session)

    return results


def _scrape_solotodo_html(session) -> list[dict]:
    """
    Fallback: raspa la página de listado /playstation-5 de SoloTodo.
    Parsea los precios desde el HTML renderizado por Next.js (datos en JSON inline).
    """
    results = []
    url = "https://www.solotodo.cl/playstation-5"
    try:
        r = session.get(url, timeout=20,
                        headers={"Accept": "text/html,*/*",
                                 "User-Agent": random.choice(UA_LIST)})
        if not r.ok:
            return results
        soup = BeautifulSoup(r.text, "html.parser")

        # Buscar el JSON de Next.js con los datos de productos
        script = soup.find("script", id="__NEXT_DATA__")
        if script:
            data  = json.loads(script.string)
            items = (data.get("props", {})
                         .get("pageProps", {})
                         .get("initialData", {})
                         .get("results", []))
            for item in items:
                name  = item.get("name", "PS5")
                price = item.get("min_price") or item.get("best_price")
                iid   = item.get("id", "")
                href  = f"https://www.solotodo.cl/products/{iid}"
                if price:
                    results.append({"store": "SoloTodo", "name": name,
                                    "price": int(price), "url": href})
            return results

        # Si no hay __NEXT_DATA__, parsear precios del HTML directamente
        for a in soup.select("a[href*='/products/']"):
            # Buscar texto con formato "$NNN.NNN"
            price_match = re.search(r"\$\s*[\d\.]+", a.get_text())
            if not price_match:
                continue
            price = clean_price(price_match.group())
            name_el = a.select_one("h2,h3,strong,[class*='name']")
            name    = name_el.get_text(strip=True) if name_el else "PS5"
            href    = a["href"]
            if not href.startswith("http"):
                href = "https://www.solotodo.cl" + href
            if price and price > 100_000:
                results.append({"store": "SoloTodo", "name": name[:60],
                                 "price": price, "url": href})
    except Exception as ex:
        log.warning(f"SoloTodo HTML: {ex}")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 2 — MercadoLibre Chile (API oficial)
# No requiere auth, es pública. Filtra solo consolas nuevas.
# ══════════════════════════════════════════════════════════════════════════════
# Palabras que indican que NO es una consola (para filtrar accesorios)
ML_EXCLUDE = [
    "control", "joystick", "mando", "cargador", "auricular", "headset",
    "soporte", "funda", "cable", "juego", "game", "silla", "teclado",
    "mouse", "cooling", "ventilador", "skin", "protector", "bolso",
]

def scrape_mercadolibre(session) -> list[dict]:
    """
    Scraping del listado web de MercadoLibre Chile.
    La API pública bloquea requests de scripts; el sitio web es más permisivo.
    """
    results = []
    seen    = set()
    urls = [
        "https://listado.mercadolibre.cl/consolas-videojuegos/ps5/_OrderId_PRICE*ASC",
        "https://listado.mercadolibre.cl/ps5-consola_ITEM*CONDITION_2230284/_OrderId_PRICE*ASC",
    ]

    for url in urls:
        try:
            r = session.get(url, timeout=20, headers={
                "User-Agent": random.choice(UA_LIST),
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "es-CL,es;q=0.9",
                "Referer": "https://www.mercadolibre.cl/",
            })
            if not r.ok:
                log.warning(f"MercadoLibre HTTP {r.status_code} en {url}")
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            # Cada producto es un <li class="ui-search-layout__item">
            for card in soup.select("li.ui-search-layout__item")[:12]:
                # Título
                title_el = card.select_one(
                    "h2.poly-box, .poly-component__title, "
                    "h2.ui-search-item__title, .ui-search-item__title"
                )
                # Precio (centavos separados por coma/punto)
                price_el = card.select_one(
                    ".andes-money-amount__fraction, "
                    ".price-tag-fraction, "
                    "span[class*='price-tag-fraction']"
                )
                # Link
                link_el = card.select_one("a.poly-component__anchor, a.ui-search-link")

                if not title_el or not price_el:
                    continue

                name   = title_el.get_text(strip=True)
                price  = clean_price(price_el.get_text())
                link   = link_el["href"] if link_el else url
                # Limpiar link (quitar parámetros de tracking)
                link   = link.split("#")[0].split("?")[0]
                name_l = name.lower()

                uid = name_l[:40]
                if uid in seen:
                    continue

                # Solo consolas PS5 (no accesorios)
                if not ("ps5" in name_l or "playstation 5" in name_l):
                    continue
                if any(k in name_l for k in ML_EXCLUDE):
                    continue
                if not price or price < 200_000:
                    continue

                seen.add(uid)
                results.append({
                    "store": "MercadoLibre",
                    "name":  name[:80],
                    "price": price,
                    "url":   link,
                })

        except Exception as ex:
            log.warning(f"MercadoLibre scraping: {ex}")
        time.sleep(random.uniform(2, 3))

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Motor principal
# ══════════════════════════════════════════════════════════════════════════════
SCRAPERS = [
    ("SoloTodo (Paris/Falabella/Ripley/Lider/etc)", scrape_solotodo),
    ("MercadoLibre Chile",                          scrape_mercadolibre),
]

def fetch_all_prices() -> list[dict]:
    session = make_session()
    all_results = []
    for label, fn in SCRAPERS:
        log.info(f"  → {label}...")
        try:
            r = fn(session)
            log.info(f"     {len(r)} producto(s) encontrados")
            all_results.extend(r)
        except Exception as ex:
            log.error(f"     Error en {label}: {ex}")
        time.sleep(random.uniform(2, 4))
    return all_results


# ══════════════════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════════════════
def send_telegram(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text,
                                     "parse_mode": "HTML",
                                     "disable_web_page_preview": True},
                          timeout=10)
        if not r.ok:
            log.error(f"Telegram error {r.status_code}: {r.text[:200]}")
        else:
            log.info("Mensaje Telegram enviado OK")
    except Exception as ex:
        log.error(f"Telegram: {ex}")


# ══════════════════════════════════════════════════════════════════════════════
# Alertas
# ══════════════════════════════════════════════════════════════════════════════
def check_and_alert(products: list[dict], history: dict, config: dict) -> dict:
    token     = config["telegram_token"]
    chat_id   = config["telegram_chat_id"]
    threshold = config.get("alert_threshold_clp", 500_000)
    today     = datetime.now().strftime("%Y-%m-%d")
    alerts    = []

    for p in products:
        key   = f"{p['store']}|{p['name'][:60]}"
        price = p["price"]
        prev  = history.get(key, {}).get("dates", [])

        # Alerta 1: precio bajo el umbral
        if price <= threshold:
            alerts.append(
                f"🔥 <b>¡OFERTA!</b> Bajo {fmt(threshold)}\n"
                f"📦 {p['name']}\n"
                f"💰 <b>{fmt(price)} CLP</b>\n"
                f"🏪 {p['store']}\n"
                f"🔗 {p['url']}"
            )

        # Alerta 2: bajó vs el precio del día anterior
        if prev:
            last_price = prev[-1]["price"]
            if price < last_price:
                diff = last_price - price
                pct  = diff / last_price * 100
                alerts.append(
                    f"📉 <b>BAJÓ DE PRECIO</b>\n"
                    f"📦 {p['name']}\n"
                    f"   {fmt(last_price)} → <b>{fmt(price)}</b> "
                    f"(-{fmt(diff)} / -{pct:.1f}%)\n"
                    f"🏪 {p['store']}\n"
                    f"🔗 {p['url']}"
                )

        # Guardar en historial (max 60 días)
        if key not in history:
            history[key] = {"store": p["store"], "name": p["name"], "dates": []}
        history[key]["dates"].append({"date": today, "price": price})
        history[key]["dates"] = history[key]["dates"][-60:]

    # Enviar alertas individuales
    if alerts:
        header = f"🎮 <b>PS5 Price Alert Chile</b> — {today}\n{'─'*30}\n\n"
        msg    = header + "\n\n".join(alerts)
        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            send_telegram(token, chat_id, chunk)
        log.info(f"{len(alerts)} alerta(s) enviadas por Telegram.")
    else:
        log.info("Ningún producto cumple criterios de alerta hoy.")

    # Resumen diario
    if config.get("send_daily_summary", True) and products:
        best = min(products, key=lambda x: x["price"])
        msg  = (
            f"📊 <b>Resumen PS5 Chile</b> — {today}\n"
            f"Revisadas {len(set(p['store'] for p in products))} fuentes, "
            f"{len(products)} listado(s).\n\n"
            f"💡 Mejor precio hoy:\n"
            f"   📦 {best['name']}\n"
            f"   💰 <b>{fmt(best['price'])} CLP</b>\n"
            f"   🏪 {best['store']}\n"
            f"   🔗 {best['url']}"
        )
        send_telegram(token, chat_id, msg)

    return history


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("=" * 55)
    log.info("PS5 Price Tracker Chile v2 — iniciando")
    log.info("=" * 55)

    config = load_json(CONFIG_FILE, {})
    if not config.get("telegram_token") or not config.get("telegram_chat_id"):
        log.error("Falta telegram_token o telegram_chat_id en config.json")
        return

    history  = load_json(HISTORY_FILE, {})
    products = fetch_all_prices()
    log.info(f"Total productos: {len(products)}")

    if products:
        # Tabla resumen en consola
        log.info(f"\n{'─'*65}")
        log.info(f"  {'PRECIO':>12}  {'TIENDA':<18}  NOMBRE")
        log.info(f"{'─'*65}")
        for p in sorted(products, key=lambda x: x["price"]):
            log.info(f"  {fmt(p['price']):>12}  {p['store']:<18}  {p['name'][:30]}")
        log.info(f"{'─'*65}")

        history = check_and_alert(products, history, config)
        save_json(HISTORY_FILE, history)
        log.info("Historial actualizado y guardado.")
    else:
        log.warning("No se encontraron productos.")
        log.warning("Causas comunes:")
        log.warning("  1. Sin internet — verifica tu conexión")
        log.warning("  2. IP bloqueada — espera 15 min o cambia de red")
        log.warning("  3. API cambió — abre un issue en el repo")
        send_telegram(
            config["telegram_token"], config["telegram_chat_id"],
            f"⚠️ <b>PS5 Tracker</b> ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
            "No se encontraron productos hoy.\n"
            "Posible bloqueo de IP o cambio en la API. Revisa los logs."
        )

    log.info("Fin.\n")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        # Modo diagnóstico: prueba las APIs sin necesitar config.json ni Telegram
        print("\n🔍 MODO DIAGNÓSTICO — probando APIs directamente\n")
        session = make_session()

        print("── SoloTodo API (primer producto: PS5 Slim 1TB Digital) ──")
        url = "https://publicapi.solotodo.com/products/225751/entities/?stores=3,5,10&ordering=active_registry__normal_price"
        try:
            r = session.get(url, timeout=15, headers={"Accept": "application/json"})
            print(f"  HTTP {r.status_code} | Content-Type: {r.headers.get('Content-Type','?')}")
            raw = r.json()
            print(f"  Tipo de respuesta: {type(raw).__name__}")
            entities = raw if isinstance(raw, list) else raw.get("results", [])
            print(f"  Entidades recibidas: {len(entities)}")
            if entities:
                print(f"  Primera entidad (keys): {list(entities[0].keys()) if isinstance(entities[0], dict) else entities[0]}")
                reg = entities[0].get("active_registry") or {}
                print(f"  Precio: {reg.get('offer_price') or reg.get('normal_price')}")
                print(f"  Tienda: {(entities[0].get('store') or {}).get('name')}")
            else:
                print(f"  Respuesta completa: {str(raw)[:300]}")
        except Exception as ex:
            print(f"  ERROR: {ex}")

        print("\n── MercadoLibre API ──")
        url = "https://api.mercadolibre.com/sites/MLC/search?q=PS5+consola&condition=new&limit=3&sort=price_asc"
        try:
            r = session.get(url, timeout=15, headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": random.choice(UA_LIST),
                "Referer": "https://listado.mercadolibre.cl/",
            })
            print(f"  HTTP {r.status_code}")
            if r.ok:
                items = r.json().get("results", [])
                print(f"  Resultados: {len(items)}")
                for it in items[:3]:
                    print(f"  · {it['title'][:55]:55} ${it['price']:,.0f}")
            else:
                print(f"  Respuesta: {r.text[:200]}")
        except Exception as ex:
            print(f"  ERROR: {ex}")

        print("\n✅ Diagnóstico completo. Si ves precios arriba, el script funcionará.\n")
    else:
        main()