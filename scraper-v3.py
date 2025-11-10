# newsapi = 7060a2ea8f714bc4b8f2b28b10d83765
# geminikey = AIzaSyBB9GKC6KrX1Ibw91yTGmR94g6cAF5zhW8
# telegrambot api = 8420610160:AAH0AsElcbB7DH66BmzRP_hg1z1b0Uz8z_o
# my telegram id = 7965892622
# pip install google-generativeai / NewsApiClient / python-telegram-bot

import os
import json
import time
import telegram
from newsapi import NewsApiClient
import google.generativeai as genai

# --- API ANAHTARLARI ---
NEWSAPI_KEY = "7060a2ea8f714bc4b8f2b28b10d83765"
GEMINI_API_KEY = "AIzaSyBB9GKC6KrX1Ibw91yTGmR94g6cAF5zhW8"
TELEGRAM_BOT_TOKEN = "8420610160:AAH0AsElcbB7DH66BmzRP_hg1z1b0Uz8z_o"
TELEGRAM_CHAT_ID = "7965892622"

# --- 1. ADIM: HABERLERİ ÇEKME FONKSİYONU ---
def haberleri_cek(api_key):
    try:
        newsapi = NewsApiClient(api_key=api_key)
        print("1. Adım: NewsAPI'a istek gönderiliyor...")
        all_articles = newsapi.get_everything(
            q='bitcoin OR ethereum OR blockchain OR crypto OR solana OR ripple OR binance OR kripto',
            sort_by='publishedAt',
            page_size=10
        )
        print(f"2. Adım: {len(all_articles['articles'])} adet haber başarıyla alındı.")
        haber_listesi = []
        if all_articles['status'] == 'ok':
            for article in all_articles['articles']:
                haber_listesi.append({
                    'baslik': article['title'],
                    'link': article['url'],
                    'kaynak': article['source']['name']
                })
            return haber_listesi
    except Exception as e:
        print(f"HATA (NewsAPI): {e}")
        return None

# --- 2. ADIM: YAPAY ZEKA İLE ANALİZ FONKSİYONU ---
def haberleri_analiz_et(api_key, haber_basligi):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        prompt = f"""
        Bir kripto para piyasa analisti gibi davran. Aşağıdaki haber başlığını analiz et ve çıktını SADECE JSON formatında ver.
        Haber Başlığı: "{haber_basligi}"
        JSON formatı şu anahtarlara sahip olmalı:
        - "kripto_ile_ilgili_mi": boolean
        - "onem_derecesi": string ('Düşük', 'Orta', 'Yüksek')
        - "etkilenen_coinler": string array (["BTC", "ETH"])
        - "duygu": string ('Pozitif', 'Negatif', 'Nötr')
        - "ozet_tr": string
        JSON Cevabı:
        """
        response = model.generate_content(prompt)
        json_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(json_text)
    except Exception as e:
        print(f"HATA (Gemini AI): {e}")
        return None

# --- 3. ADIM: TELEGRAM'A BİLDİRİM GÖNDERME FONKSİYONU ---
async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    try:
        bot = telegram.Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML')
        print("✅ Telegram bildirimi başarıyla gönderildi.")
    except Exception as e:
        print(f"❌ HATA (Telegram): {e}")

# --- ANA PROGRAM ---
import asyncio

async def main():
    if "BURAYA" in NEWSAPI_KEY or "BURAYA" in GEMINI_API_KEY or "BURAYA" in TELEGRAM_BOT_TOKEN or "BURAYA" in TELEGRAM_CHAT_ID:
        print("Lütfen kodun en üstündeki tüm API anahtarlarını ve ID'leri doldurun.")
        return

    # 1. Haberleri Çek
    cekilen_haberler = haberleri_cek(NEWSAPI_KEY)

    # 2. Haberleri Analiz Et ve Bildirim Gönder
    if cekilen_haberler:
        print("\n--- HABER ANALİZİ BAŞLIYOR ---\n")
        for haber in cekilen_haberler:
            print(f"📰 Haber: {haber['baslik']}")
            analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'])
            
            if analiz_sonucu and analiz_sonucu.get('kripto_ile_ilgili_mi'):
                print(f"🧠 Gemini Analizi: Önem derecesi '{analiz_sonucu.get('onem_derecesi')}' olarak bulundu.")
                
                # SADECE ÖNEM DERECESİ 'Yüksek' OLANLARI BİLDİR
                if analiz_sonucu.get('onem_derecesi') == 'Yüksek':
                    print("🔥 ÖNEMLİ HABER! Telegram'a gönderiliyor...")
                    
                    # Telegram mesajını formatla
                    coinler = ", ".join(analiz_sonucu.get('etkilenen_coinler', []))
                    mesaj = (
                        f"🚨 <b>YÜKSEK ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n"
                        f"<b>Başlık:</b> {haber['baslik']}\n"
                        f"<b>Kaynak:</b> {haber['kaynak']}\n\n"
                        f"<b>Duygu:</b> {analiz_sonucu.get('duygu')}\n"
                        f"<b>Etkilenen Coinler:</b> {coinler}\n\n"
                        f"<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr')}</i>\n\n"
                        f"<a href='{haber['link']}'>Habere Git</a>"
                    )
                    
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
            
            print("-" * 30)
            time.sleep(1)

if __name__ == "__main__":
    # async fonksiyonları çalıştırmak için asyncio kullanılır
    asyncio.run(main())