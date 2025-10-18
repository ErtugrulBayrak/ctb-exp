#Ozellik: Haber icerigi analizi (newspaper3k) eklendi
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
from binance.exceptions import BinanceAPIException
import pandas as pd
import pandas_ta as ta
from newspaper import Article, Config

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

def get_haber_icerigi(url):
    try:
        config = Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
        config.request_timeout = 20
        config.verify_ssl = False
        article = Article(url, config=config)
        article.download()
        article.parse()
        return article.text[:5000]
    except Exception as e:
        print(f"HATA (Newspaper3k - {url}): İçerik çekilemedi. Sebep: {e}"); return None

# <<< ÇÖZÜM: BU FONKSİYON TAMAMEN YENİLENDİ >>>
def haberleri_analiz_et(api_key, haber_basligi, haber_icerigi):
    try:
        genai.configure(api_key=api_key)
        
        # Güvenlik filtrelerini daha az katı olacak şekilde yapılandır
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
        
        prompt = f"""
        Bir kripto para piyasa analisti gibi davran. Sana bir haberin hem başlığını hem de tam metnini vereceğim. Analizini yaparken **asıl olarak tam metne odaklan**, başlık sadece bir referanstır. Çıktını SADECE JSON formatında ver.

        Haber Başlığı: "{haber_basligi}"
        Haber Metni: "{haber_icerigi}"

        İstediğim JSON formatı şu anahtarlara sahip olmalı:
        - "kripto_ile_ilgili_mi": boolean
        - "onem_derecisi": string ('Düşük', 'Orta', 'Yüksek', 'Çok Yüksek')
        - "etkilenen_coinler": string array (Binance'te listelenen resmi ticker sembolleri, örn: "BTC", "ETH")
        - "duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif')
        - "ozet_tr": string (Tüm metni okuyarak 1-2 cümlelik detaylı bir özet çıkar.)

        JSON Cevabı:
        """
        response = model.generate_content(prompt)
        
        # JSON'a çevirmeden önce yanıtın geçerli olup olmadığını kontrol et
        # Güvenlik filtresi devreye girerse 'response.text' hata verir.
        if not response.parts:
            print(f"HATA (Gemini AI): Yanıt alınamadı. Muhtemel güvenlik engeli. Geri bildirim: {response.prompt_feedback}")
            return None

        json_text = response.text.strip().replace('```json', '').replace('```', '')
        
        # JSON'a çevirmeyi dene, başarısız olursa hatayı ve ham metni yazdır
        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print(f"HATA (Gemini AI): Geçersiz JSON formatı alındı. Hata: {e}")
            print(f"Alınan Ham Metin: '{json_text}'")
            return None

    except Exception as e:
        print(f"HATA (Gemini AI): Beklenmedik Hata -> {e}"); return None

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
        if e.code == -1121: print(f"UYARI (Teknik Analiz): {coin_sembolu.upper()}/USDT paritesi Binance'te bulunamadı."); return None
        else: print(f"HATA (Teknik Analiz - {coin_sembolu}): {e}"); return None
    except Exception as e:
        print(f"HATA (Teknik Analiz - {coin_sembolu}): Beklenmedik Hata -> {e}"); return None

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    try:
        bot = telegram.Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML')
        print("✅ Telegram bildirimi başarıyla gönderildi.")
    except Exception as e:
        print(f"❌ HATA (Telegram): {e}")

# ... (ANA DÖNGÜ aynı kalıyor) ...
async def ana_dongu():
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
                print(f"📰 '{haber['baslik']}' içeriği çekiliyor...")
                haber_icerigi = get_haber_icerigi(haber['link'])
                if not haber_icerigi: print("-> İçerik alınamadı, bu haber atlanıyor."); haberi_kaydet(haber['link']); continue
                print("-> İçerik alındı, Gemini ile analiz ediliyor...")
                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'], haber_icerigi)
                if analiz_sonucu == "KOTA_ASILDI": print("Gemini kotası dolduğu için bu döngü durduruluyor."); break
                if analiz_sonucu and analiz_sonucu.get('kripto_ile_ilgili_mi') and analiz_sonucu.get('onem_derecisi') in ['Yüksek', 'Çok Yüksek']:
                    print(f"🔥 ÖNEMLİ HABER! ({analiz_sonucu.get('onem_derecisi')}) Teknik analiz yapılıyor...")
                    teknik_analiz_mesaji = ""
                    etkilenen_coinler = analiz_sonucu.get('etkilenen_coinler', [])
                    if etkilenen_coinler:
                        teknik_analiz_mesaji += "<b>Teknik Analiz (4s RSI):</b>\n"
                        for coin in etkilenen_coinler:
                            rsi_degeri = get_teknik_analiz(coin, binance_client)
                            if rsi_degeri: teknik_analiz_mesaji += f" - <b>{coin.upper()}/USDT:</b> {rsi_degeri}\n"
                    coinler_str = ", ".join(etkilenen_coinler)
                    mesaj = (f"🚨 <b>{analiz_sonucu.get('onem_derecisi').upper()} ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n<b>Başlık:</b> {haber['baslik']}\n<b>Kaynak:</b> {haber['kaynak']}\n\n<b>Duygu:</b> {analiz_sonucu.get('duygu')}\n<b>Etkilenen Coinler:</b> {coinler_str}\n\n{teknik_analiz_mesaji}\n<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr')}</i>\n\n<a href='{haber['link']}'>Habere Git</a>")
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                haberi_kaydet(haber['link']); time.sleep(5)
            print(f"Bu döngüde {yeni_haber_sayisi} yeni haber analiz edildi.")
        bekleme_suresi = 1800; print(f"--- Döngü tamamlandı. {bekleme_suresi / 60} dakika bekleniyor... ---"); await asyncio.sleep(bekleme_suresi)

if __name__ == "__main__":
    asyncio.run(ana_dongu())