import os
import json
import time
import telegram
import asyncio
from newsapi import NewsApiClient
import google.generativeai as genai
from binance.client import Client
import pandas as pd
import pandas_ta as ta

# --- API ANAHTARLARI (Ortam Değişkenlerinden okunacak) ---
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY')
BINANCE_SECRET_KEY = os.environ.get('BINANCE_SECRET_KEY')

# --- FONKSİYONLAR ---

# haberleri_cek ve haberleri_analiz_et fonksiyonları DEĞİŞMEDİ, aynı kalıyor
def haberleri_cek(api_key):
    # ... Bu fonksiyonun içeriği öncekiyle aynı ...
    try:
        newsapi = NewsApiClient(api_key=api_key)
        all_articles = newsapi.get_everything(
            q='bitcoin OR ethereum OR blockchain OR crypto OR solana OR ripple OR binance OR kripto',
            sort_by='publishedAt', page_size=25)
        haber_listesi = []
        if all_articles['status'] == 'ok':
            for article in all_articles['articles']:
                haber_listesi.append({
                    'baslik': article['title'], 'link': article['url'], 'kaynak': article['source']['name']
                })
        return haber_listesi
    except Exception as e:
        print(f"HATA (NewsAPI): {e}")
        return None

def haberleri_analiz_et(api_key, haber_basligi):
    # ... Bu fonksiyonun içeriği öncekiyle aynı ...
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        prompt = f"""
        Bir kripto para piyasa analisti gibi davran. Aşağıdaki haber başlığını analiz et ve çıktını SADECE JSON formatında ver.
        Haber Başlığı: "{haber_basligi}"
        JSON formatı şu anahtarlara sahip olmalı: "kripto_ile_ilgili_mi": boolean, "onem_derecesi": string ('Düşük', 'Orta', 'Yüksek'), "etkilenen_coinler": string array, "duygu": string ('Pozitif', 'Negatif', 'Nötr'), "ozet_tr": string
        JSON Cevabı:
        """
        response = model.generate_content(prompt)
        json_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(json_text)
    except Exception as e:
        print(f"HATA (Gemini AI): {e}")
        return None

# YENİ FONKSİYON: Teknik Analiz (RSI)
def get_teknik_analiz(coin_sembolu, binance_client):
    try:
        # Binance'te işlem gören parite adını oluşturuyoruz (örn: BTCUSDT)
        parite = f"{coin_sembolu.upper()}USDT"
        
        # 4 saatlik mum verilerini çekiyoruz (son 100 mum yeterli)
        mumlar = binance_client.get_historical_klines(parite, Client.KLINE_INTERVAL_4HOUR, "1 day ago UTC")
        
        # Gelen veriyi bir pandas DataFrame'e çeviriyoruz
        df = pd.DataFrame(mumlar, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['close'] = pd.to_numeric(df['close']) # Kapanış fiyatlarını sayısal yap

        # RSI hesapla (standart periyot 14'tür)
        df.ta.rsi(length=14, append=True)
        
        # En son (güncel) RSI değerini al
        son_rsi = df['RSI_14'].iloc[-1]
        
        rsi_yorumu = ""
        if son_rsi > 70:
            rsi_yorumu = "Aşırı Alım Bölgesi 📈"
        elif son_rsi < 30:
            rsi_yorumu = "Aşırı Satım Bölgesi 📉"
        else:
            rsi_yorumu = "Nötr Bölge 📊"
            
        return f"{son_rsi:.2f} ({rsi_yorumu})"

    except Exception as e:
        print(f"HATA (Teknik Analiz - {coin_sembolu}): {e}")
        return None

# telegrama_bildirim_gonder fonksiyonu DEĞİŞMEDİ, aynı kalıyor
async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    # ... Bu fonksiyonun içeriği öncekiyle aynı ...
    try:
        bot = telegram.Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML')
        print("✅ Telegram bildirimi başarıyla gönderildi.")
    except Exception as e:
        print(f"❌ HATA (Telegram): {e}")

# --- ANA DÖNGÜ (GÜNCELLENDİ) ---
async def ana_dongu():
    if not all([NEWSAPI_KEY, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BINANCE_API_KEY, BINANCE_SECRET_KEY]):
        print("HATA: Lütfen tüm Ortam Değişkenlerini (Environment Variables) ayarlayın.")
        return
        
    # Binance istemcisini başlat
    binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY)

    while True:
        print(f"\n--- {time.ctime()} --- Döngü başlıyor ---")
        cekilen_haberler = haberleri_cek(NEWSAPI_KEY)
        
        if cekilen_haberler:
            for haber in cekilen_haberler:
                print(f"📰 Haber: {haber['baslik']}")
                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'])
                
                if analiz_sonucu and analiz_sonucu.get('kripto_ile_ilgili_mi') and analiz_sonucu.get('onem_derecesi') == 'Yüksek':
                    print("🔥 ÖNEMLİ HABER! Teknik analiz yapılıyor...")
                    
                    teknik_analiz_mesaji = ""
                    etkilenen_coinler = analiz_sonucu.get('etkilenen_coinler', [])
                    if etkilenen_coinler:
                        teknik_analiz_mesaji += "<b>Teknik Analiz (4s RSI):</b>\n"
                        for coin in etkilenen_coinler:
                            rsi_degeri = get_teknik_analiz(coin, binance_client)
                            if rsi_degeri:
                                teknik_analiz_mesaji += f" - <b>{coin.upper()}/USDT:</b> {rsi_degeri}\n"
                    
                    coinler_str = ", ".join(etkilenen_coinler)
                    mesaj = (
                        f"🚨 <b>YÜKSEK ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n"
                        f"<b>Başlık:</b> {haber['baslik']}\n"
                        f"<b>Kaynak:</b> {haber['kaynak']}\n\n"
                        f"<b>Duygu:</b> {analiz_sonucu.get('duygu')}\n"
                        f"<b>Etkilenen Coinler:</b> {coinler_str}\n\n"
                        f"{teknik_analiz_mesaji}\n"
                        f"<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr')}</i>\n\n"
                        f"<a href='{haber['link']}'>Habere Git</a>"
                    )
                    
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                
                time.sleep(2)
        
        bekleme_suresi = 1800
        print(f"--- Döngü tamamlandı. {bekleme_suresi / 60} dakika bekleniyor... ---")
        await asyncio.sleep(bekleme_suresi)

if __name__ == "__main__":
    asyncio.run(ana_dongu())