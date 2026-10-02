"""Tennis Scout - avisa por Telegram cuando salen partidos de tenis masculino
(ATP, Challenger, Grand Slam, Copa Davis) con cuotas Pinnacle.
Fuente: API publica de invitado de Pinnacle (la misma que usa pinnacle.com).

Uso:
  python bot.py          # arranca el bot
  python bot.py --test   # manda un mensaje de prueba a Telegram y comprueba Pinnacle
  python bot.py --once   # un solo ciclo (GitHub Actions)

Configuracion en .env (o variables de entorno):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
  PINNACLE_API_KEY   -> X-API-Key de pinnacle.com (la rotan cada pocos meses)
  POLL_SECONDS       -> opcional, por defecto 60
"""
import os, re, sys, json, time, requests
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

TG_TOKEN = os.environ["TELEGRAM_TOKEN"].strip()
TG_CHAT  = os.environ["TELEGRAM_CHAT_ID"].strip()
PIN_KEY  = os.getenv("PINNACLE_API_KEY", "").strip()
POLL_SEC = float(os.getenv("POLL_SECONDS", "60"))
BASE     = "https://guest.api.arcadia.pinnacle.com/0.1"
SPORT    = 33  # tenis
STATE    = Path(__file__).with_name("known_matches.json")
HEADERS  = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.pinnacle.com/",
            "Accept": "application/json", "X-API-Key": PIN_KEY}

INCLUDE = re.compile(r"\bATP\b|challenger|australian open|roland garros|french open|"
                     r"wimbledon|us open|davis cup", re.I)
EXCLUDE = re.compile(r"\bWTA\b|\bITF\b|women|ladies|femen|junior|exhibition", re.I)


class BadKey(Exception):
    pass


def api(path):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, timeout=30)
    if r.status_code in (401, 403):
        raise BadKey(f"Pinnacle rechaza la API key ({r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    return r.json() if r.content else []


def clean(name):
    return re.sub(r"\s*\(Games\)\s*$", "", str(name or "")).strip()


def american_to_decimal(p):
    p = float(p)
    return 1 + p / 100 if p > 0 else 1 + 100 / abs(p)


def get_matches():
    """Partidos ATP prematch, deduplicados por partido padre."""
    out = {}
    for m in api(f"/sports/{SPORT}/matchups"):
        if m.get("type", "matchup") != "matchup" or m.get("isLive"):
            continue
        league = (m.get("league") or {}).get("name", "")
        if EXCLUDE.search(league) or not INCLUDE.search(league):
            continue
        parts = m.get("participants") or []
        home = next((clean(p.get("name")) for p in parts if p.get("alignment") == "home"), "")
        away = next((clean(p.get("name")) for p in parts if p.get("alignment") == "away"), "")
        if not home or not away:
            continue
        odds_id = m.get("parentId") or m.get("id")
        if odds_id in out:
            continue
        out[odds_id] = dict(id=odds_id, tour=league, p1=home, p2=away, start=m.get("startTime", ""))
    return out


def get_moneylines():
    """{matchupId: (cuota_home, cuota_away)} en decimal, solo partido completo."""
    res = {}
    for mk in api(f"/sports/{SPORT}/markets/straight?primaryOnly=true"):
        if mk.get("type") != "moneyline" or mk.get("period", 0) != 0:
            continue
        prices = {p.get("designation"): p.get("price") for p in mk.get("prices") or []}
        if prices.get("home") is None or prices.get("away") is None:
            continue
        res[mk.get("matchupId")] = (american_to_decimal(prices["home"]),
                                   american_to_decimal(prices["away"]))
    return res


def tg(text):
    for _ in range(5):
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json=dict(chat_id=TG_CHAT, text=text, parse_mode="HTML",
                                    disable_web_page_preview=True), timeout=30)
        if r.status_code == 429:  # flood: esperar lo que pida Telegram
            wait = (r.json().get("parameters") or {}).get("retry_after", 5)
            time.sleep(wait + 1)
            continue
        r.raise_for_status()
        time.sleep(2)  # pausa entre mensajes para no provocar 429
        return
    raise RuntimeError("Telegram sigue devolviendo 429")


def fmt_time(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
        return str(s)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def msg(info, prices):
    q1, q2 = (f"{prices[0]:.2f}", f"{prices[1]:.2f}") if prices else ("N/A", "N/A")
    return (f"🎾 <b>NUEVO PARTIDO ATP</b> 📅 PREMATCH\n\n"
            f"🏆 <b>Torneo:</b> {esc(info['tour'])}\n"
            f"👤 <b>{esc(info['p1'])}</b> vs <b>{esc(info['p2'])}</b>\n"
            f"⏰ <b>Hora:</b> {fmt_time(info['start'])}\n\n"
            f"📊 <b>Cuotas Pinnacle:</b>\n"
            f"🟢 {esc(info['p1'])}: <code>{q1}</code>\n"
            f"🔵 {esc(info['p2'])}: <code>{q2}</code>\n\n"
            f"🏦 <b>Bookmaker:</b> Pinnacle\n"
            f"🆔 <code>{info['id']}</code>")


def load():
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save(known):
    STATE.write_text(json.dumps(sorted(known)), encoding="utf-8")


def cycle():
    known = load()
    matches = get_matches()
    lines = get_moneylines()
    n_new = 0
    for mid, info in matches.items():
        if str(mid) in known:
            continue
        tg(msg(info, lines.get(mid)))
        known.add(str(mid))
        save(known)  # guardar tras cada aviso: si se corta, no repite
        n_new += 1
    print(f"{datetime.now():%H:%M:%S} partidos_ATP={len(matches)} con_cuota="
          f"{sum(1 for m in matches if m in lines)} avisos_nuevos={n_new}", flush=True)


def check_key():
    if not PIN_KEY:
        raise SystemExit("Falta PINNACLE_API_KEY en el archivo .env")
    try:
        api(f"/sports/{SPORT}/matchups?limit=1")
    except BadKey as e:
        raise SystemExit(f"{e}\nCopia la key nueva desde pinnacle.com (DevTools > Red > "
                         f"peticion a arcadia > cabeceras > X-API-Key) y ponla en .env")


def single_instance():
    """Evita dos copias a la vez: el puerto local solo lo puede ocupar una."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47653))
    except OSError:
        print("Tennis Scout ya se esta ejecutando en otra ventana.")
        sys.exit(3)
    return s


if __name__ == "__main__":
    if "--once" in sys.argv:  # un solo ciclo (para GitHub Actions)
        if not PIN_KEY:
            raise SystemExit("Falta PINNACLE_API_KEY")
        try:
            cycle()
        except BadKey as e:
            print("error:", e, flush=True)
            tg("⚠️ Tennis Scout: Pinnacle ha cambiado la API key. Hay que actualizarla")
            sys.exit(1)
        sys.exit()
    if "--test" not in sys.argv:
        _lock = single_instance()
    check_key()
    if "--test" in sys.argv:
        tg("🧪 Prueba Tennis Scout: Telegram OK")
        print("Mensaje de prueba enviado. Pinnacle OK.")
        sys.exit()
    tg("✅ Tennis Scout arrancado")
    while True:
        try:
            cycle()
        except BadKey as e:
            print("error:", e, flush=True)
            try:
                tg("⚠️ Tennis Scout: Pinnacle ha cambiado la API key. Hay que actualizarla en .env")
            except Exception:
                pass
            time.sleep(30 * 60)
            continue
        except Exception as e:
            print("error:", e, flush=True)
        time.sleep(POLL_SEC)
