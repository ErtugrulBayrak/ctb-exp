import os
import json
import time
import telegram
import asyncio
import re
from newsapi import NewsApiClient
import google.generativeai as genai
from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import pandas_ta as ta
from newspaper import Article, Config
import praw
import requests # Binance Client için gerekli olabilir, ekleyelim

# --- API ANAHTARLARI ---
# Sunucudaki Ortam Değişkenlerinden okunacak
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY')
BINANCE_SECRET_KEY = os.environ.get('BINANCE_SECRET_KEY')
REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET')
REDDIT_USER_AGENT = os.environ.get('REDDIT_USER_AGENT', 'KriptoAnalizBotu v1.0 by DefaultUser') # Default eklendi
REDDIT_USERNAME = os.environ.get('REDDIT_USERNAME')
REDDIT_PASSWORD = os.environ.get('REDDIT_PASSWORD')

# --- Veritabanı Dosyası ---
ISLENMIS_HABERLER_DOSYASI = "islenmis_haberler.txt"

# --- TEMEL YARDIMCI FONKSİYONLAR ---

def islenmis_haberleri_yukle():
    """Daha önce işlenen haber linklerini dosyadan okur."""
    if not os.path.exists(ISLENMIS_HABERLER_DOSYASI):
        return set()
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"HATA (Veritabanı Okuma): {e}")
        return set()

def haberi_kaydet(haber_linki):
    """İşlenen haber linkini dosyaya ekler."""
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'a', encoding='utf-8') as f:
            f.write(haber_linki + '\n')
    except Exception as e:
        print(f"HATA (Veritabanı Yazma): {e}")

def haber_basligi_uygun_mu(baslik):
    """Haber başlığının kripto ile ilgili olup olmadığını basit anahtar kelimelerle kontrol eder."""
    anahtar_kelimeler = ['bitcoin', 'ethereum', 'crypto', 'blockchain', 'binance', 'solana', 'ripple', 'kripto', 'coin', 'token', 'web3', 'nft', 'etf', 'defi', 'metaverse', 'mining', 'staking', 'airdrop']
    baslik_kucuk_harf = baslik.lower()
    return any(kelime in baslik_kucuk_harf for kelime in anahtar_kelimeler)

def extract_json_from_text(text):
    """ Verilen metin içindeki ilk geçerli JSON bloğunu bulur ve döndürür. """
    if not text: return None
    # Hem ```json ... ``` hem de doğrudan { ... } formatını arayalım
    match = re.search(r"```json\s*(\{.*?\})\s*```|(\{.*\})", text, re.DOTALL)
    if match:
        # İlk grup ```json``` içindekini, ikinci grup doğrudan { } içindekini yakalar
        json_part = match.group(1) or match.group(2)
        try:
            return json.loads(json_part)
        except json.JSONDecodeError as e:
            print(f"HATA (JSON Ayıklama): Ayıklanan metin JSON'a çevrilemedi. Hata: {e}")
            print(f"Ayıklanan Kısım: '{json_part[:200]}...'") # Sadece başını yazdır
            return None
    return None

# --- VERİ ÇEKME VE ANALİZ FONKSİYONLARI ---

def haberleri_cek(api_key):
    """NewsAPI kullanarak en son kripto haberlerini çeker."""
    if not api_key: print("HATA (NewsAPI): API anahtarı eksik."); return []
    try:
        newsapi = NewsApiClient(api_key=api_key)
        # Daha odaklı arama: Sadece kripto terimleri + belki 'technology'
        all_articles = newsapi.get_everything(
            q='(bitcoin OR ethereum OR crypto OR blockchain OR web3 OR cryptocurrency) AND NOT (politics OR sports)', # İstenmeyenleri filtrele
            language='en', # Sadece İngilizce haberler daha tutarlı sonuç verebilir
            sort_by='publishedAt',
            page_size=50 # Başlangıçta daha fazla çekip filtreleyelim
        )
        if all_articles['status'] == 'ok':
            return [{'baslik': a['title'], 'link': a['url'], 'kaynak': a['source']['name']}
                    for a in all_articles.get('articles', []) if a['title'] and '[Removed]' not in a['title']] # Başlığı olmayan veya silinmiş haberleri atla
        else:
            print(f"HATA (NewsAPI): API'dan 'ok' durumu alınamadı. Mesaj: {all_articles.get('message')}")
            return []
    except Exception as e:
        print(f"HATA (NewsAPI): Beklenmedik Hata -> {e}"); return []

def get_haber_icerigi(url):
    """Verilen URL'deki haber makalesinin metnini çeker (newspaper3k ile)."""
    try:
        config = Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
        config.request_timeout = 20
        config.verify_ssl = False # SSL hatalarını es geç (bazı siteler için gerekli)
        config.fetch_images = False # Resimleri indirme, hızlandırır
        config.memoize_articles = False # Önbellekleme yapma

        article = Article(url, config=config)
        article.download()
        article.parse()

        # Metin boşsa veya çok kısaysa başarısız say
        if not article.text or len(article.text) < 100:
             print(f"UYARI (Newspaper3k - {url}): Yeterli içerik bulunamadı veya çıkarılamadı.")
             return None

        # Çok uzun metinleri Gemini'ye göndermeden önce kırp (maliyet ve performans)
        return article.text[:7000] # Limiti biraz artıralım
    except Exception as e:
        print(f"HATA (Newspaper3k - {url}): İçerik çekilemedi. Sebep: {e}"); return None

def haberleri_analiz_et(api_key, haber_basligi, haber_icerigi):
    """Haber başlığını ve içeriğini Gemini ile analiz eder."""
    if not api_key: print("HATA (Gemini AI): API anahtarı eksik."); return None
    try:
        genai.configure(api_key=api_key)
        safety_settings = [ # Güvenlik filtrelerini minimuma indir
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)

        # En son optimize edilmiş, katı prompt
        prompt = f"""
        GÖREV: Aşağıdaki haber başlığını ve metnini analiz et. Çıktın SADECE ve SADECE geçerli bir JSON objesi olmalı. Başka HİÇBİR metin, açıklama veya formatlama ekleme (```json bloğu KULLANMA).

        Haber Başlığı: "{haber_basligi}"
        Haber Metni: "{haber_icerigi}"

        İstenen JSON Yapısı (ANAHTARLAR VE DEĞER TİPLERİ KESİN OLMALI):
        {{
          "kripto_ile_ilgili_mi": boolean,
          "onem_derecisi": string ('Düşük', 'Orta', 'Yüksek', 'Çok Yüksek'),
          "etkilenen_coinler": array[string] (SADECE Binance ticker sembolleri, örn: ["BTC", "ETH"]),
          "duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string (1-2 cümlelik detaylı Türkçe özet)
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        # Yanıtı kontrol et ve JSON'u ayıkla
        if not response.parts:
            feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'Geri bildirim yok'
            print(f"HATA (Gemini AI): Yanıt alınamadı. Muhtemel güvenlik engeli. Geri bildirim: {feedback}"); return None

        raw_text = response.text.strip()
        analiz = extract_json_from_text(raw_text)

        if analiz:
            # Temel anahtarların varlığını kontrol edelim
            required_keys = ["kripto_ile_ilgili_mi", "onem_derecisi", "etkilenen_coinler", "duygu", "ozet_tr"]
            if all(key in analiz for key in required_keys):
                return analiz
            else:
                 print(f"HATA (Gemini AI): JSON eksik anahtarlar içeriyor.")
                 print(f"Alınan JSON: {analiz}")
                 return None
        else:
            print(f"HATA (Gemini AI): Yanıttan geçerli JSON ayıklanamadı.")
            print(f"Alınan Ham Metin:\n---\n{raw_text[:500]}...\n---") # Sadece başını yazdır
            return None

    except Exception as e:
        print(f"HATA (Gemini AI): Beklenmedik Hata -> {e}"); return None

def get_teknik_analiz(coin_sembolu, binance_client):
    """Verilen coin sembolü için Binance'ten 4s RSI değerini hesaplar."""
    if not binance_client: return None # Binance client yoksa direkt çık
    if not coin_sembolu or not isinstance(coin_sembolu, str): return None # Geçersiz sembolse çık

    try:
        parite = f"{coin_sembolu.upper()}USDT"
        # Daha fazla veri çekelim, pandas_ta bazen daha fazlasına ihtiyaç duyabilir
        mumlar = binance_client.get_historical_klines(parite, Client.KLINE_INTERVAL_4HOUR, "4 days ago UTC")

        if len(mumlar) < 20: # Gerekli mum sayısını biraz artıralım
            print(f"UYARI (Teknik Analiz - {parite}): RSI için yeterli veri yok (Mum sayısı: {len(mumlar)})."); return None

        df = pd.DataFrame(mumlar, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['close'] = pd.to_numeric(df['close'])

        # RSI hesapla ve sonucu kontrol et
        rsi_series = df.ta.rsi(length=14) # Ayrı bir seri olarak al
        if rsi_series is None or rsi_series.dropna().empty:
            print(f"UYARI (Teknik Analiz - {parite}): RSI değeri hesaplanamadı (pandas_ta None döndürdü)."); return None

        son_rsi = rsi_series.iloc[-1]
        if pd.isna(son_rsi): # Son değer NaN ise hesaplanamamıştır
             print(f"UYARI (Teknik Analiz - {parite}): Son RSI değeri hesaplanamadı (NaN)."); return None

        # RSI yorumunu belirle
        rsi_yorumu = "Aşırı Alım 📈" if son_rsi > 70 else "Aşırı Satım 📉" if son_rsi < 30 else "Nötr 📊"
        return f"{son_rsi:.2f} ({rsi_yorumu})"

    except BinanceAPIException as e:
        if e.code == -1121: # Geçersiz sembol hatası
            print(f"UYARI (Teknik Analiz): {parite} paritesi Binance'te bulunamadı."); return None
        else: # Diğer Binance API hataları
            print(f"HATA (Binance API - {coin_sembolu}): {e}"); return None
    except Exception as e: # Diğer tüm hatalar (pandas, vs.)
        print(f"HATA (Teknik Analiz - {coin_sembolu}): Beklenmedik Hata -> {e}"); return None

def get_reddit_sentiment(gemini_api_key):
    """Reddit r/CryptoCurrency'den başlıkları çeker ve Gemini ile duyarlılığı analiz eder."""
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, REDDIT_USERNAME, REDDIT_PASSWORD]):
        print("UYARI (Reddit): API bilgileri eksik, bu adım atlanıyor."); return None
    if not gemini_api_key: print("HATA (Reddit/Gemini): Gemini API anahtarı eksik."); return None

    try:
        print("📊 Reddit duyarlılık analizi başlıyor...")
        # PRAW'ı read_only modunda kullanmak şifre gerektirmez ve daha güvenli olabilir
        # Ancak bazı sublara erişim için giriş yapmak gerekebilir, şimdilik böyle kalsın.
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID, client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT, username=REDDIT_USERNAME, password=REDDIT_PASSWORD,
            # read_only=True # Eğer şifresiz kullanmak isterseniz
        )
        # Bağlantıyı test edelim (isteğe bağlı ama faydalı)
        reddit.user.me()
        print("-> Reddit'e başarıyla bağlanıldı.")

        subreddit = reddit.subreddit("CryptoCurrency")
        limit = 30 # Biraz daha fazla başlık alalım

        metin_blogu = ""
        try:
            for submission in subreddit.hot(limit=limit):
                metin_blogu += submission.title + ". "
        except Exception as praw_e:
             print(f"HATA (PRAW): Subreddit verisi çekilemedi. {praw_e}"); return None

        if not metin_blogu: print("-> Reddit'ten çekilecek başlık bulunamadı."); return None
        print(f"-> r/CryptoCurrency'den {limit} başlık metni alındı, Gemini'ye gönderiliyor...")

        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash') # Reddit için güvenlik ayarları genelde gerekmez

        # En son optimize edilmiş, katı prompt
        prompt = f"""
        GÖREV: Aşağıdaki metin bloğunu analiz et. Çıktın SADECE ve SADECE geçerli bir JSON objesi olmalı. Başka HİÇBİR metin, açıklama veya formatlama ekleme (```json bloğu KULLANMA).

        Metin Bloğu: "{metin_blogu[:6000]}" # Limiti biraz artıralım

        İstenen JSON Yapısı (ANAHTARLAR VE DEĞER TİPLERİ KESİN OLMALI):
        {{
          "genel_duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string (Genel duygu ve öne çıkan 1-2 konuyu içeren TEK cümlelik Türkçe özet.)
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        # Yanıtı kontrol et ve JSON'u ayıkla
        if not response.parts:
            feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'Geri bildirim yok'
            print(f"HATA (Gemini/Reddit): Yanıt alınamadı. Geri bildirim: {feedback}"); return None

        raw_text = response.text.strip()
        analiz = extract_json_from_text(raw_text)

        if analiz and "genel_duygu" in analiz and "ozet_tr" in analiz:
            print("-> Reddit duyarlılık analizi tamamlandı.")
            return analiz
        else:
            print(f"HATA (Gemini/Reddit): Yanıttan geçerli veya tam JSON ayıklanamadı.")
            print(f"Alınan Ham Metin:\n---\n{raw_text[:500]}...\n---")
            return None

    except praw.exceptions.PRAWException as e:
        print(f"HATA (PRAW): Reddit API hatası. {e}"); return None
    except Exception as e:
        print(f"HATA (Reddit/Genel): Beklenmedik Hata -> {e}"); return None

# --- BİLDİRİM FONKSİYONU ---

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    """Telegram'a asenkron olarak mesaj gönderir (Basit versiyon)."""
    if not bot_token or not chat_id: print("HATA (Telegram): Bot token veya Chat ID eksik."); return
    try:
        bot = telegram.Bot(token=bot_token)
        # Mesajın çok uzun olmasını engelle (Telegram limiti ~4096 karakter)
        max_len = 4000
        if len(mesaj) > max_len:
            mesaj = mesaj[:max_len] + "\n\n...(Mesaj kısaltıldı)..."

        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML', disable_web_page_preview=True) # Link önizlemesini kapatalım
        print("✅ Telegram bildirimi başarıyla gönderildi.")
    except telegram.error.TelegramError as e:
        print(f"❌ HATA (Telegram API): {e}")
    except Exception as e:
        print(f"❌ HATA (Telegram/Genel): Beklenmedik Hata -> {e}")


# --- ANA İŞ AKIŞI DÖNGÜSÜ ---

async def ana_dongu():
    """Ana iş akışını yöneten asenkron fonksiyon."""
    # API Anahtarlarının varlığını başta bir kere kontrol et
    gerekli_anahtarlar = {
        'NewsAPI': NEWSAPI_KEY, 'Gemini': GEMINI_API_KEY, 'Telegram Bot': TELEGRAM_BOT_TOKEN,
        'Telegram Chat': TELEGRAM_CHAT_ID, 'Binance API': BINANCE_API_KEY, 'Binance Secret': BINANCE_SECRET_KEY,
        'Reddit Client ID': REDDIT_CLIENT_ID, 'Reddit Secret': REDDIT_CLIENT_SECRET, 'Reddit User Agent': REDDIT_USER_AGENT,
        'Reddit Username': REDDIT_USERNAME, 'Reddit Password': REDDIT_PASSWORD
    }
    eksik_anahtarlar = [isim for isim, deger in gerekli_anahtarlar.items() if not deger]
    if eksik_anahtarlar:
        print(f"UYARI: Şu ortam değişkenleri ayarlı değil: {', '.join(eksik_anahtarlar)}. İlgili adımlar atlanabilir.")

    # Binance istemcisini başlat (hata kontrolüyle)
    binance_client = None
    if BINANCE_API_KEY and BINANCE_SECRET_KEY:
        try:
            binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY, requests_params={'timeout': 20}) # Timeout ekleyelim
            binance_client.ping() # Bağlantıyı test et
            print("✅ Binance API istemcisi başarıyla başlatıldı ve bağlantı test edildi.")
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
             print("❌ HATA (Binance Client): Bağlantı zaman aşımına uğradı. Ağ veya Güvenlik Duvarı ayarlarını kontrol edin.")
        except BinanceAPIException as e:
             print(f"❌ HATA (Binance Client): API hatası - {e}")
        except Exception as e:
            print(f"❌ HATA (Binance Client): İstemci başlatılırken beklenmedik hata: {e}")
    else:
        print("UYARI: Binance API anahtarları eksik, teknik analiz adımı atlanacak.")

    # Ana Sonsuz Döngü
    while True:
        print(f"\n--- {time.ctime()} --- Döngü başlıyor ---")
        dongu_baslangic_zamani = time.time()

        # Adım 1: Reddit Duyarlılığını Al
        reddit_analizi = get_reddit_sentiment(GEMINI_API_KEY)
        reddit_duygu_ozeti_str = "" # Haber mesajına eklenecek metin
        if reddit_analizi:
            print("--- Reddit Genel Durum ---")
            print(json.dumps(reddit_analizi, indent=2, ensure_ascii=False))
            print("-------------------------")
            genel_duygu = reddit_analizi.get('genel_duygu', 'Bilinmiyor')
            reddit_ozet = reddit_analizi.get('ozet_tr', 'Reddit özeti alınamadı.')
            # Mesaja eklenecek formatlı metni hazırla
            reddit_duygu_ozeti_str = f"<b>Anlık Reddit Duyarlılığı (r/CC):</b> {genel_duygu}\n<i>{reddit_ozet}</i>\n\n"
            await asyncio.sleep(2) # API limitleri için küçük bekleme

        # Adım 2: İşlenmiş Haberleri Yükle
        islenmis_haberler = islenmis_haberleri_yukle()
        print(f"{len(islenmis_haberler)} adet haber daha önce işlenmiş.")

        # Adım 3: Yeni Haberleri Çek
        cekilen_haberler = haberleri_cek(NEWSAPI_KEY)
        print(f"{len(cekilen_haberler)} adet haber NewsAPI'dan çekildi.")

        # Adım 4: Yeni Haberleri İşle
        if cekilen_haberler:
            yeni_haber_sayisi = 0
            analiz_edilen_haber_sayisi = 0
            for haber in cekilen_haberler:
                # Haber işlenmişse veya filtreye takılırsa atla
                if haber['link'] in islenmis_haberler or not haber_basligi_uygun_mu(haber['baslik']):
                    if haber['link'] not in islenmis_haberler: haberi_kaydet(haber['link'])
                    continue

                yeni_haber_sayisi += 1
                print(f"--- Haber {yeni_haber_sayisi}/{len(cekilen_haberler) - len(islenmis_haberler)} ---")
                print(f"📰 '{haber['baslik']}' içeriği çekiliyor...")

                # Adım 4a: Haber İçeriğini Çek
                haber_icerigi = get_haber_icerigi(haber['link'])
                if not haber_icerigi:
                    print("-> İçerik alınamadı, bu haber atlanıyor."); haberi_kaydet(haber['link']); continue
                print("-> İçerik alındı, Gemini ile analiz ediliyor...")

                # Adım 4b: Gemini ile Analiz Et
                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'], haber_icerigi)
                analiz_edilen_haber_sayisi += 1

                # Gemini hatası varsa veya kota dolduysa döngüyü kır
                if analiz_sonucu == "KOTA_ASILDI": print("Gemini kotası doldu, döngü sonlandırılıyor."); break
                if not isinstance(analiz_sonucu, dict): # Analiz başarısızsa veya geçersizse atla
                    haberi_kaydet(haber['link']); continue # Başarısız olsa da kaydet

                onem_derecesi = analiz_sonucu.get('onem_derecisi', 'Bulunamadı')
                print(f"-> Gemini Analizi: Önem Derecesi = {onem_derecesi}, Duygu = {analiz_sonucu.get('duygu')}")

                # Adım 4c: Önemliyse Bildirim Hazırla ve Gönder
                if analiz_sonucu.get('kripto_ile_ilgili_mi') and onem_derecesi in ['Yüksek', 'Çok Yüksek']:
                    print(f"🔥 ÖNEMLİ HABER! ({onem_derecesi}) Teknik analiz yapılıyor...")

                    # Adım 4c-i: Teknik Analiz Yap
                    teknik_analiz_mesaji = ""
                    etkilenen_coinler = analiz_sonucu.get('etkilenen_coinler', [])
                    if etkilenen_coinler and binance_client: # Sadece coin varsa ve client hazırsa
                        teknik_analiz_mesaji += "<b>Teknik Analiz (4s RSI):</b>\n"
                        coin_analiz_sayisi = 0
                        for coin in etkilenen_coinler:
                            # Çok fazla coin analizi yapmamak için limit koyalım (ilk 3 coin gibi)
                            if coin_analiz_sayisi >= 3:
                                teknik_analiz_mesaji += " - Diğerleri...\n"
                                break
                            rsi_degeri = get_teknik_analiz(coin, binance_client)
                            if rsi_degeri:
                                teknik_analiz_mesaji += f" - <b>{coin.upper()}/USDT:</b> {rsi_degeri}\n"
                                coin_analiz_sayisi += 1
                            await asyncio.sleep(0.5) # Binance API limitleri için küçük bekleme

                    # Adım 4c-ii: Telegram Mesajını Oluştur
                    coinler_str = ", ".join(etkilenen_coinler) if etkilenen_coinler else "Belirtilmemiş"
                    mesaj = (
                        f"🚨 <b>{onem_derecesi.upper()} ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n"
                        f"<b>Başlık:</b> {haber['baslik']}\n"
                        f"<b>Kaynak:</b> {haber['kaynak']}\n\n"
                        f"{reddit_duygu_ozeti_str}" # Döngü başında alınan Reddit özeti
                        f"<b>Haber Analizi (Gemini):</b>\n"
                        f"- Duygu: {analiz_sonucu.get('duygu', 'N/A')}\n"
                        f"- Etkilenen Coinler: {coinler_str}\n\n"
                        f"{teknik_analiz_mesaji if teknik_analiz_mesaji else ''}" # Eğer teknik analiz yoksa boşluk bırakma
                        f"<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr', 'Özet alınamadı.')}</i>\n\n"
                        f"<a href='{haber['link']}'>Habere Git</a>"
                    )

                    # Adım 4c-iii: Telegram'a Gönder
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)

                # Adım 4d: Haberi İşlendi Olarak Kaydet ve Bekle
                haberi_kaydet(haber['link'])
                # API limitlerine takılmamak için bekleme (Gemini RPM limiti 60)
                # Saniyede 1 istekten biraz yavaş olalım
                await asyncio.sleep(3) # Bekleme süresini biraz artıralım

            print(f"Bu döngüde {analiz_edilen_haber_sayisi} yeni haber analiz edildi.")

        # Döngü Sonu Bekleme
        dongu_bitis_zamani = time.time()
        gecen_sure = dongu_bitis_zamani - dongu_baslangic_zamani
        bekleme_suresi = max(1800 - gecen_sure, 60) # En az 1 dakika bekle, toplamda 30 dakika hedefle
        print(f"--- Döngü tamamlandı ({gecen_sure:.1f} saniye sürdü). {bekleme_suresi / 60:.1f} dakika bekleniyor... ---")
        await asyncio.sleep(bekleme_suresi)

# --- PROGRAM BAŞLANGIÇ NOKTASI ---
if __name__ == "__main__":
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        print("\nProgram kullanıcı tarafından sonlandırıldı.")
    except Exception as main_e:
        print(f"\n❌ KRİTİK ANA HATA: Program beklenmedik bir şekilde durdu! Hata: {main_e}")
        # İsteğe bağlı: Kritik hata durumunda Telegram'a bildirim gönderilebilir
        # asyncio.run(telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, f"❌ BOT DURDU! Hata: {main_e}"))