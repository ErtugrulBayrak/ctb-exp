# newsapi = 7060a2ea8f714bc4b8f2b28b10d83765
# geminikey = AIzaSyBB9GKC6KrX1Ibw91yTGmR94g6cAF5zhW8
# pip install google-generativeai / NewsApiClient / python-telegram-bot

import os
import json
import time
from newsapi import NewsApiClient
import google.generativeai as genai

# --- 1. ADIM: HABERLERİ ÇEKME FONKSİYONU ---
def haberleri_cek(api_key):
    try:
        newsapi = NewsApiClient(api_key=api_key)
        print("1. Adım: NewsAPI'a istek gönderiliyor...")
        all_articles = newsapi.get_everything(
            q='bitcoin OR ethereum OR blockchain OR crypto OR solana OR ripple OR binance OR kripto',
            sort_by='publishedAt',
            page_size=5  # Test aşamasında hızlı olması için sayıyı 5'e düşürelim
        )
        print("2. Adım: İstek başarılı, haberler alınıyor...")
        haber_listesi = []
        if all_articles['status'] == 'ok':
            for article in all_articles['articles']:
                haber_listesi.append({
                    'baslik': article['title'],
                    'link': article['url'],
                    'kaynak': article['source']['name']
                })
            print(f"3. Adım: {len(haber_listesi)} adet haber başarıyla alındı.")
            return haber_listesi
        else:
            print(f"Hata: API'dan 'ok' durumu alınamadı.")
            return None
    except Exception as e:
        print(f"HATA (NewsAPI): {type(e).__name__} - {e}")
        return None

# --- 2. ADIM: YAPAY ZEKA İLE ANALİZ FONKSİYONU ---
def haberleri_analiz_et(api_key, haber_basligi):
    """
    Verilen bir haber başlığını Gemini AI ile analiz eder ve yapılandırılmış bir
    JSON çıktısı döndürür.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')

        # Gemini'ye göndereceğimiz komut (prompt)
        prompt = f"""
        Bir kripto para piyasa analisti gibi davran. Aşağıdaki haber başlığını analiz et ve çıktını SADECE JSON formatında ver.

        Haber Başlığı: "{haber_basligi}"

        İstediğim JSON formatı şu anahtarlara sahip olmalı:
        - "kripto_ile_ilgili_mi": boolean (true/false). Bu haber doğrudan kripto, blockchain veya web3 ile ilgili mi?
        - "onem_derecesi": string ('Düşük', 'Orta', 'Yüksek'). Haberin piyasa için önemi ne kadar?
        - "etkilenen_coinler": string array (["BTC", "ETH"]). En çok etkilenen kripto paraların sembollerini listele. Yoksa boş liste [] döndür.
        - "duygu": string ('Pozitif', 'Negatif', 'Nötr'). Haberin piyasa için genel duygu tonu nedir?
        - "ozet_tr": string. Haberin ne hakkında olduğunu tek ve kısa bir cümleyle Türkçe özetle.

        JSON Cevabı:
        """

        response = model.generate_content(prompt)
        
        # Gemini'den gelen yanıtın içindeki text'i alıp JSON'a çeviriyoruz
        # Bazen AI, JSON'ı ```json ... ``` bloğu içine koyabilir, bunu temizleyelim.
        json_text = response.text.strip().replace('```json', '').replace('```', '')
        analiz = json.loads(json_text)
        return analiz

    except Exception as e:
        print(f"HATA (Gemini AI): {type(e).__name__} - {e}")
        return None

# --- ANA PROGRAM ---
if __name__ == "__main__":
    
    # LÜTFEN API ANAHTARLARINIZI AŞAĞIYA GİRİN
    NEWSAPI_KEY = "7060a2ea8f714bc4b8f2b28b10d83765"
    GEMINI_API_KEY = "AIzaSyBB9GKC6KrX1Ibw91yTGmR94g6cAF5zhW8"

    if "BURAYA" in NEWSAPI_KEY or "BURAYA" in GEMINI_API_KEY:
        print("Lütfen kodun içindeki NEWSAPI_KEY ve GEMINI_API_KEY değişkenlerine kendi anahtarlarınızı girin.")
    else:
        # 1. Haberleri Çek
        cekilen_haberler = haberleri_cek(NEWSAPI_KEY)

        # 2. Haberleri Analiz Et
        if cekilen_haberler:
            print("\n--- HABER ANALİZİ BAŞLIYOR ---\n")
            for haber in cekilen_haberler:
                print(f"📰 Haber Başlığı: {haber['baslik']}")
                print(f"🔗 Kaynak: {haber['kaynak']}")
                
                # Gemini'ye gönderip analizi alıyoruz
                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'])
                
                if analiz_sonucu:
                    print("🧠 Gemini Analizi:")
                    # Analiz sonucunu daha okunaklı bir formatta yazdıralım
                    print(json.dumps(analiz_sonucu, indent=2, ensure_ascii=False))
                
                print("-" * 30)
                # API hız limitlerine takılmamak için her istek arasında 1 saniye bekle
                time.sleep(1)