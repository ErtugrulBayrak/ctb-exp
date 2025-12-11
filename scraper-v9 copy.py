# -*- coding: utf-8 -*-
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
import requests
import google.api_core.exceptions
from dotenv import load_dotenv
import sys
from datetime import datetime, timedelta, timezone

# --- .env Dosyasını Yükle ---
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
loaded = load_dotenv(dotenv_path=dotenv_path, override=True)
if not loaded:
    print("UYARI: .env dosyası bulunamadı veya boş!")

# --- API ANAHTARLARI ---
NEWSAPI_KEY = os.getenv('NEWSAPI_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
BINANCE_SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET')
REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', f'python:KriptoAnalizBotu:v1.0 (by /u/{os.getenv("REDDIT_USERNAME", "DefaultUser")})')
REDDIT_USERNAME = os.getenv('REDDIT_USERNAME')
REDDIT_PASSWORD = os.getenv('REDDIT_PASSWORD')
BITQUERY_API_KEY = os.getenv('BITQUERY_API_KEY')

# Kritik anahtar kontrolü
if not all([NEWSAPI_KEY, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    print("❌ KRİTİK HATA: Temel API Anahtarları eksik!")
    sys.exit(1)

ISLENMIS_HABERLER_DOSYASI = "islenmis_haberler.txt"

# --- YARDIMCI FONKSİYONLAR ---

def islenmis_haberleri_yukle():
    """Daha önce işlenen haber linklerini dosyadan okur."""
    if not os.path.exists(ISLENMIS_HABERLER_DOSYASI):
        try:
            open(ISLENMIS_HABERLER_DOSYASI, 'a').close()
        except Exception as e:
            print(f"HATA: Veritabanı dosyası oluşturulamadı: {e}")
        return set()
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"HATA (Veritabanı Okuma): {e}")
        return set()

def haberi_kaydet(haber_linki):
    """İşlenen haber linkini dosyaya ekler."""
    if not haber_linki or not isinstance(haber_linki, str):
        return
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'a', encoding='utf-8') as f:
            f.write(haber_linki + '\n')
    except Exception as e:
        print(f"HATA (Veritabanı Yazma): {e}")

def haber_basligi_uygun_mu(baslik):
    """Haber başlığının kripto ile ilgili olup olmadığını kontrol eder."""
    if not baslik or not isinstance(baslik, str):
        return False
    
    anahtar_kelimeler = [
        'bitcoin', 'ethereum', 'crypto', 'blockchain', 'binance', 'solana', 'ripple',
        'kripto', 'coin', 'token', 'web3', 'nft', 'etf', 'defi', 'metaverse', 'mining',
        'staking', 'airdrop', 'sec', 'fed', 'whale', 'wallet', 'ledger', 'halving',
        'bull run', 'bear market', 'altcoin'
    ]
    finans_kelimeler = [
        'stock', 'market', 'dow', 'nasdaq', 'nyse', 'forex', 'interest rate',
        'fed meeting', 'inflation', 'cpi'
    ]
    
    baslik_kucuk = baslik.lower()
    kripto_var = any(k in baslik_kucuk for k in anahtar_kelimeler)
    finans_var = any(f in baslik_kucuk for f in finans_kelimeler)
    
    if finans_var and not kripto_var:
        return False
    return kripto_var

def extract_json_from_text(text):
    """Verilen metin içindeki ilk geçerli JSON bloğunu bulur ve döndürür."""
    if not text or not isinstance(text, str):
        return None

    # Markdown bloğu veya doğrudan JSON ara
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        json_part = match.group(1)
    else:
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if not match:
            print("HATA (JSON Ayıklama): JSON yapısı bulunamadı.")
            return None
        json_part = match.group(0)

    try:
        # Temizleme
        json_part = re.sub(r'//.*?$|/\*.*?\*/', '', json_part, flags=re.MULTILINE)
        json_part = json_part.replace('\n', ' ').replace('\r', ' ')
        json_part = re.sub(r',\s*([\}\]])', r'\1', json_part)

        if not (json_part.startswith('{') and json_part.endswith('}')):
            print("HATA (JSON Ayıklama): Geçersiz JSON formatı.")
            return None

        return json.loads(json_part)
    except json.JSONDecodeError as e:
        print(f"HATA (JSON Ayıklama): JSON parse hatası: {e}")
        return None
    except Exception as e:
        print(f"HATA (JSON Ayıklama): Beklenmedik hata: {e}")
        return None

# --- VERİ ÇEKME FONKSİYONLARI ---

def haberleri_cek(api_key):
    """NewsAPI kullanarak en son kripto haberlerini çeker."""
    if not api_key:
        print("HATA (NewsAPI): API anahtarı eksik.")
        return []
    try:
        newsapi = NewsApiClient(api_key=api_key)
        all_articles = newsapi.get_everything(
            q='(bitcoin OR ethereum OR crypto OR blockchain OR web3 OR cryptocurrency OR altcoin OR defi OR nft OR metaverse OR binance OR coinbase OR solana OR ripple OR xrp OR doge OR shib OR sec OR halving)',
            language='en',
            sort_by='publishedAt',
            page_size=80
        )
        if all_articles['status'] != 'ok':
            print(f"HATA (NewsAPI): {all_articles.get('message')}")
            return []

        haber_listesi = []
        links = set()
        for a in all_articles.get('articles', []):
            link = a.get('url')
            title = a.get('title')
            source = a.get('source', {}).get('name')
            published_at = a.get('publishedAt')

            # Son 24 saat kontrolü
            try:
                publish_time = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) - publish_time > timedelta(hours=24):
                    continue
            except:
                pass

            if link and title and source and '[Removed]' not in title and link not in links:
                haber_listesi.append({'baslik': title, 'link': link, 'kaynak': source})
                links.add(link)

        print(f"-> NewsAPI: {len(haber_listesi)} haber bulundu.")
        return haber_listesi
    except Exception as e:
        print(f"HATA (NewsAPI): {e}")
        return []

def get_haber_icerigi(url):
    """Verilen URL'deki haber makalesinin metnini çeker."""
    if not url or not isinstance(url, str):
        return None
    print(f"-> İçerik çekiliyor: {url[:80]}...")
    try:
        config = Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0'
        config.request_timeout = 30
        config.verify_ssl = False
        config.fetch_images = False
        config.memoize_articles = False

        article = Article(url, config=config)

        try:
            article.download()
        except requests.exceptions.Timeout:
            print(f"HATA (Newspaper3k): Timeout - {url}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"HATA (Newspaper3k): {e}")
            return None

        try:
            article.parse()
        except Exception as e:
            print(f"HATA (Newspaper3k Parse): {e}")
            return None

        if not article.text or len(article.text) < 200:
            print(f"UYARI (Newspaper3k): Yetersiz metin ({len(article.text) if article.text else 0} karakter)")
            return None

        print(f"-> İçerik çekildi ({len(article.text)} karakter).")
        return article.text[:8000]

    except Exception as e:
        print(f"HATA (Newspaper3k): {str(e)[:150]}")
        return None

def haberleri_analiz_et(api_key, haber_basligi, haber_icerigi):
    """Haber başlığını ve içeriğini Gemini ile analiz eder."""
    if not api_key or not haber_basligi or not haber_icerigi:
        return None
    print("-> Gemini ile analiz ediliyor...")
    try:
        genai.configure(api_key=api_key)
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)

        prompt = f"""
        GÖREV: Aşağıdaki haber başlığını ve metnini analiz et. Çıktın SADECE geçerli bir JSON objesi olmalı.

        Haber Başlığı: "{haber_basligi}"
        Haber Metni: "{haber_icerigi}"

        İstenen JSON Yapısı:
        {{
          "kripto_ile_ilgili_mi": boolean,
          "onem_derecisi": string ('Düşük', 'Orta', 'Yüksek', 'Çok Yüksek'),
          "etkilenen_coinler": array[string] (SADECE Binance ticker sembolleri),
          "duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string (1-2 cümlelik Türkçe özet)
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        if not response.parts:
            print(f"HATA (Gemini): Yanıt alınamadı.")
            return None
        
        try:
            raw_text = "".join(part.text for part in response.parts).strip()
            if not raw_text:
                print(f"HATA (Gemini): Boş yanıt.")
                return None
        except ValueError:
            print(f"HATA (Gemini): Metin içeriği bulunamadı.")
            return None

        analiz = extract_json_from_text(raw_text)
        if analiz:
            required_keys = ["kripto_ile_ilgili_mi", "onem_derecisi", "etkilenen_coinler", "duygu", "ozet_tr"]
            if all(key in analiz for key in required_keys) and \
               isinstance(analiz.get("kripto_ile_ilgili_mi"), bool) and \
               isinstance(analiz.get("etkilenen_coinler"), list):
                print("-> Gemini Analizi başarılı.")
                return analiz
            print(f"HATA (Gemini): JSON eksik/yanlış anahtar.")
            return None
        print(f"HATA (Gemini): JSON ayıklanamadı.")
        return None

    except google.api_core.exceptions.ResourceExhausted:
        print(f"HATA (Gemini): API Kota Aşıldı!")
        return "KOTA_ASILDI"
    except Exception as e:
        print(f"HATA (Gemini): {e}")
        return None

def get_teknik_analiz(coin_sembolu, binance_client):
    """Verilen coin sembolü için RSI değerini hesaplar."""
    if not binance_client or not coin_sembolu:
        return None
    parite = f"{coin_sembolu.upper()}USDT"
    print(f"-> Teknik Analiz ({parite}) isteniyor...")
    try:
        mumlar = binance_client.get_historical_klines(parite, Client.KLINE_INTERVAL_4HOUR, "4 days ago UTC", limit=100)
        if not mumlar or len(mumlar) < 20:
            return None
        
        df = pd.DataFrame(mumlar, columns=['ts', 'o', 'h', 'l', 'c', 'v', 'ct', 'qav', 'nt', 'tbbav', 'tbqav', 'i'])
        for col in ['o', 'h', 'l', 'c', 'v']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['c'], inplace=True)
        
        if len(df) < 15:
            return None
        
        rsi_series = df.ta.rsi(close=df['c'], length=14)
        if rsi_series is None or rsi_series.dropna().empty:
            return None
        
        son_rsi = rsi_series.iloc[-1]
        if pd.isna(son_rsi):
            return None
        
        rsi_yorumu = "Aşırı Alım 📈 (>70)" if son_rsi > 70 else "Aşırı Satım 📉 (<30)" if son_rsi < 30 else "Nötr 📊 (30-70)"
        print(f"-> {parite} RSI: {son_rsi:.1f}")
        return f"{son_rsi:.1f} ({rsi_yorumu})"
    except BinanceAPIException as e:
        if e.code == -1121:
            print(f"UYARI ({parite}): Binance'te bulunamadı.")
        else:
            print(f"HATA (Binance): {e}")
        return None
    except Exception as e:
        print(f"HATA (Teknik Analiz): {e}")
        return None

def get_reddit_sentiment(gemini_api_key):
    """Reddit r/CryptoCurrency'den duyarlılık analizi yapar."""
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT]):
        return None
    if not gemini_api_key:
        return None

    try:
        print("📊 Reddit duyarlılık analizi başlıyor...")
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
            check_for_async=False
        )
        subreddit = reddit.subreddit("CryptoCurrency")

        metin_blogu = ""
        for submission in subreddit.new(limit=40):
            if not submission.stickied and submission.title and len(submission.title) > 15:
                metin_blogu += submission.title.strip().replace('"', "'") + ". "

        if not metin_blogu:
            return None

        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        prompt = f"""
        GÖREV: Aşağıdaki metin bloğunu analiz et. Çıktın SADECE geçerli bir JSON objesi olmalı.

        Metin Bloğu: "{metin_blogu[:8000]}"

        İstenen JSON Yapısı:
        {{
          "genel_duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string (TEK cümlelik Türkçe özet)
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        if not response.parts:
            return None
        
        try:
            raw_text = "".join(part.text for part in response.parts).strip()
        except ValueError:
            return None

        analiz = extract_json_from_text(raw_text)
        if analiz and "genel_duygu" in analiz and "ozet_tr" in analiz:
            print("-> Reddit duyarlılık analizi tamamlandı.")
            return analiz
        return None

    except Exception as e:
        print(f"HATA (Reddit): {e}")
        return None

# --- BİLDİRİM FONKSİYONU ---

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    """Telegram'a asenkron mesaj gönderir."""
    if not bot_token or not chat_id:
        return
    try:
        bot = telegram.Bot(token=bot_token)
        max_len = 4096
        if len(mesaj) > max_len:
            kisa_mesaj = mesaj[:max_len - 50]
            link_match = re.search(r"<a href='(.*?)'>Habere Git</a>", mesaj)
            link_str = link_match.group(0) if link_match else ""
            mesaj = kisa_mesaj + "\n\n...(Mesaj kısaltıldı)...\n\n" + link_str

        await asyncio.wait_for(
            bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML', disable_web_page_preview=True),
            timeout=45.0
        )
        print("✅ Telegram bildirimi gönderildi.")
    except asyncio.TimeoutError:
        print("❌ HATA (Telegram): Timeout")
    except telegram.error.TelegramError as e:
        print(f"❌ HATA (Telegram): {e}")
    except Exception as e:
        print(f"❌ HATA (Telegram): {e}")

def get_buyuk_transferler(bitquery_api_key, min_usd_degeri=1000000, sure_dakika=60):
    """Bitquery kullanarak büyük transferleri çeker."""
    if not bitquery_api_key or not isinstance(bitquery_api_key, str):
        return None
    print(f"🔗 Bitquery ile son {sure_dakika} dakikadaki >{min_usd_degeri:,}$ transferler sorgulanıyor...")

    query = """
    query ($limit: Int!, $offset: Int!, $startTime: ISO86GDateTime!, $minAmountFloat: Float!) {
      ethereum {
        transfers(
          options: {limit: $limit, offset: $offset, desc: "block.timestamp.iso8601"}
          time: {since: $startTime}
          amount: {gt: $minAmountFloat}
          currency: {in: ["USDT", "USDC"]}
        ) {
          block { timestamp { iso8601 } }
          sender { address annotation }
          receiver { address annotation }
          currency { symbol }
          amount
          amountUSD
          transaction { hash }
        }
      }
    }
    """

    try:
        start_time_dt = datetime.now(timezone.utc) - timedelta(minutes=sure_dakika)
        start_time_iso = start_time_dt.isoformat()
    except Exception as e:
        print(f"HATA (Bitquery): {e}")
        return None

    headers = {'Authorization': f'Bearer {bitquery_api_key}'}
    variables = {"limit": 30, "offset": 0, "startTime": start_time_iso, "minAmountFloat": 1000000.0}

    try:
        response = requests.post(
            'https://graphql.bitquery.io/',
            json={'query': query, 'variables': variables},
            headers=headers,
            timeout=40
        )
        response.raise_for_status()
        data = response.json()

        if 'errors' in data:
            print(f"HATA (Bitquery GraphQL): {json.dumps(data['errors'], indent=2)}")
            return None

        transfers = data.get('data', {}).get('ethereum', {}).get('transfers', [])
        if not transfers:
            print("-> Büyük transfer bulunamadı.")
            return None

        ozet_listesi = []
        for t in transfers:
            amount_usd = t.get('amountUSD')
            if amount_usd is not None and isinstance(amount_usd, (int, float)) and amount_usd >= min_usd_degeri:
                sender = t.get('sender', {})
                receiver = t.get('receiver', {})
                ozet_listesi.append({
                    "zaman": t.get('block', {}).get('timestamp', {}).get('iso8601', '?').replace('T', ' ').split('.')[0],
                    "gonderen": sender.get('annotation') or sender.get('address', '?'),
                    "alan": receiver.get('annotation') or receiver.get('address', '?'),
                    "miktar_str": f"${amount_usd:,.0f}",
                    "token": t.get('currency', {}).get('symbol', '?')
                })

        if not ozet_listesi:
            return None

        print(f"-> {len(ozet_listesi)} transfer özeti hazırlandı.")
        ozet_listesi.sort(key=lambda x: x.get('zaman', ''), reverse=True)
        return ozet_listesi

    except requests.exceptions.HTTPError as e:
        print(f"HATA (Bitquery HTTP): {e}")
        return None
    except requests.exceptions.Timeout:
        print(f"HATA (Bitquery): Timeout")
        return None
    except Exception as e:
        print(f"HATA (Bitquery): {e}")
        return None

# --- ANA İŞ AKIŞI DÖNGÜSÜ ---

async def ana_dongu():
    """Ana iş akışını yöneten asenkron fonksiyon."""
    # Binance istemcisini başlat
    binance_client = None
    if BINANCE_API_KEY and BINANCE_SECRET_KEY:
        try:
            binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY, requests_params={'timeout': 30})
            binance_client.ping()
            print("✅ Binance API bağlantısı başarılı.")
        except Exception as e:
            print(f"❌ HATA (Binance): {e}")
    else:
        print("UYARI: Binance anahtarları eksik.")

    while True:
        print(f"\n--- {time.ctime()} --- Döngü başlıyor ---")
        dongu_baslangic = time.time()
        reddit_duygu_ozeti_str = ""
        onchain_ozet_str = ""

        # Reddit Duyarlılığı
        if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            reddit_analizi = get_reddit_sentiment(GEMINI_API_KEY)
            if reddit_analizi:
                genel_duygu = reddit_analizi.get('genel_duygu', '?')
                reddit_ozet = reddit_analizi.get('ozet_tr', '?')
                reddit_duygu_ozeti_str = f"<b>Reddit Duyarlılığı (r/CC):</b> {genel_duygu}\n<i>{reddit_ozet}</i>\n\n"
            await asyncio.sleep(1)

        # On-Chain Veri
        if BITQUERY_API_KEY:
            buyuk_transferler = get_buyuk_transferler(BITQUERY_API_KEY)
            if buyuk_transferler:
                onchain_ozet_str += "<b>On-Chain Hareketler (Son 1 Saat):</b>\n"
                for transfer in buyuk_transferler[:3]:
                    gonderen = transfer['gonderen']
                    alan = transfer['alan']
                    gonderen_kisa = f"{gonderen[:6]}...{gonderen[-4:]}" if len(gonderen) > 15 else gonderen
                    alan_kisa = f"{alan[:6]}...{alan[-4:]}" if len(alan) > 15 else alan
                    onchain_ozet_str += f"- {transfer.get('miktar_str','?')} {transfer.get('token','?')} | {gonderen_kisa} -> {alan_kisa}\n"
                if len(buyuk_transferler) > 3:
                    onchain_ozet_str += f"- ... ve {len(buyuk_transferler)-3} diğer transfer\n"
                onchain_ozet_str += "\n"
            await asyncio.sleep(1)

        # İşlenmiş Haberler
        islenmis_haberler = islenmis_haberleri_yukle()
        print(f"{len(islenmis_haberler)} haber daha önce işlenmiş.")

        # Yeni Haberleri Çek
        cekilen_haberler = haberleri_cek(NEWSAPI_KEY) or []
        print(f"{len(cekilen_haberler)} haber çekildi.")

        # Haberleri İşle
        if cekilen_haberler:
            analiz_sayisi = 0
            MIN_BEKLEME = 2.0

            for i, haber in enumerate(cekilen_haberler):
                haber_baslangic = time.time()
                link = haber.get('link')

                if not link or link in islenmis_haberler or not haber_basligi_uygun_mu(haber.get('baslik')):
                    if link and link not in islenmis_haberler:
                        haberi_kaydet(link)
                    continue

                print(f"--- Haber {i+1}/{len(cekilen_haberler)} ---")
                print(f"📰 '{haber.get('baslik', 'Başlık Yok')}'")

                haber_icerigi = get_haber_icerigi(link)
                if not haber_icerigi:
                    haberi_kaydet(link)
                    continue

                analiz_sonucu = haberleri_analiz_et(GEMINI_API_KEY, haber.get('baslik'), haber_icerigi)
                analiz_sayisi += 1

                if analiz_sonucu == "KOTA_ASILDI":
                    print("Gemini kotası doldu...")
                    break
                if not isinstance(analiz_sonucu, dict):
                    haberi_kaydet(link)
                    continue

                onem = analiz_sonucu.get('onem_derecisi', 'Bulunamadı')
                print(f"-> Analiz: Önem={onem}, Duygu={analiz_sonucu.get('duygu')}")

                if analiz_sonucu.get('kripto_ile_ilgili_mi') and onem in ['Yüksek', 'Çok Yüksek']:
                    print(f"🔥 ÖNEMLİ HABER! ({onem})")
                    teknik_mesaj = ""
                    coinler = analiz_sonucu.get('etkilenen_coinler', [])
                    
                    if coinler and binance_client:
                        teknik_mesaj += "<b>Teknik Analiz (4s RSI):</b>\n"
                        coin_sayisi = 0
                        for coin in coinler:
                            if re.fullmatch(r'^[A-Z]{3,5}$', coin):
                                if coin_sayisi >= 3:
                                    teknik_mesaj += "- Diğerleri...\n"
                                    break
                                rsi = get_teknik_analiz(coin, binance_client)
                                if rsi:
                                    teknik_mesaj += f" - <b>{coin}/USDT:</b> {rsi}\n"
                                    coin_sayisi += 1
                                await asyncio.sleep(0.3)

                    coinler_str = ", ".join(coinler) if coinler else "Belirtilmemiş"
                    mesaj = (
                        f"🚨 <b>{onem.upper()} ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n"
                        f"<b>Başlık:</b> {haber.get('baslik', 'N/A')}\n"
                        f"<b>Kaynak:</b> {haber.get('kaynak', 'N/A')}\n\n"
                        f"{reddit_duygu_ozeti_str}"
                        f"{onchain_ozet_str}"
                        f"<b>Haber Analizi:</b>\n"
                        f"- Duygu: {analiz_sonucu.get('duygu', 'N/A')}\n"
                        f"- Coinler: {coinler_str}\n\n"
                        f"{teknik_mesaj}"
                        f"<b>Özet:</b> <i>{analiz_sonucu.get('ozet_tr', 'Özet alınamadı.')}</i>\n\n"
                        f"<a href='{link}'>Habere Git</a>"
                    )
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)

                haberi_kaydet(link)

                # Rate limiting
                kalan = MIN_BEKLEME - (time.time() - haber_baslangic)
                if kalan > 0:
                    await asyncio.sleep(kalan)

            print(f"Bu döngüde {analiz_sayisi} haber analiz edildi.")

        # Döngü sonu bekleme
        gecen_sure = time.time() - dongu_baslangic
        bekleme = max(1800 - gecen_sure, 60)
        print(f"--- Döngü tamamlandı ({gecen_sure:.1f}s). {bekleme/60:.1f}dk bekleniyor... ---")
        await asyncio.sleep(bekleme)

# --- PROGRAM BAŞLANGIÇ ---
if __name__ == "__main__":
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        print("\nProgram sonlandırıldı.")
    except Exception as e:
        print(f"\n❌ KRİTİK HATA: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Program çıkışı.")