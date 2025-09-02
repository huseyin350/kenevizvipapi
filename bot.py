# bot.py - deletions-proof sürüm
import asyncio
import threading
import time
import traceback
import json
from datetime import datetime
from flask import Flask, request, Response
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ========== AYARLAR ==========
api_id = 17570480
api_hash = "18c5be05094b146ef29b0cb6f6601f1f"
STRING_SESSION = "1ApWapzMBuzn4w931iuDQKpfd5VNwQn_YGuxiWl-sulb5H7QwaTmu2WY-G0DxbRuMTUvMLFWCPT-YP61bf7HDmNRO7VgvLIn0Dt6vYJZjrDrIqtSGC4mdIyYeDOUnl5u8fPHNtjxk7XDt78dFfe70ZxjjY1k87Aim5y4ou-LlyM1GJ3aL88jYMrCMSWB0oaLfEKIDmz3hHVgUxm7y5qJHoxaOnhCg-BojF4tPIoYbqgKz9rcwE3eZTd9ZrbOzePNjQac9zalvii1KEjCGNpXkHLmNPLPa_IMXy9hk5j85anSHtxH0c2RYcmhdMkn1AuLljPlO-gEQwxMMYaQPLqIpGEj__xJthHE="
BOT_USERNAME = "JarvisSohbetBot"
YAPIMCI_TEXT = "👷 Yapımcı: @Keneviiiz ve @Nabi_backend (Telegram'dan ulaşabilirsiniz) 😁"

# ========== TELETHON ==========
loop = asyncio.new_event_loop()
client = TelegramClient(StringSession(STRING_SESSION), api_id, api_hash, loop=loop)

async def _start_client():
    await client.start()
    me = await client.get_me()
    print(f"✅ Telethon bağlandı: @{me.username} ({me.id})")

def _start_loop_thread():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_start_client())
    loop.run_forever()

_thread = threading.Thread(target=_start_loop_thread, daemon=True)
_thread.start()

# ========== GÜVENLİ GÖNDERME VE TOPLAMA (silme/edit vs) ==========
async def _send_and_collect(cmd: str, first_timeout: int = 12, collect_seconds: int = 25, fetch_limit: int = 60):
    """
    - Gönder -> ilk cevabı bekle (first_timeout)
    - Sonra kesinlikle collect_seconds kadar bekle
    - NewMessage veya MessageEdited olaylarını yakala
    - collect bittikten sonra fallback: get_messages ile son `fetch_limit` mesajı çekip
      sent.date sonrası Jarvis mesajlarını al (silinmiş parçalar geri gelmez ama son gönderilenler yakalanır)
    - Döner: list[str] (sıralı parçalar)
    """
    parts_by_id = {}       # msg.id -> text
    try:
        sent = await client.send_message(BOT_USERNAME, cmd)
    except Exception as e:
        raise RuntimeError(f"Mesaj gönderilemedi: {e}")

    first_future = loop.create_future()

    # process helper
    async def _process_msg_obj(msg):
        try:
            if msg is None:
                return
            # Only keep text (safeguard)
            text = msg.text or ""
            # accept messages with id >= sent.id (includes edits same id)
            if getattr(msg, "id", 0) >= getattr(sent, "id", 0):
                parts_by_id[msg.id] = text
                if not first_future.done():
                    first_future.set_result(True)
        except Exception as ex:
            print("process_msg_obj hata:", ex)

    # handlers
    async def _new_handler(event):
        await _process_msg_obj(event.message)

    async def _edit_handler(event):
        await _process_msg_obj(event.message)

    # register
    client.add_event_handler(_new_handler, events.NewMessage(from_users=BOT_USERNAME))
    client.add_event_handler(_edit_handler, events.MessageEdited(from_users=BOT_USERNAME))

    # 1) İlk cevabı bekle
    try:
        await asyncio.wait_for(first_future, timeout=first_timeout)
    except asyncio.TimeoutError:
        # ilk gelmediyse yine collect'e geç
        pass

    # 2) Kesinlikle collect_seconds boyunca bekle
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

    # fallback: serverdan son mesajları çek ve sent.date sonrası olanları al
    try:
        # get entity to ensure we can filter sender_id
        bot_entity = await client.get_entity(BOT_USERNAME)
        # çek
        msgs = await client.get_messages(BOT_USERNAME, limit=fetch_limit)
        for m in reversed(msgs):  # tersten (eski->yeni) değerlendirme
            # only consider messages from the bot entity and after our sent message time
            if getattr(m, "sender_id", None) == getattr(bot_entity, "id", None):
                if hasattr(sent, "date") and hasattr(m, "date"):
                    if m.date >= sent.date:
                        parts_by_id[m.id] = m.text or ""
                else:
                    # eğer date yoksa id bazlı kontrol
                    if getattr(m, "id", 0) >= getattr(sent, "id", 0):
                        parts_by_id[m.id] = m.text or ""
    except Exception as e:
        # fallback hata verse bile normal yolla dön
        print("Fallback get_messages hatası:", e)

    # Eğer hiç parça yoksa bilgilendir
    if not parts_by_id:
        return ["⏳ Cevap gelmedi"]

    # sırala id'ye göre (eski -> yeni)
    ordered = [parts_by_id[k] for k in sorted(parts_by_id.keys())]

    # Eğer parçalardan sadece "hazırlanıyor..." gibi bir placeholder kaldıysa ve son parça çok uzun ise,
    # bazı botlar önce placeholder atıp sonra finali editliyor; edits yakalandı ama yine de final eksikse
    # (bu durumda ordered içerik genelde final'i kapsar). Biz doğrudan döndürüyoruz.
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
        cevaplar = future.result(timeout=collect_seconds + 25)
    except Exception as e:
        tb = traceback.format_exc()
        print("Hata (send_and_collect):", tb)
        return pretty_json_response({"yapimci": yapimci, "hata": str(e)}), 500

    formatted = make_formatted_text(cevaplar)
    result = {
        "yapimci": yapimci,
        "komut": full_cmd,
        "zaman": datetime.utcnow().isoformat() + "Z",
        "cevap_sayisi": len(cevaplar),
        "cevaplar": cevaplar,
        "formatted": formatted,
        "not": "✨ Cevaplar numaralandırıldı; emojiler düzgün görünmelidir."
    }
    return pretty_json_response(result)

if __name__ == "__main__":
    print("✅ Başlatılıyor (deletions-proof): http://127.0.0.1:5000/komut?cmd=yapayzeka&text=Merhaba")
    app.run(host="0.0.0.0", port=5000)
