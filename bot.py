# bot_fixed_safe.py - deletions-proof, spam-proof sürüm
# Kullanım: render/hosting ortamına koyup `python bot_fixed_safe.py` ile çalıştırın.
# Özellikler:
# - Kök (/) endpoint'i sadece güvenli JSON döner (ping için güvenli)
# - /komut isteği için komut+text cache (CACHE_TTL süresi boyunca aynı komut tekrar gönderilmez)
# - _send_and_collect: daha sıkı filtreleme, fallback ve makul timeouts
# - Basit logging ve hata yönetimi

import asyncio
import threading
import time
import traceback
import json
import re
from datetime import datetime, timezone
from flask import Flask, request, Response
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ========== AYARLAR ==========
api_id = 17570480
api_hash = "18c5be05094b146ef29b0cb6f6601f1f"
STRING_SESSION = "1ApWapzMBuzn4w931iuDQKpfd5VNwQn_YGuxiWl-sulb5H7QwaTmu2WY-G0DxbRuMTUvMLFWCPT-YP61bf7HDmNRO7VgvLIn0Dt6vYJZjrDrIqtSGC4mdIyYeDOUnl5u8fPHNtjxk7XDt78dFfe70ZxjjY1k87Aim5y4ou-LlyM1GJ3aL88jYMrCMSWB0oaLfEKIDmz3hHVgUxm7y5qJHoxaOnhCg-BojF4tPIoYbqgKz9rcwE3eZTd9ZrbOzePNjQac9zalvii1KEjCGNpXkHLmNPLPa_IMXy9hk5j85anSHtxH0c2RYcmhdMkn1AuLljPlO-gEQwxMMYaQPLqIpGEj__xJthHE="
BOT_USERNAME = "JarvisSohbetBot"
YAPIMCI_TEXT = "👷 Yapımcı: @Keneviiiz ve @Nabi_backend (Telegram'dan ulaşabilirsiniz) 😁"

# Cache / dedupe ayarları
CACHE_TTL = 300  # saniye; aynı komut bu süre içinde yeniden çalıştırılmayacak
_response_cache = {}  # full_cmd -> {"timestamp": ts, "result": response_dict}

# Globals to be set after client start
BOT_ENTITY_ID = None

# ========== TELETHON SETUP ==========
loop = asyncio.new_event_loop()
client = TelegramClient(StringSession(STRING_SESSION), api_id, api_hash, loop=loop)

async def _start_client():
    global BOT_ENTITY_ID
    try:
        await client.start()
        me = await client.get_me()
        print(f"✅ Telethon bağlandı: @{me.username} ({me.id})")
        try:
            bot_ent = await client.get_entity(BOT_USERNAME)
            BOT_ENTITY_ID = getattr(bot_ent, "id", None)
            print(f"✅ Hedef bot id: {BOT_ENTITY_ID} ( @{BOT_USERNAME} )")
        except Exception as e:
            print("⚠️ BOT entity alınamadı:", e)
            BOT_ENTITY_ID = None
    except Exception as e:
        print("Telethon başlatılırken hata:", e)


def _start_loop_thread():
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_start_client())
        loop.run_forever()
    except Exception as exc:
        print("Loop thread hata:", exc)

_thread = threading.Thread(target=_start_loop_thread, daemon=True)
_thread.start()

# ========== GÜVENLİ GÖNDERME VE TOPLAMA ==========

def _looks_like_spam_text(text: str) -> bool:
    if not text:
        return False
    # çok sayıda kullanıcı mention'ı (inline) -> spam-like
    if text.count("tg://user?id=") >= 6:
        return True
    # t.me/+ linkleri (private group invite) -> genelde davet linki
    if "https://t.me/+" in text or "t.me/+" in text:
        return True
    # aşırı uzun kullanıcı listesi markdown ile
    if len(re.findall(r"\[.*?\]\(tg://user\?id=\d+\)", text)) >= 6:
        return True
    return False


async def _send_and_collect(cmd: str, first_timeout: int = 8, collect_seconds: int = 20, fetch_limit: int = 60):
    """
    Gönder -> yeni mesajlar + edit'leri yakala -> fallback ile son mesajları filtreleyerek al
    Döner: list[str] (sıralı parçalar)
    """
    global BOT_ENTITY_ID

    parts_by_id = {}

    # Ensure BOT_ENTITY_ID; try to fetch if None
    if BOT_ENTITY_ID is None:
        try:
            bot_ent = await client.get_entity(BOT_USERNAME)
            BOT_ENTITY_ID = getattr(bot_ent, "id", None)
        except Exception as e:
            print("BOT_ENTITY_ID alınamadı (send_and_collect):", e)

    try:
        sent = await client.send_message(BOT_USERNAME, cmd)
    except Exception as e:
        raise RuntimeError(f"Mesaj gönderilemedi: {e}")

    first_future = loop.create_future()

    async def _process_msg_obj(msg):
        try:
            if msg is None:
                return
            sender_id = getattr(msg, "sender_id", None)
            if BOT_ENTITY_ID is not None and sender_id != BOT_ENTITY_ID:
                return
            if getattr(msg, "fwd_from", None) is not None:
                return
            text = msg.text or ""
            if _looks_like_spam_text(text):
                print("Filtrelendi (spam-like):", (text[:120] + "...") if len(text) > 120 else text)
                return
            # accept messages with id >= sent.id
            if getattr(msg, "id", 0) >= getattr(sent, "id", 0):
                parts_by_id[msg.id] = text
                if not first_future.done():
                    first_future.set_result(True)
        except Exception as ex:
            print("process_msg_obj hata:", ex)

    async def _new_handler(event):
        await _process_msg_obj(event.message)

    async def _edit_handler(event):
        await _process_msg_obj(event.message)

    # register handlers
    try:
        if BOT_ENTITY_ID is not None:
            client.add_event_handler(_new_handler, events.NewMessage(from_users=BOT_ENTITY_ID))
            client.add_event_handler(_edit_handler, events.MessageEdited(from_users=BOT_ENTITY_ID))
        else:
            client.add_event_handler(_new_handler, events.NewMessage())
            client.add_event_handler(_edit_handler, events.MessageEdited())
    except Exception as e:
        print("Event handler ekleme hatası:", e)

    # 1) İlk cevabı bekle (kısa)
    try:
        await asyncio.wait_for(first_future, timeout=first_timeout)
    except asyncio.TimeoutError:
        # ilk gelmediyse yine collect'e geç
        pass

    # 2) collect_seconds boyunca bekle
    await asyncio.sleep(collect_seconds)

    # remove handlers
    try:
        client.remove_event_handler(_new_handler)
    except Exception:
        pass
    try:
        client.remove_event_handler(_edit_handler)
    except Exception:
        pass

    # fallback: serverdan son mesajları çek
    try:
        msgs = await client.get_messages(BOT_USERNAME, limit=fetch_limit)
        for m in reversed(msgs):
            try:
                if getattr(m, "sender_id", None) != BOT_ENTITY_ID:
                    continue
                if getattr(m, "fwd_from", None) is not None:
                    continue
                if hasattr(sent, "date") and hasattr(m, "date"):
                    if m.date < sent.date:
                        continue
                else:
                    if getattr(m, "id", 0) < getattr(sent, "id", 0):
                        continue
                text = m.text or ""
                if _looks_like_spam_text(text):
                    continue
                parts_by_id[m.id] = text
            except Exception as ie:
                print("Fallback mesaj işleme hatası:", ie)
    except Exception as e:
        print("Fallback get_messages hatası:", e)

    if not parts_by_id:
        return ["⏳ Cevap gelmedi veya filtrelendi"]

    ordered = [parts_by_id[k] for k in sorted(parts_by_id.keys())]
    return ordered


# ========== FLASK & HELPERS ==========
app = Flask(__name__)


def pretty_json_response(obj: dict, status: int = 200) -> Response:
    txt = json.dumps(obj, ensure_ascii=False, indent=2)
    return Response(txt, status=status, mimetype="application/json; charset=utf-8")


def make_formatted_text(responses: list[str]) -> str:
    lines = []
    for idx, part in enumerate(responses, start=1):
        part_clean = part.strip()
        lines.append(f"{idx}. {part_clean}")
    return "\n\n".join(lines)


@app.route("/komut", methods=["GET"])
def komut_api():
    yapimci = YAPIMCI_TEXT
    cmd = request.args.get("cmd", "").strip()
    text = request.args.get("text", "").strip()
    if not cmd:
        return pretty_json_response({"yapimci": yapimci, "hata": "Komut girilmedi."}, status=400)

    if cmd.lower() == "yapayzeka" and text:
        text = text.title()

    full_cmd = f"/{cmd} {text}".strip()

    # cache temizleme (eski kayıtlar)
    now = time.time()
    expired = [k for k, v in _response_cache.items() if now - v["timestamp"] > CACHE_TTL]
    for k in expired:
        _response_cache.pop(k, None)

    # Eğer cache'te varsa cached cevabı dön (spam'ı önlemek için)
    if full_cmd in _response_cache:
        cached = _response_cache[full_cmd]["result"]
        # Bilgilendirici not ekle
        cached_copy = dict(cached)
        cached_copy["note"] = "🔒 Bu yanıt cache'den döndü (spam önleme)." 
        return pretty_json_response(cached_copy)

    # Komutu cache'e kaydetmeden önce çalıştır
    # belirli komutlara özel collect_seconds
    key = cmd.lower()
    if key == "piyasa":
        collect_seconds = 7
    elif key == "yapayzeka":
        collect_seconds = 63
    else:
        collect_seconds = 25

    try:
        future = asyncio.run_coroutine_threadsafe(
            _send_and_collect(full_cmd, first_timeout=12, collect_seconds=collect_seconds, fetch_limit=80),
            loop
        )
        # result timeout: collect_seconds + makul ek süre
        timeout = collect_seconds + 20
        cevaplar = future.result(timeout=timeout)
    except Exception as e:
        tb = traceback.format_exc()
        print("Hata (send_and_collect):", tb)
        return pretty_json_response({"yapimci": yapimci, "hata": str(e)}), 500
    finally:
        try:
            future.cancel()
        except Exception:
            pass

    formatted = make_formatted_text(cevaplar)
    result = {
        "yapimci": yapimci,
        "komut": full_cmd,
        "zaman": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "cevap_sayisi": len(cevaplar),
        "cevaplar": cevaplar,
        "formatted": formatted,
        "not": "✨ Cevaplar numaralandırıldı; filtreleme uygulandı (forward/davet/çok-mention içerenler atıldı)."
    }

    # cache'e kaydet (aynı komut kısa sürede tekrar gelmesin)
    _response_cache[full_cmd] = {"timestamp": time.time(), "result": result}

    return pretty_json_response(result)


@app.route("/", methods=["GET"])
def root():
    """API ana endpoint: sadece yapımcı bilgisini gösterir. Ping/job için güvenli."""
    return pretty_json_response({
        "yapimci": YAPIMCI_TEXT,
        "not": "Keneviz VIP API çalışıyor ✅ (sadece JSON, ping için güvenli)"
    })


if __name__ == "__main__":
    print("✅ Başlatılıyor (spam-proof): http://127.0.0.1:5000/komut?cmd=yapayzeka&text=Merhaba")
    app.run(host="0.0.0.0", port=5000)
