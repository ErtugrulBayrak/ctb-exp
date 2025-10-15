import os
import json
import time
import telegram
from newsapi import NewsApiClient
import google.generativeai as genai
import asyncio

# --- API ANAHTARLARI ---
# Render üzerinde bu anahtarları "Environment Variables" (Ortam Değişkenleri) olarak ayarlayacağız.
# Bu, anahtarlarımızı kodun içinde açıkça yazmaktan çok daha güvenlidir.
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# --- FONKSİYONLAR (DEĞİŞİKLİK YOK) ---
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

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    # ... Bu fonksiyonun içeriği öncekiyle aynı ...
    try:
        bot = telegram.Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML')
        print("✅ Telegram bildirimi başarıyla gönderildi.")
    except Exception as e:
        print(f"❌ HATA (Telegram): {e}")

# --- ANA DÖNGÜ ---
async def ana_dongu():
    if not all([NEWSAPI_KEY, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("HATA: Lütfen tüm Ortam Değişkenlerini (Environment Variables) ayarlayın.")
        return

    while True:
        print(f"\n--- {time.ctime()} --- Döngü başlıyor ---")
        cekilen_haberler = haberleri_cek(NEWSAPI_KEY)
        
        if cekilen_haberler:
            for haber in cekilen_haberler:
                # Önemli not: Sunucuda tekrar tekrar aynı haberi göndermemek için
                # normalde bir veritabanı kullanıp haber linkini kontrol etmek gerekir.
                # Şimdilik bu basit haliyle devam ediyoruz.
                print(f"📰 Haber: {haber['baslik']}")
                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'])
                
                if analiz_sonucu and analiz_sonucu.get('kripto_ile_ilgili_mi') and analiz_sonucu.get('onem_derecesi') == 'Yüksek':
                    print("🔥 ÖNEMLİ HABER! Telegram'a gönderiliyor...")
                    coinler = ", ".join(analiz_sonucu.get('etkilenen_coinler', []))
                    mesaj = (f"🚨 <b>YÜKSEK ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n<b>Başlık:</b> {haber['baslik']}\n<b>Kaynak:</b> {haber['kaynak']}\n\n<b>Duygu:</b> {analiz_sonucu.get('duygu')}\n<b>Etkilenen Coinler:</b> {coinler}\n\n<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr')}</i>\n\n<a href='{haber['link']}'>Habere Git</a>")
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                
                time.sleep(2) # API limitleri için küçük bir bekleme
        
        bekleme_suresi = 1800 # 30 dakika
        print(f"--- Döngü tamamlandı. {bekleme_suresi / 60} dakika bekleniyor... ---")
        await asyncio.sleep(bekleme_suresi)

if __name__ == "__main__":
    asyncio.run(ana_dongu())