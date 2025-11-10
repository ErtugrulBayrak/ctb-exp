import os
import json
import time
import telegram
import asyncio
import re
import httpx
from newsapi import NewsApiClient
import google.generativeai as genai
from binance.client import Client
from binance.exceptions import BinanceAPIException  # <<< YENİ IMPORT <<<
import pandas as pd
import pandas_ta as ta

# ... (API ANAHTARLARI ve diğer tüm fonksiyonlar aynı kalıyor) ...
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY')
BINANCE_SECRET_KEY = os.environ.get('BINANCE_SECRET_KEY')
ISLENMIS_HABERLER_DOSYASI = "islenmis_haberler.txt"

def islenmis_haberleri_yukle():
    if not os.path.exists(ISLENMIS_HABERLER_DOSYASI): return set()
    with open(ISLENMIS_HABERLER_DOSYASI, 'r') as f: return set(line.strip() for line in f)

def haberi_kaydet(haber_linki):
    with open(ISLENMIS_HABERLER_DOSYASI, 'a') as f: f.write(haber_linki + '\n')

def haber_basligi_uygun_mu(baslik):
    anahtar_kelimeler = ['bitcoin', 'ethereum', 'crypto', 'blockchain', 'binance', 'solana', 'ripple', 'kripto', 'coin', 'token', 'web3', 'nft', 'etf']
    return any(kelime in baslik.lower() for kelime in anahtar_kelimeler)

def haberleri_cek(api_key):
    try:
        newsapi = NewsApiClient(api_key=api_key)
        all_articles = newsapi.get_everything(q='crypto OR bitcoin OR ethereum', sort_by='publishedAt', page_size=50)
        return [{'baslik': a['title'], 'link': a['url'], 'kaynak': a['source']['name']} for a in all_articles.get('articles', [])]
    except Exception as e:
        print(f"HATA (NewsAPI): {e}"); return []

def haberleri_analiz_et(api_key, haber_basligi):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        prompt = f"""
        Bir kripto para piyasa analisti gibi davran... (Bu kısım aynı)
        """
        response = model.generate_content(prompt)
        json_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(json_text)
    except Exception as e:
        if 'You exceeded your current quota' in str(e): print("HATA (Gemini AI): Günlük API kotası aşıldı."); return "KOTA_ASILDI"
        print(f"HATA (Gemini AI): {e}"); return None

# <<< ÇÖZÜM: BU FONKSİYON DAHA AKILLI HALE GETİRİLDİ >>>
def get_teknik_analiz(coin_sembolu, binance_client):
    try:
        parite = f"{coin_sembolu.upper()}USDT"
        mumlar = binance_client.get_historical_klines(parite, Client.KLINE_INTERVAL_4HOUR, "3 days ago UTC")
        if len(mumlar) < 15: print(f"UYARI (Teknik Analiz - {parite}): RSI için yeterli veri yok."); return None
        df = pd.DataFrame(mumlar, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['close'] = pd.to_numeric(df['close'])
        df.ta.rsi(length=14, append=True)
        if 'RSI_14' not in df.columns or df['RSI_14'].dropna().empty: print(f"UYARI (Teknik Analiz - {parite}): RSI değeri hesaplanamadı."); return None
        son_rsi = df['RSI_14'].iloc[-1]
        rsi_yorumu = "Aşırı Alım Bölgesi 📈" if son_rsi > 70 else "Aşırı Satım Bölgesi 📉" if son_rsi < 30 else "Nötr Bölge 📊"
        return f"{son_rsi:.2f} ({rsi_yorumu})"
    except BinanceAPIException as e:
        # "Invalid symbol" hatasını özel olarak yakala
        if e.code == -1121:
            print(f"UYARI (Teknik Analiz): {coin_sembolu.upper()}/USDT paritesi Binance'te bulunamadı.")
            return None # Hata vermek yerine None döndür ve yoluna devam et
        else:
            # Diğer Binance API hataları için
            print(f"HATA (Teknik Analiz - {coin_sembolu}): {e}")
            return None
    except Exception as e:
        # Diğer beklenmedik hatalar için
        print(f"HATA (Teknik Analiz - {coin_sembolu}): Beklenmedik Hata -> {e}")
        return None

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    try:
        timeout_config = httpx.Timeout(30.0, read=30.0, connect=30.0)
        async_client = httpx.AsyncClient(timeout=timeout_config)
        bot = telegram.Bot(token=bot_token, arbitrary_async_client=async_client)
        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML')
        print("✅ Telegram bildirimi başarıyla gönderildi.")
    except Exception as e:
        print(f"❌ HATA (Telegram): {e}")

# --- ANA DÖNGÜ (Değişiklik yok) ---
async def ana_dongu():
    # ... Bu fonksiyonun içeriği öncekiyle aynı ...
    if not all([NEWSAPI_KEY, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BINANCE_API_KEY, BINANCE_SECRET_KEY]):
        print("HATA: Lütfen tüm Ortam Değişkenlerini (Environment Variables) ayarlayın."); return
    binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY)
    while True:
        print(f"\n--- {time.ctime()} --- Döngü başlıyor ---")
        islenmis_haberler = islenmis_haberleri_yukle(); print(f"{len(islenmis_haberler)} adet haber daha önce işlenmiş.")
        cekilen_haberler = haberleri_cek(NEWSAPI_KEY)
        if cekilen_haberler:
            yeni_haber_sayisi = 0
            for haber in cekilen_haberler:
                if haber['link'] in islenmis_haberler or not haber_basligi_uygun_mu(haber['baslik']):
                    if haber['link'] not in islenmis_haberler: haberi_kaydet(haber['link'])
                    continue
                yeni_haber_sayisi += 1
                print(f"📰 Yeni Haber Analiz Ediliyor: {haber['baslik']}")
                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'])
                if analiz_sonucu == "KOTA_ASILDI": print("Gemini kotası dolduğu için bu döngü durduruluyor."); break
                if analiz_sonucu and analiz_sonucu.get('kripto_ile_ilgili_mi') and analiz_sonucu.get('onem_derecisi') == 'Yüksek':
                    print("🔥 ÖNEMLİ HABER! Teknik analiz yapılıyor...")
                    teknik_analiz_mesaji = ""
                    etkilenen_coinler = analiz_sonucu.get('etkilenen_coinler', [])
                    if etkilenen_coinler:
                        teknik_analiz_mesaji += "<b>Teknik Analiz (4s RSI):</b>\n"
                        for coin in etkilenen_coinler:
                            rsi_degeri = get_teknik_analiz(coin, binance_client)
                            if rsi_degeri: teknik_analiz_mesaji += f" - <b>{coin.upper()}/USDT:</b> {rsi_degeri}\n"
                    coinler_str = ", ".join(etkilenen_coinler)
                    mesaj = (f"🚨 <b>YÜKSEK ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n<b>Başlık:</b> {haber['baslik']}\n<b>Kaynak:</b> {haber['kaynak']}\n\n<b>Duygu:</b> {analiz_sonucu.get('duygu')}\n<b>Etkilenen Coinler:</b> {coinler_str}\n\n{teknik_analiz_mesaji}\n<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr')}</i>\n\n<a href='{haber['link']}'>Habere Git</a>")
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                haberi_kaydet(haber['link']); time.sleep(2)
            print(f"Bu döngüde {yeni_haber_sayisi} yeni haber analiz edildi.")
        bekleme_suresi = 1800; print(f"--- Döngü tamamlandı. {bekleme_suresi / 60} dakika bekleniyor... ---"); await asyncio.sleep(bekleme_suresi)

if __name__ == "__main__":
    asyncio.run(ana_dongu())