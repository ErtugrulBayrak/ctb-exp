# -*- coding: utf-8 -*-
import os
import json
import time
import telegram
import asyncio
import re
# import httpx # Gerek yok
from newsapi import NewsApiClient
import google.generativeai as genai
from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import pandas_ta as ta
from newspaper import Article, Config
import praw
import requests
import google.api_core.exceptions
from dotenv import load_dotenv
import sys # sys.exit() için eklendi
from datetime import datetime, timedelta, timezone # Bitquery için eklendi

# --- .env Dosyasını Yükle ve Kontrol Et ---
print("--- .env Dosyası Yükleniyor ---")
# Script'in bulunduğu dizindeki .env dosyasını bulmaya çalış
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
print(f".env dosyasının arandığı yol: {dotenv_path}")
try:
    loaded = load_dotenv(dotenv_path=dotenv_path, verbose=True, override=True)
    if loaded:
        print(".env dosyası başarıyla yüklendi.")
    else:
        print("UYARI: .env dosyası bulunamadı veya boş! Ortam değişkenleri kullanılacak (eğer varsa).")
except Exception as e:
    print(f"HATA: .env dosyası yüklenirken bir sorun oluştu: {e}")

# --- API ANAHTARLARI (.env veya Ortam Değişkenlerinden) ---
# os.getenv kullanmak, değişken yoksa None döndürür, bu da kontrolü kolaylaştırır.
NEWSAPI_KEY = os.getenv('NEWSAPI_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
BINANCE_SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET')
REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', f'python:KriptoAnalizBotu:v1.0 (by /u/{os.getenv("REDDIT_USERNAME", "DefaultUser")})') # User Agent formatı önerisi
REDDIT_USERNAME = os.getenv('REDDIT_USERNAME')
REDDIT_PASSWORD = os.getenv('REDDIT_PASSWORD')
BITQUERY_API_KEY = os.getenv('BITQUERY_API_KEY')

# --- Bitquery Anahtar Kontrolü ---
print("\n--- .env/Ortam Değişkenleri Detaylı Kontrolü ---")
# Sadece Bitquery değil, önemli olanları loglayalım
print(f"Okunan BITQUERY_API_KEY Tipi: {type(BITQUERY_API_KEY)}")
print(f"Okunan GEMINI_API_KEY Tipi: {type(GEMINI_API_KEY)}")
print(f"Okunan NEWSAPI_KEY Tipi: {type(NEWSAPI_KEY)}")
print("-----------------------------------------")

if BITQUERY_API_KEY is Ellipsis: # Ellipsis kontrolü
    print("❌ KRİTİK HATA: BITQUERY_API_KEY 'Ellipsis' olarak okunuyor.")
    sys.exit(1)
# Anahtarların hiçbiri None olmamalı (opsiyonel olanlar hariç)
if not all([NEWSAPI_KEY, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    print("❌ KRİTİK HATA: Temel API Anahtarları (.env dosyasında veya ortamda) eksik!")
    sys.exit(1)


# --- Veritabanı Dosyası ---
ISLENMIS_HABERLER_DOSYASI = "islenmis_haberler.txt"

# --- TEMEL YARDIMCI FONKSİYONLAR ---

def islenmis_haberleri_yukle():
    """Daha önce işlenen haber linklerini dosyadan okur."""
    if not os.path.exists(ISLENMIS_HABERLER_DOSYASI):
        # Dosya yoksa oluştur
        try: open(ISLENMIS_HABERLER_DOSYASI, 'a').close()
        except Exception as e: print(f"HATA: Veritabanı dosyası oluşturulamadı: {e}")
        return set()
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"HATA (Veritabanı Okuma): {e}")
        return set()

def haberi_kaydet(haber_linki):
    """İşlenen haber linkini dosyaya ekler."""
    if not haber_linki or not isinstance(haber_linki, str): return
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'a', encoding='utf-8') as f:
            f.write(haber_linki + '\n')
    except Exception as e:
        print(f"HATA (Veritabanı Yazma): {e}")

def haber_basligi_uygun_mu(baslik):
    """Haber başlığının kripto ile ilgili olup olmadığını basit anahtar kelimelerle kontrol eder."""
    if not baslik or not isinstance(baslik, str): return False
    anahtar_kelimeler = ['bitcoin', 'ethereum', 'crypto', 'blockchain', 'binance', 'solana', 'ripple', 'kripto', 'coin', 'token', 'web3', 'nft', 'etf', 'defi', 'metaverse', 'mining', 'staking', 'airdrop', 'sec', 'fed', 'whale', 'wallet', 'ledger', 'halving', 'bull run', 'bear market', 'altcoin']
    baslik_kucuk_harf = baslik.lower()
    # Finans kelimeleri geçiyorsa AMA kripto geçmiyorsa eleyelim
    finans_kelimeler = ['stock', 'market', 'dow', 'nasdaq', 'nyse', 'forex', 'interest rate', 'fed meeting', 'inflation', 'cpi']
    if any(f_kelime in baslik_kucuk_harf for f_kelime in finans_kelimeler) and not any(k_kelime in baslik_kucuk_harf for k_kelime in anahtar_kelimeler):
        #print(f"-> Başlık genel finans içeriyor ama kripto değil, atlanıyor: {baslik}") # İsteğe bağlı log
        return False
    return any(kelime in baslik_kucuk_harf for kelime in anahtar_kelimeler)

def extract_json_from_text(text):
    """ Verilen metin içindeki ilk geçerli JSON bloğunu bulur ve döndürür (Daha Sağlam). """
    if not text or not isinstance(text, str): return None

    # 1. ```json ... ``` bloğunu ara
    match_markdown = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match_markdown:
        json_part = match_markdown.group(1)
        print("-> JSON ayıklama: Markdown bloğu bulundu.")
    else:
        # 2. Eğer markdown bloğu yoksa, doğrudan { ... } ara
        match_direct = re.search(r"(\{.*\})", text, re.DOTALL)
        if match_direct:
            json_part = match_direct.group(0)
            print("-> JSON ayıklama: Doğrudan JSON bloğu bulundu.")
        else:
            # Hiçbir JSON yapısı bulunamadı
            print("HATA (JSON Ayıklama): Metin içinde JSON yapısı bulunamadı.")
            print(f"Alınan Metin (İlk 500kr): {text[:500]}...")
            return None

    # 3. Ayıklanan JSON'ı temizle ve parse etmeyi dene
    try:
        # Temizleme adımları (yorumlar, satır sonları, sonda kalan virgüller)
        json_part = re.sub(r'//.*?$|/\*.*?\*/', '', json_part, flags=re.MULTILINE) # Yorumları sil
        json_part = json_part.replace('\n', ' ').replace('\r', ' ') # Satır sonlarını boşlukla değiştir
        
        # En önemlisi: String içindeki kaçışsız çift tırnakları "kaçışlı" hale getir (örn: "özet_tr": "Square"ın...")
        # Bu karmaşık bir işlemdir, bunun yerine Gemini'nin düzgün format göndermesine güvenmek
        # veya daha basit bir temizlik yapmak daha iyi olabilir.
        # Şimdilik sadece sonda kalan virgülü düzeltelim:
        json_part = re.sub(r',\s*([\}\]])', r'\1', json_part) # Sonda kalan virgüller

        # Çok temel validasyon: { ile başlayıp } ile bitiyor mu?
        if not (json_part.startswith('{') and json_part.endswith('}')):
             print("HATA (JSON Ayıklama): Ayıklanan kısım { } ile başlayıp bitmiyor.")
             print(f"Ayıklanan Kısım: '{json_part[:200]}...'")
             return None

        return json.loads(json_part)
    except json.JSONDecodeError as e:
        print(f"HATA (JSON Ayıklama): Temizlenmiş metin JSON'a çevrilemedi. Hata: {e}")
        # Hatanın nerede olduğunu göstermek için hata konumuna yakın metni yazdır
        hata_konumu = e.pos
        baslangic = max(0, hata_konumu - 30)
        bitis = min(len(json_part), hata_konumu + 30)
        print(f"Hata çevresi (konum {hata_konumu}): ...{json_part[baslangic:bitis]}...")
        return None
    except Exception as e:
        print(f"HATA (JSON Ayıklama): Beklenmedik hata. {e}")
        return None

# --- VERİ ÇEKME VE ANALİZ FONKSİYONLARI ---

def haberleri_cek(api_key):
    """NewsAPI kullanarak en son kripto haberlerini çeker. Hata durumunda BOŞ LİSTE döner."""
    if not api_key: print("HATA (NewsAPI): API anahtarı eksik."); return []
    try:
        newsapi = NewsApiClient(api_key=api_key)
        # domains ile kaynakları sınırlandırabiliriz (daha kaliteli haberler için)
        # örn: domains='coindesk.com,cointelegraph.com,theblockcrypto.com,decrypt.co,bloomberg.com,reuters.com'
        all_articles = newsapi.get_everything(
            q='(bitcoin OR ethereum OR crypto OR blockchain OR web3 OR cryptocurrency OR altcoin OR defi OR nft OR metaverse OR binance OR coinbase OR solana OR ripple OR xrp OR doge OR shib OR sec OR halving)',
            language='en',
            sort_by='publishedAt', # En yeniden eskiye
            page_size=80 # Daha fazla çekelim, filtreleme sonrası kalsın
        )
        if all_articles['status'] == 'ok':
            haber_listesi = []
            links = set()
            for a in all_articles.get('articles', []):
                link = a.get('url')
                title = a.get('title')
                source = a.get('source', {}).get('name')
                published_at = a.get('publishedAt') # Yayınlanma zamanını alalım

                # Son 24 saatteki haberleri almak için (isteğe bağlı)
                try:
                    publish_time = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                    if datetime.now(timezone.utc) - publish_time > timedelta(hours=24):
                        continue # Çok eski haberse atla
                except: pass # Zaman formatı hatasını görmezden gel

                if link and title and source and '[Removed]' not in title and link not in links:
                    haber_listesi.append({'baslik': title, 'link': link, 'kaynak': source})
                    links.add(link)
            print(f"-> NewsAPI: {len(haber_listesi)} uygun haber bulundu.")
            return haber_listesi
        else:
            print(f"HATA (NewsAPI): API'dan 'ok' durumu alınamadı. Mesaj: {all_articles.get('message')}")
            return []
    except Exception as e:
        print(f"HATA (NewsAPI): Beklenmedik Hata -> {e}")
        return []

def get_haber_icerigi(url):
    """Verilen URL'deki haber makalesinin metnini çeker (newspaper3k ile), sağlamlaştırılmış."""
    if not url or not isinstance(url, str): return None
    print(f"-> İçerik çekiliyor: {url[:80]}...") # URL'nin başını yazdır
    try:
        config = Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0' # Firefox User Agent
        config.request_timeout = 30 # Timeout'u artıralım
        config.verify_ssl = False
        config.fetch_images = False
        config.memoize_articles = False
        # Bazı siteler için ek header gerekebilir
        # config.headers = {'Referer': 'https://www.google.com/'}

        article = Article(url, config=config)

        # download() metodunu try-except içine alalım, özellikle timeout için
        try:
            article.download()
        except requests.exceptions.Timeout:
             print(f"HATA (Newspaper3k Download - Timeout): {url} zaman aşımına uğradı ({config.request_timeout}s).")
             return None
        except requests.exceptions.RequestException as req_e:
             print(f"HATA (Newspaper3k Download - Request): {url} indirilemedi. {req_e}")
             return None

        if not article.html or len(article.html) < 500:
             print(f"UYARI (Newspaper3k - {url}): Yetersiz HTML ({len(article.html) if article.html else 0} byte).")
             # Bazen HTML çok kısa olsa da parse edilebilir, devam etmeyi deneyelim
             # return None

        # parse() metodunu da try-except içine alalım
        try:
            article.parse()
        except Exception as parse_e:
             print(f"HATA (Newspaper3k Parse): {url} ayrıştırılamadı. {parse_e}")
             return None


        if not article.text or len(article.text) < 200: # Minimum metin uzunluğu
             print(f"UYARI (Newspaper3k - {url}): Yeterli metin ({len(article.text) if article.text else 0} karakter) çıkarılamadı.")
             return None

        print(f"-> İçerik başarıyla çekildi ({len(article.text)} karakter).")
        return article.text[:8000] # Limitle

    except Exception as e:
        error_message = str(e); limit = 150
        if len(error_message) > limit: error_message = error_message[:limit] + "..."
        print(f"HATA (Newspaper3k/Genel - {url}): İçerik çekilemedi. Sebep: {error_message}")
        return None

def haberleri_analiz_et(api_key, haber_basligi, haber_icerigi):
    """Haber başlığını ve içeriğini Gemini ile analiz eder, sağlamlaştırılmış."""
    if not api_key: print("HATA (Gemini AI): API anahtarı eksik."); return None
    if not haber_basligi or not haber_icerigi: return None
    print("-> Gemini ile analiz ediliyor...")
    try:
        genai.configure(api_key=api_key)
        safety_settings = [ # Güvenlik minimumda
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        # Generation config ile timeout ekleyebiliriz (destekliyorsa)
        # generation_config = genai.types.GenerationConfig(temperature=0.7, max_output_tokens=500)
        model = genai.GenerativeModel(
            'models/gemini-2.5-flash',
            safety_settings=safety_settings
            # generation_config=generation_config # Gerekirse eklenebilir
            )

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
        # API çağrısı
        response = model.generate_content(prompt) # request_options={'timeout': 60} eklenebilir mi?

        # Yanıt kontrolü
        if not response.parts:
            feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'Geri bildirim yok'
            print(f"HATA (Gemini AI): Yanıt alınamadı (Boş Parts). Geri bildirim: {feedback}")
            return None
        try:
             raw_text = "".join(part.text for part in response.parts).strip()
             if not raw_text: # Eğer text boşsa
                  print(f"HATA (Gemini AI): Yanıt alındı ama metin içeriği boş. Geri bildirim: {response.prompt_feedback}")
                  return None
        except ValueError:
             print(f"HATA (Gemini AI): Yanıtta metin içeriği bulunamadı (ValueError).")
             return None

        # JSON Ayıklama ve Doğrulama
        analiz = extract_json_from_text(raw_text)
        if analiz:
            required_keys = ["kripto_ile_ilgili_mi", "onem_derecisi", "etkilenen_coinler", "duygu", "ozet_tr"]
            # Anahtarların varlığını ve tiplerini kontrol edelim (daha sağlam)
            if all(key in analiz for key in required_keys) and \
               isinstance(analiz.get("kripto_ile_ilgili_mi"), bool) and \
               isinstance(analiz.get("onem_derecisi"), str) and \
               isinstance(analiz.get("etkilenen_coinler"), list) and \
               isinstance(analiz.get("duygu"), str) and \
               isinstance(analiz.get("ozet_tr"), str):
                print("-> Gemini Analizi başarılı.")
                return analiz
            else:
                 print(f"HATA (Gemini AI): JSON eksik/yanlış tipte anahtarlar içeriyor.")
                 print(f"Alınan JSON: {json.dumps(analiz, indent=2, ensure_ascii=False)}") # Tam JSON'ı yazdır
                 return None
        else:
            print(f"HATA (Gemini AI): Yanıttan geçerli JSON ayıklanamadı.")
            print(f"Alınan Ham Metin:\n---\n{raw_text[:500]}...\n---")
            return None

    except google.api_core.exceptions.ResourceExhausted as e:
         print(f"HATA (Gemini AI): API Kota Aşıldı! Detay: {e}")
         return "KOTA_ASILDI"
    except Exception as e:
        print(f"HATA (Gemini AI): Beklenmedik Hata -> {e}")
        return None

def get_teknik_analiz(coin_sembolu, binance_client):
    """Verilen coin sembolü için Binance'ten 4s RSI değerini hesaplar, sağlamlaştırılmış."""
    if not binance_client: return None
    if not coin_sembolu or not isinstance(coin_sembolu, str): return None
    parite = f"{coin_sembolu.upper()}USDT"
    print(f"-> Teknik Analiz ({parite}) isteniyor...")
    try:
        mumlar = binance_client.get_historical_klines(parite, Client.KLINE_INTERVAL_4HOUR, "4 days ago UTC", limit=100)
        if not mumlar or len(mumlar) < 20: print(f"UYARI ({parite}): RSI için yeterli veri yok ({len(mumlar) if mumlar else 0})."); return None
        df = pd.DataFrame(mumlar, columns=['ts', 'o', 'h', 'l', 'c', 'v', 'ct', 'qav', 'nt', 'tbbav', 'tbqav', 'i']) # Kısa isimler
        for col in ['o', 'h', 'l', 'c', 'v']: df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['c'], inplace=True)
        if len(df) < 15: print(f"UYARI ({parite}): Sayısal veri sonrası yetersiz ({len(df)})."); return None
        rsi_series = df.ta.rsi(close=df['c'], length=14)
        if rsi_series is None or rsi_series.dropna().empty: print(f"UYARI ({parite}): RSI hesaplanamadı (None)."); return None
        son_rsi = rsi_series.iloc[-1]
        if pd.isna(son_rsi): print(f"UYARI ({parite}): Son RSI değeri NaN."); return None
        rsi_yorumu = "Aşırı Alım 📈 (>70)" if son_rsi > 70 else "Aşırı Satım 📉 (<30)" if son_rsi < 30 else "Nötr 📊 (30-70)"
        print(f"-> {parite} RSI: {son_rsi:.1f}")
        return f"{son_rsi:.1f} ({rsi_yorumu})"
    except BinanceAPIException as e:
        if e.code == -1121: print(f"UYARI ({parite}): Binance'te bulunamadı."); return None
        else: print(f"HATA (Binance API - {coin_sembolu}): {e}"); return None
    except Exception as e: print(f"HATA (Teknik Analiz - {coin_sembolu}): {e}"); return None

def get_reddit_sentiment(gemini_api_key):
    """Reddit r/CryptoCurrency'den başlıkları çeker ve Gemini ile duyarlılığı analiz eder, sağlamlaştırılmış."""
    # Reddit API anahtarlarını kontrol et
    reddit_creds_ok = all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT])
    if not reddit_creds_ok: print("UYARI (Reddit): API bilgileri eksik, atlanıyor."); return None
    if not gemini_api_key: print("HATA (Reddit/Gemini): Gemini API anahtarı eksik."); return None

    try:
        print("📊 Reddit duyarlılık analizi başlıyor...")
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID, client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT, check_for_async=False
            # username ve password olmadan read-only modda çalışır
        )
        print(f"-> Reddit Read-Only modu: {reddit.read_only}")
        subreddit = reddit.subreddit("CryptoCurrency")
        _ = subreddit.display_name # Bağlantı testi
        print(f"-> r/{subreddit.display_name} subreddit'ine erişildi.")

        limit = 40 # Daha fazla başlık
        metin_blogu = ""
        cekilen_baslik_sayisi = 0
        try:
            for submission in subreddit.new(limit=limit):
                if not submission.stickied and submission.title and len(submission.title) > 15: # Daha uzun başlıklar
                    metin_blogu += submission.title.strip().replace('"',"'") + ". " # Çift tırnakları değiştir
                    cekilen_baslik_sayisi += 1
        except Exception as praw_e: print(f"HATA (PRAW): Veri çekilemedi. {praw_e}"); return None

        if not metin_blogu: print("-> Reddit'ten uygun başlık bulunamadı."); return None
        print(f"-> {cekilen_baslik_sayisi} başlık alındı, Gemini'ye gönderiliyor...")

        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        prompt = f"""
        GÖREV: Aşağıdaki metin bloğunu analiz et. Çıktın SADECE ve SADECE geçerli bir JSON objesi olmalı. Başka HİÇBİR metin ekleme.

        Metin Bloğu: "{metin_blogu[:8000]}" # Limiti artır

        İstenen JSON Yapısı:
        {{
          "genel_duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string (TEK cümlelik Türkçe özet.)
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        # Yanıt kontrolü ve JSON ayıklama
        if not response.parts:
            feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else '?'
            print(f"HATA (Gemini/Reddit): Yanıt alınamadı. Geri bildirim: {feedback}"); return None
        try: raw_text = "".join(part.text for part in response.parts).strip()
        except ValueError: print(f"HATA (Gemini/Reddit): Yanıtta metin yok."); return None

        analiz = extract_json_from_text(raw_text)
        if analiz and "genel_duygu" in analiz and "ozet_tr" in analiz:
            print("-> Reddit duyarlılık analizi tamamlandı.")
            return analiz
        else:
            print(f"HATA (Gemini/Reddit): Geçerli JSON ayıklanamadı.")
            print(f"Alınan Ham Metin:\n---\n{raw_text[:500]}...\n---")
            return None

    except praw.exceptions.PRAWException as e: print(f"HATA (PRAW): {e}"); return None
    except Exception as e: print(f"HATA (Reddit/Genel): {e}"); return None

# --- BİLDİRİM FONKSİYONU ---

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    """Telegram'a asenkron mesaj gönderir, sağlamlaştırılmış."""
    if not bot_token or not chat_id: print("HATA (Telegram): Token/Chat ID eksik."); return
    try:
        bot = telegram.Bot(token=bot_token)
        max_len = 4096
        if len(mesaj) > max_len:
            print(f"UYARI (Telegram): Mesaj çok uzun ({len(mesaj)}kr), kısaltılıyor.")
            # Linkin kaybolmaması için sondan keselim
            kisa_mesaj = mesaj[:max_len - 50]
            link_match = re.search(r"<a href='(.*?)'>Habere Git</a>", mesaj)
            link_str = link_match.group(0) if link_match else ""
            mesaj = kisa_mesaj + "\n\n...(Mesaj kısaltıldı)...\n\n" + link_str

        # Mesaj göndermeyi timeout ile dene
        await asyncio.wait_for(
            bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML', disable_web_page_preview=True),
            timeout=45.0 # Timeout'u artıralım
        )
        print("✅ Telegram bildirimi başarıyla gönderildi.")
    except asyncio.TimeoutError: print("❌ HATA (Telegram): Mesaj gönderme zaman aşımına uğradı (45s).")
    except telegram.error.BadRequest as e: print(f"❌ HATA (Telegram API - BadRequest): {e}\nMesaj (İlk 500kr): {mesaj[:500]}...")
    except telegram.error.TelegramError as e: print(f"❌ HATA (Telegram API): {e}")
    except Exception as e: print(f"❌ HATA (Telegram/Genel): {e}")

#get buyuk transfer
# <<< YENİ FONKSİYON: BITQUERY İLE ON-CHAIN VERİ ÇEKME (TAM VE DÜZELTİLMİŞ) >>>
def get_buyuk_transferler(bitquery_api_key, min_usd_degeri=1000000, sure_dakika=60):
    """Bitquery kullanarak belirli bir değerin üzerindeki son transferleri çeker (Sorgu Düzeltildi)."""
    # ... (fonksiyonun başındaki api_key kontrolü ve print mesajı aynı) ...
    if not bitquery_api_key or not isinstance(bitquery_api_key, str):
        print("UYARI (Bitquery): API anahtarı eksik veya geçersiz."); return None
    print(f"🔗 Bitquery ile son {sure_dakika} dakikadaki >{min_usd_degeri:,}$ transferler sorgulanıyor...")


    # <<< DÜZELTİLMİŞ GraphQL Sorgusu >>>
    # amount(calculate: USD) kısmı çıkarıldı. Yerine 'amountUSD' (Büyük harf olmadan) alanı istendi.
    # amount filtresi (minAmountFloat) token miktarına göre çalışır.
    query = """
    query ($limit: Int!, $offset: Int!, $startTime: ISO86GDateTime!, $minAmountFloat: Float!) {
      ethereum {
        transfers(
          options: {limit: $limit, offset: $offset, desc: "block.timestamp.iso8601"}
          time: {since: $startTime}
          amount: {gt: $minAmountFloat} # Token miktarına göre filtrele
          currency: {in: ["USDT", "USDC"]}
        ) {
          block { timestamp { iso8601 } }
          sender { address annotation }
          receiver { address annotation }
          currency { symbol }
          amount # Token miktarı
          amountUSD # USD değerini bu şekilde (calculate olmadan) istiyoruz
          transaction { hash }
        }
      }
    }
    """
    # <<<------------------------------------>>>

    # ... (Zaman hesaplaması aynı) ...
    from datetime import datetime, timedelta, timezone
    try: start_time_dt = datetime.now(timezone.utc) - timedelta(minutes=sure_dakika); start_time_iso = start_time_dt.isoformat()
    except Exception as time_e: print(f"HATA (Bitquery Time Calc): {time_e}"); return None

    headers = {'Authorization': f'Bearer {bitquery_api_key}'} # Doğru Header
    variables = {"limit": 30, "offset": 0, "startTime": start_time_iso, "minAmountFloat": 1000000.0 } # Token miktarı filtresi

    response = None
    try:
        response = requests.post('https://graphql.bitquery.io/', json={'query': query, 'variables': variables}, headers=headers, timeout=40)
        response.raise_for_status()
        data = response.json()

        if 'errors' in data:
            error_details = json.dumps(data['errors'], indent=2)
            print(f"HATA (Bitquery GraphQL):\n{error_details}")
            return None

        transfers = data.get('data', {}).get('ethereum', {}).get('transfers', [])
        if not transfers: print("-> Büyük transfer bulunamadı."); return None

        print(f"-> {len(transfers)} transfer bulundu. USD değeri kontrol ediliyor...")
        ozet_listesi = []
        for t in transfers:
            # Artık amountUSD olarak okuyoruz
            amount_usd = t.get('amountUSD')
            if amount_usd is not None and isinstance(amount_usd, (int, float)) and amount_usd >= min_usd_degeri:
                 sender_address = t.get('sender', {}).get('address', '?')
                 receiver_address = t.get('receiver', {}).get('address', '?')
                 usd_str = f"${amount_usd:,.0f}"
                 ozet_listesi.append({
                     "zaman": t.get('block', {}).get('timestamp', {}).get('iso8601', '?').replace('T', ' ').split('.')[0],
                     "gonderen": t.get('sender', {}).get('annotation') or sender_address,
                     "alan": t.get('receiver', {}).get('annotation') or receiver_address,
                     "miktar_str": usd_str, # Miktarı string olarak sakla
                     "token": t.get('currency', {}).get('symbol', '?')
                 })

        if not ozet_listesi: print("-> Filtre sonrası büyük transfer kalmadı."); return None

        print(f"-> {len(ozet_listesi)} transfer özeti hazırlandı.")
        ozet_listesi.sort(key=lambda x: x.get('zaman', ''), reverse=True)
        return ozet_listesi

    # ... (Geri kalan except blokları aynı) ...
    except requests.exceptions.HTTPError as http_err:
        if response is not None and response.status_code == 401:
             print(f"HATA (Bitquery Auth): Kimlik doğrulama başarısız (401). API Anahtarınızı kontrol edin!")
        else:
             print(f"HATA (Bitquery HTTP): {http_err}")
        return None
    except requests.exceptions.Timeout:
        print(f"HATA (Bitquery Request): API isteği zaman aşımına uğradı ({40}s).")
        return None
    except requests.exceptions.RequestException as e:
        print(f"HATA (Bitquery Request): API isteği başarısız oldu. {e}"); return None
    except json.JSONDecodeError as e:
        print(f"HATA (Bitquery Response): API yanıtı JSON formatında değil. {e}"); return None
    except Exception as e:
        print(f"HATA (Bitquery/Genel): Beklenmedik Hata -> {e}"); return None

# --- ANA İŞ AKIŞI DÖNGÜSÜ ---

async def ana_dongu():
    """Ana iş akışını yöneten asenkron fonksiyon."""
    # API Anahtarlarını kontrol et
    gerekli_anahtarlar = { 'NewsAPI': NEWSAPI_KEY, 'Gemini': GEMINI_API_KEY, 'Telegram Bot': TELEGRAM_BOT_TOKEN, 'Telegram Chat': TELEGRAM_CHAT_ID }
    # Opsiyonel Anahtarlar
    opsiyonel_anahtarlar = {'Binance API': BINANCE_API_KEY, 'Binance Secret': BINANCE_SECRET_KEY,'Reddit Client ID': REDDIT_CLIENT_ID, 'Reddit Secret': REDDIT_CLIENT_SECRET, 'Reddit User Agent': REDDIT_USER_AGENT, 'Bitquery': BITQUERY_API_KEY}

    eksik_gerekli = [isim for isim, deger in gerekli_anahtarlar.items() if not deger]
    if eksik_gerekli:
        print(f"❌ KRİTİK HATA: Şu temel .env değişkenleri eksik: {', '.join(eksik_gerekli)}. Program durduruluyor.")
        return # Temel anahtarlar yoksa başlama

    eksik_opsiyonel = [isim for isim, deger in opsiyonel_anahtarlar.items() if not deger]
    if eksik_opsiyonel:
        print(f"UYARI: Şu opsiyonel .env değişkenleri eksik: {', '.join(eksik_opsiyonel)}. İlgili adımlar atlanacak.")

    # Binance istemcisini başlat
    binance_client = None
    if BINANCE_API_KEY and BINANCE_SECRET_KEY:
        try:
            if not isinstance(BINANCE_API_KEY, str) or not isinstance(BINANCE_SECRET_KEY, str): raise ValueError("Binance anahtarları string değil.")
            binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY, requests_params={'timeout': 30}) # Timeout artırıldı
            binance_client.ping()
            print("✅ Binance API istemcisi başarıyla başlatıldı.")
        except Exception as e: print(f"❌ HATA (Binance Client): Başlatılamadı - {e}")
    else: print("UYARI: Binance anahtarları eksik, teknik analiz yapılamayacak.")

    # Ana Sonsuz Döngü
    while True:
        print(f"\n--- {time.ctime()} --- Döngü başlıyor ---")
        dongu_baslangic_zamani = time.time()
        # Döngü başına durumları sıfırla
        reddit_duygu_ozeti_str = ""
        onchain_ozet_str = ""

        # Adım 1: Reddit Duyarlılığı (API anahtarı varsa)
        if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            reddit_analizi = get_reddit_sentiment(GEMINI_API_KEY)
            if reddit_analizi:
                print("--- Reddit Genel Durum ---"); print(json.dumps(reddit_analizi, indent=2, ensure_ascii=False)); print("-------------------------")
                genel_duygu = reddit_analizi.get('genel_duygu', '?'); reddit_ozet = reddit_analizi.get('ozet_tr', '?')
                reddit_duygu_ozeti_str = f"<b>Anlık Reddit Duyarlılığı (r/CC):</b> {genel_duygu}\n<i>{reddit_ozet}</i>\n\n"
            await asyncio.sleep(1) # API arasına nefes payı

        # Adım 2: On-Chain Veri (API anahtarı varsa)
        if BITQUERY_API_KEY:
            buyuk_transferler = get_buyuk_transferler(BITQUERY_API_KEY)
            if buyuk_transferler:
                print("--- On-Chain Büyük Transferler (Son 1 Saat, >1M$) ---")
                onchain_ozet_str += "<b>Dikkat Çeken On-Chain Hareketler (Son 1 Saat):</b>\n"
                for transfer in buyuk_transferler[:3]: # İlk 3'ü
                    gonderen_kisa = transfer['gonderen'][:6]+'...'+transfer['gonderen'][-4:] if isinstance(transfer.get('gonderen'), str) and len(transfer['gonderen'])>15 else transfer.get('gonderen', '?')
                    alan_kisa = transfer['alan'][:6]+'...'+transfer['alan'][-4:] if isinstance(transfer.get('alan'), str) and len(transfer['alan'])>15 else transfer.get('alan', '?')
                    print(f"- {transfer.get('miktar_usd','?')} {transfer.get('token','?')} | {gonderen_kisa} -> {alan_kisa}")
                    onchain_ozet_str += f"- {transfer.get('miktar_usd','?')} {transfer.get('token','?')} | {gonderen_kisa} -> {alan_kisa}\n"
                if len(buyuk_transferler) > 3: onchain_ozet_str += f"- ... ve {len(buyuk_transferler)-3} diğer transfer\n"
                onchain_ozet_str += "\n"
                print("-----------------------------------------------------")
            await asyncio.sleep(1) # API arasına nefes payı

        # Adım 3: İşlenmiş Haberler
        islenmis_haberler = islenmis_haberleri_yukle()
        print(f"{len(islenmis_haberler)} adet haber daha önce işlenmiş.")

        # Adım 4: Yeni Haberleri Çek
        cekilen_haberler = haberleri_cek(NEWSAPI_KEY)
        if cekilen_haberler is None: cekilen_haberler = []
        print(f"{len(cekilen_haberler)} adet haber NewsAPI'dan çekildi.")

        # Adım 5: Yeni Haberleri İşle
        if cekilen_haberler:
            yeni_haber_sayisi = 0; analiz_edilen_haber_sayisi = 0
            MIN_HABER_ARASI_SURE = 2.0 # Saniye (Gemini RPM limitini daha güvenli yönetmek için)

            for i, haber in enumerate(cekilen_haberler):
                haber_baslangic_zamani = time.time()
                link = haber.get('link') # Linki başta alalım

                if not link or link in islenmis_haberler or not haber_basligi_uygun_mu(haber.get('baslik')):
                    if link and link not in islenmis_haberler: haberi_kaydet(link)
                    continue

                yeni_haber_sayisi += 1
                # Haber sırasını loglayalım
                print(f"--- Haber {yeni_haber_sayisi} (Toplamda {i+1}/{len(cekilen_haberler)}) ---")
                print(f"📰 '{haber.get('baslik', 'Başlık Yok')}'")

                haber_icerigi = get_haber_icerigi(link)
                if not haber_icerigi: print("-> İçerik alınamadı."); haberi_kaydet(link); continue

                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber.get('baslik'), haber_icerigi)
                analiz_edilen_haber_sayisi += 1

                if analiz_sonucu == "KOTA_ASILDI": print("Gemini kotası doldu..."); break
                if not isinstance(analiz_sonucu, dict): haberi_kaydet(link); continue

                onem_derecisi = analiz_sonucu.get('onem_derecisi', 'Bulunamadı')
                print(f"-> Gemini Analizi: Önem={onem_derecisi}, Duygu={analiz_sonucu.get('duygu')}, Coinler={analiz_sonucu.get('etkilenen_coinler')}")

                if analiz_sonucu.get('kripto_ile_ilgili_mi') and onem_derecisi in ['Yüksek', 'Çok Yüksek']:
                    print(f"🔥 ÖNEMLİ HABER! ({onem_derecisi})")
                    teknik_analiz_mesaji = ""
                    etkilenen_coinler = analiz_sonucu.get('etkilenen_coinler', [])
                    if etkilenen_coinler and binance_client:
                        teknik_analiz_mesaji += "<b>Teknik Analiz (4s RSI):</b>\n"
                        coin_analiz_sayisi = 0
                        for coin in etkilenen_coinler:
                            # Sadece geçerli ticker formatında olanları deneyelim (örn: 3-5 harf)
                            if re.fullmatch(r'^[A-Z]{3,5}$', coin):
                                if coin_analiz_sayisi >= 3: teknik_analiz_mesaji += "- Diğerleri...\n"; break
                                rsi_degeri = get_teknik_analiz(coin, binance_client)
                                if rsi_degeri: teknik_analiz_mesaji += f" - <b>{coin}/USDT:</b> {rsi_degeri}\n"; coin_analiz_sayisi += 1
                                await asyncio.sleep(0.3) # Binance API arası bekleme
                            else:
                                print(f"UYARI (Teknik Analiz): Geçersiz coin sembolü '{coin}', atlanıyor.")


                    coinler_str = ", ".join(etkilenen_coinler) if etkilenen_coinler else "Belirtilmemiş"
                    # Mesajı oluştururken NoneType hatalarını önlemek için .get kullanalım
                    mesaj = (
                        f"🚨 <b>{onem_derecisi.upper()} ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n"
                        f"<b>Başlık:</b> {haber.get('baslik', 'N/A')}\n"
                        f"<b>Kaynak:</b> {haber.get('kaynak', 'N/A')}\n\n"
                        f"{reddit_duygu_ozeti_str if reddit_duygu_ozeti_str else ''}"
                        f"{onchain_ozet_str if onchain_ozet_str else ''}"
                        f"<b>Haber Analizi (Gemini):</b>\n"
                        f"- Duygu: {analiz_sonucu.get('duygu', 'N/A')}\n"
                        f"- Etkilenen Coinler: {coinler_str}\n\n"
                        f"{teknik_analiz_mesaji if teknik_analiz_mesaji else ''}"
                        f"<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr', 'Özet alınamadı.')}</i>\n\n"
                        f"<a href='{link}'>Habere Git</a>"
                    )
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)

                haberi_kaydet(link)

                # Haber işleme süresini hesapla ve gerekirse bekle
                haber_bitis_zamani = time.time()
                haber_isleme_suresi = haber_bitis_zamani - haber_baslangic_zamani
                kalan_bekleme = MIN_HABER_ARASI_SURE - haber_isleme_suresi
                if kalan_bekleme > 0:
                    #print(f"-> {kalan_bekleme:.1f}sn bekleniyor...") # Debug logu
                    await asyncio.sleep(kalan_bekleme)

            print(f"Bu döngüde {analiz_edilen_haber_sayisi} yeni haber analiz edildi.")

        # Döngü sonu bekleme
        dongu_bitis_zamani = time.time(); gecen_sure = dongu_bitis_zamani - dongu_baslangic_zamani
        bekleme_suresi = max(1800 - gecen_sure, 60) # Toplam 30dk hedefle, min 60sn bekle
        print(f"--- Döngü tamamlandı ({gecen_sure:.1f}s). {bekleme_suresi / 60:.1f}dk bekleniyor... ---")
        await asyncio.sleep(bekleme_suresi)

# --- PROGRAM BAŞLANGIÇ NOKTASI ---
if __name__ == "__main__":
    try:
        # Windows'ta asyncio için event loop policy ayarı nadiren gerekir
        # if sys.platform == 'win32':
        #    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        print("\nProgram kullanıcı tarafından sonlandırıldı.")
    except Exception as main_e:
        print(f"\n❌ KRİTİK ANA HATA: Program beklenmedik bir şekilde durdu! Hata: {main_e}")
        import traceback
        traceback.print_exc() # Detaylı hata raporunu yazdır
        # Opsiyonel: Kritik hata durumunda Telegram'a bildirim gönder
        # try:
        #     if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        #         hata_mesaji = f"❌ BOT DURDU! Kritik Hata:\n<pre>{str(main_e)[:500]}</pre>" # HTML <pre> etiketi formatı korur
        #         asyncio.run(telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, hata_mesaji))
        # except Exception as tel_err: print(f"(Telegram hata bildirimi gönderilemedi: {tel_err})")
    finally:
         print("\nProgram çıkışı yapılıyor.")