# newsapi = 7060a2ea8f714bc4b8f2b28b10d83765
# geminikey = AIzaSyBB9GKC6KrX1Ibw91yTGmR94g6cAF5zhW8
# pip install google-generativeai / NewsApiClient /

from newsapi import NewsApiClient

from newsapi import NewsApiClient

def haberleri_cek(api_key):
    """
    NewsAPI kullanarak kripto para ile ilgili en son haberleri çeker.
    Dil filtresi, API'ın desteklememesi nedeniyle kaldırılmıştır.
    """
    try:
        # NewsAPI istemcisini kendi API anahtarımızla başlatıyoruz
        newsapi = NewsApiClient(api_key=api_key)

        print("1. Adım: NewsAPI'a istek gönderiliyor (tüm dillerde)...")

        # Arama sorgumuzu hazırlıyoruz.
        # 'language' parametresini hata verdiği için kaldırdık.
        # Bu sayede arama tüm dillerde yapılacaktır.
        all_articles = newsapi.get_everything(
            q='bitcoin OR ethereum OR blockchain OR crypto OR solana OR ripple OR binance OR kripto',
            sort_by='publishedAt',
            page_size=25  # Son 25 haberi alalım
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
            print(f"Hata: API'dan 'ok' durumu alınamadı. Gelen mesaj: {all_articles.get('message')}")
            return None

    except Exception as e:
        # Hatanın daha detaylı görülmesi için tipini de yazdıralım
        print(f"HATA: Beklenmedik bir hata oluştu: {type(e).__name__} - {e}")
        return None

# --- ANA PROGRAM ---
if __name__ == "__main__":
    
    # LÜTFEN newsapi.org'dan ALDIĞINIZ KENDİ API ANAHTARINIZI BURAYA GİRİN
    MY_API_KEY = "7060a2ea8f714bc4b8f2b28b10d83765"

    if MY_API_KEY == "BURAYA_API_ANAHTARINIZI_YAPISTIRIN":
        print("Lütfen kodun içindeki MY_API_KEY değişkenine kendi NewsAPI anahtarınızı girin.")
    else:
        cekilen_haberler = haberleri_cek(MY_API_KEY)

        if cekilen_haberler:
            print(f"\n--- Bulunan Haberler ({len(cekilen_haberler)} adet) ---\n")
            for haber in cekilen_haberler:
                print(f"Başlık: {haber['baslik']}")
                print(f"Kaynak: {haber['kaynak']}")
                print(f"Link: {haber['link']}\n")
        else:
            print("\nİşlem tamamlandı fakat gösterilecek haber bulunamadı.")