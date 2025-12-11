import os
import sys

# ═══════════════════════════════════════════════════════════════════════════════
# GRPC/ALTS UYARILARINI TAMAMEN BASTIR (C++ seviyesinde)
# ═══════════════════════════════════════════════════════════════════════════════
# Bu ayarlar TÜM import'lardan ÖNCE yapılmalı
os.environ['GRPC_VERBOSITY'] = 'NONE'
os.environ['GRPC_TRACE'] = ''
os.environ['GRPC_ENABLE_FORK_SUPPORT'] = '0'
os.environ['GLOG_minloglevel'] = '3'
os.environ['GLOG_logtostderr'] = '0'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['ABSL_MIN_LOG_LEVEL'] = '3'

# stderr'i geçici olarak /dev/null'a yönlendir (gRPC yüklenirken)
_original_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')

# Şimdi gRPC kullanan kütüphaneleri import et
import warnings
warnings.filterwarnings('ignore')

import logging
logging.getLogger('absl').setLevel(logging.CRITICAL)
logging.getLogger('grpc').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
logging.getLogger('google').setLevel(logging.CRITICAL)


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

# stderr'i geri yükle (gRPC yüklendi, artık güvenli)
sys.stderr = _original_stderr


# API Anahtarları
NEWSAPI_KEY = '7060a2ea8f714bc4b8f2b28b10d83765'
GEMINI_API_KEY = 'AIzaSyBB9GKC6KrX1Ibw91yTGmR94g6cAF5zhW8'
TELEGRAM_BOT_TOKEN = '8420610160:AAH0AsElcbB7DH66BmzRP_hg1z1b0Uz8z_o'
TELEGRAM_CHAT_ID = '7965892622'
BINANCE_API_KEY = 'cVDDYZ33Q7ikhtjsfwYP8dS2FhGHAvvPhw9uYRxwDqyf8YEASVnjJZNUYya3GoXO'
BINANCE_SECRET_KEY = 'Eo43m2LK0F6MQVgJbOGBh3XBT6fWnIGyLjug8MmlYwJcuu0nVGV0V8vFFGpM60Hc'
REDDIT_CLIENT_ID = 'G0rIefRfVdRJoJAFsTKuXA'
REDDIT_CLIENT_SECRET = 'tINXoJs8U8nmwLeDxw4mNZPwPymNNw'
REDDIT_USER_AGENT = 'NewsToMe by Milburn89'
REDDIT_USERNAME = 'Milburn89'
REDDIT_PASSWORD = 'Nwpss_reddit2'
ETHERSCAN_API_KEY = 'I4E2S72EWJ6FAGR8S3T4IEZ6EYVVADKVCI'

ISLENMIS_HABERLER_DOSYASI = "islenmis_haberler.txt"

# Loglama yardımcı fonksiyonları
def log(mesaj, seviye="INFO", girinti=0):
    """Yapılandırılmış log çıktısı üretir."""
    zaman = time.strftime("%H:%M:%S")
    prefix = "  " * girinti
    sembol = {"INFO": "•", "OK": "✓", "WARN": "⚠", "ERR": "✗", "DATA": "→"}.get(seviye, "•")
    print(f"[{zaman}] {prefix}{sembol} {mesaj}", flush=True)

def log_bolum(baslik, emoji="📌"):
    """Yeni bir bölüm başlığı yazdırır."""
    print(f"\n{'─'*50}", flush=True)
    print(f"{emoji} {baslik.upper()}", flush=True)
    print(f"{'─'*50}", flush=True)

def log_ozet(veriler):
    """Döngü özeti yazdırır."""
    print(f"\n{'═'*50}", flush=True)
    print("📋 DÖNGÜ ÖZETİ", flush=True)
    print(f"{'═'*50}", flush=True)
    for anahtar, deger in veriler.items():
        print(f"   {anahtar}: {deger}", flush=True)
    print(f"{'═'*50}\n", flush=True)

def islenmis_haberleri_yukle():
    if not os.path.exists(ISLENMIS_HABERLER_DOSYASI):
        return set()
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"HATA (Veritabanı Okuma): {e}")
        return set()

def haberi_kaydet(haber_linki):
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'a', encoding='utf-8') as f:
            f.write(haber_linki + '\n')
    except Exception as e:
        print(f"HATA (Veritabanı Yazma): {e}")

def haber_basligi_uygun_mu(baslik):
    anahtar_kelimeler = ['bitcoin', 'ethereum', 'crypto', 'blockchain', 'binance', 'solana', 'ripple', 'kripto', 'coin', 'token', 'web3', 'nft', 'etf', 'defi', 'metaverse', 'mining', 'staking', 'airdrop']
    return any(kelime in baslik.lower() for kelime in anahtar_kelimeler)

def extract_json_from_text(text):
    if not text:
        return None
    match = re.search(r"```json\s*(\{.*?\})\s*```|(\{.*\})", text, re.DOTALL)
    if match:
        json_part = match.group(1) or match.group(2)
        try:
            return json.loads(json_part)
        except json.JSONDecodeError as e:
            print(f"HATA (JSON Ayıklama): {e}")
            return None
    return None

def haberleri_cek(api_key):
    if not api_key:
        print("HATA (NewsAPI): API anahtarı eksik.")
        return []
    try:
        newsapi = NewsApiClient(api_key=api_key)
        all_articles = newsapi.get_everything(
            q='(bitcoin OR ethereum OR crypto OR blockchain OR web3 OR cryptocurrency) AND NOT (politics OR sports)',
            language='en',
            sort_by='publishedAt',
            page_size=50
        )
        if all_articles['status'] == 'ok':
            return [{'baslik': a['title'], 'link': a['url'], 'kaynak': a['source']['name']}
                    for a in all_articles.get('articles', []) if a['title'] and '[Removed]' not in a['title']]
        print(f"HATA (NewsAPI): {all_articles.get('message')}")
        return []
    except Exception as e:
        print(f"HATA (NewsAPI): {e}")
        return []

def get_haber_icerigi(url):
    try:
        config = Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        config.request_timeout = 20
        config.verify_ssl = False
        config.fetch_images = False
        config.memoize_articles = False

        article = Article(url, config=config)
        article.download()
        article.parse()

        if not article.text or len(article.text) < 100:
            print(f"UYARI (Newspaper3k - {url}): Yeterli içerik bulunamadı.")
            return None
        return article.text[:7000]
    except Exception as e:
        print(f"HATA (Newspaper3k - {url}): {e}")
        return None

def haberleri_analiz_et(api_key, haber_basligi, haber_icerigi):
    if not api_key:
        print("HATA (Gemini AI): API anahtarı eksik.")
        return None
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
          "etkilenen_coinler": array[string] (SADECE Binance ticker sembolleri kullan: BTC, ETH, SOL, XRP, ADA, DOGE, AVAX, LINK gibi 2-5 harfli kısaltmalar. Tam isim YAZMA.),
          "duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        if not response.parts:
            print(f"HATA (Gemini AI): Yanıt alınamadı.")
            return None

        analiz = extract_json_from_text(response.text.strip())
        if analiz:
            required_keys = ["kripto_ile_ilgili_mi", "onem_derecisi", "etkilenen_coinler", "duygu", "ozet_tr"]
            if all(key in analiz for key in required_keys):
                return analiz
            print("HATA (Gemini AI): JSON eksik anahtarlar içeriyor.")
        else:
            print("HATA (Gemini AI): Yanıttan geçerli JSON ayıklanamadı.")
        return None
    except Exception as e:
        print(f"HATA (Gemini AI): {e}")
        return None

STABLECOINS = ['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'FDUSD']

COIN_MAPPING = {
    'BITCOIN': 'BTC', 'ETHER': 'ETH', 'ETHEREUM': 'ETH', 'RIPPLE': 'XRP',
    'CARDANO': 'ADA', 'DOGECOIN': 'DOGE', 'POLKADOT': 'DOT', 'CHAINLINK': 'LINK',
    'LITECOIN': 'LTC', 'AVALANCHE': 'AVAX', 'POLYGON': 'MATIC', 'STELLAR': 'XLM',
    'COSMOS': 'ATOM', 'MONERO': 'XMR', 'TRON': 'TRX', 'UNISWAP': 'UNI',
    'HEDERA': 'HBAR', 'FILECOIN': 'FIL', 'APTOS': 'APT', 'ARBITRUM': 'ARB',
    'OPTIMISM': 'OP', 'NEAR': 'NEAR', 'INJECTIVE': 'INJ', 'RENDER': 'RENDER',
    'SOLANA': 'SOL', 'TETHER': 'USDT', 'BINANCE': 'BNB', 'SUI': 'SUI',
}

INVALID_TERMS = ['STABLECOINS', 'CRYPTO', 'CRYPTOCURRENCY', 'ALTCOIN', 'ALTCOINS', 
                 'TOKEN', 'TOKENS', 'COIN', 'COINS', 'KRIPTO', 'PARA', 'BIRIMLERI']

def normalize_coin_symbol(coin):
    if not coin or not isinstance(coin, str):
        return None
    coin_upper = coin.upper().strip()
    if len(coin_upper) > 10 or ' ' in coin_upper:
        return None
    if coin_upper in INVALID_TERMS or coin_upper in STABLECOINS:
        return None
    return COIN_MAPPING.get(coin_upper, coin_upper)

def get_teknik_analiz(coin_sembolu, binance_client):
    """
    Gelişmiş teknik analiz: RSI (14), EMA 50/200 (Trend), MACD (Momentum)
    4 saatlik mumlara dayalı analiz yapar.
    """
    if not binance_client:
        return None
    coin_upper = normalize_coin_symbol(coin_sembolu)
    if not coin_upper:
        return None
    
    try:
        parite = f"{coin_upper}USDT"
        # EMA 200 için daha fazla veri gerekli (en az 200 mum)
        mumlar = binance_client.get_historical_klines(
            parite, 
            Client.KLINE_INTERVAL_4HOUR, 
            "50 days ago UTC"
        )

        if len(mumlar) < 200:
            log(f"{parite}: Yeterli veri yok ({len(mumlar)} mum)", "WARN", 2)
            return None

        # DataFrame oluştur
        df = pd.DataFrame(mumlar, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        
        son_fiyat = df['close'].iloc[-1]
        
        # ────────── RSI (14) ──────────
        rsi_series = df.ta.rsi(length=14)
        son_rsi = rsi_series.iloc[-1] if rsi_series is not None and not rsi_series.dropna().empty else None
        
        if son_rsi is None or pd.isna(son_rsi):
            rsi_str = "RSI: Hesaplanamadı"
        elif son_rsi > 70:
            rsi_str = f"RSI: {son_rsi:.1f} (Aşırı Alım 📈)"
        elif son_rsi < 30:
            rsi_str = f"RSI: {son_rsi:.1f} (Aşırı Satım 📉)"
        else:
            rsi_str = f"RSI: {son_rsi:.1f} (Nötr 📊)"
        
        # ────────── EMA 50 & EMA 200 (Trend) ──────────
        ema_50 = df.ta.ema(length=50)
        ema_200 = df.ta.ema(length=200)
        
        son_ema50 = ema_50.iloc[-1] if ema_50 is not None and not ema_50.dropna().empty else None
        son_ema200 = ema_200.iloc[-1] if ema_200 is not None and not ema_200.dropna().empty else None
        
        if son_ema200 is None or pd.isna(son_ema200):
            trend_str = "TREND: Hesaplanamadı"
        elif son_fiyat > son_ema200:
            if son_ema50 and son_ema50 > son_ema200:
                trend_str = "TREND: GÜÇLÜ YÜKSELİŞ 🐂🐂"  # Golden cross yakın
            else:
                trend_str = "TREND: YÜKSELİŞ 🐂"
        else:
            if son_ema50 and son_ema50 < son_ema200:
                trend_str = "TREND: GÜÇLÜ DÜŞÜŞ 🐻🐻"  # Death cross yakın
            else:
                trend_str = "TREND: DÜŞÜŞ �"
        
        # ────────── MACD (Momentum) ──────────
        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        
        if macd_df is not None and not macd_df.empty:
            macd_line = macd_df.iloc[-1, 0]  # MACD line
            signal_line = macd_df.iloc[-1, 2]  # Signal line
            macd_hist = macd_df.iloc[-1, 1]  # Histogram
            
            if pd.isna(macd_line) or pd.isna(signal_line):
                momentum_str = "MOMENTUM: Hesaplanamadı"
            elif macd_line > signal_line:
                if macd_hist > 0:
                    momentum_str = "MOMENTUM: POZİTİF (AL) �"
                else:
                    momentum_str = "MOMENTUM: ZAYIF POZİTİF 🟡"
            else:
                if macd_hist < 0:
                    momentum_str = "MOMENTUM: NEGATİF (SAT) 🔴"
                else:
                    momentum_str = "MOMENTUM: ZAYIF NEGATİF �"
        else:
            momentum_str = "MOMENTUM: Hesaplanamadı"
        
        # ────────── Sonuç ──────────
        sonuc = f"{rsi_str} | {trend_str} | {momentum_str}"
        return sonuc

    except BinanceAPIException as e:
        if e.code == -1121:
            log(f"{parite} paritesi Binance'te bulunamadı", "WARN", 2)
        else:
            log(f"Binance API hatası ({coin_sembolu}): {e}", "ERR", 2)
        return None
    except Exception as e:
        log(f"Teknik analiz hatası ({coin_sembolu}): {e}", "ERR", 2)
        return None

def get_reddit_sentiment(gemini_api_key):
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, REDDIT_USERNAME, REDDIT_PASSWORD]):
        print("UYARI (Reddit): API bilgileri eksik.")
        return None
    if not gemini_api_key:
        print("HATA (Reddit/Gemini): Gemini API anahtarı eksik.")
        return None

    try:
        log("Reddit API'ye bağlanılıyor...", "INFO")
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID, client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT, username=REDDIT_USERNAME, password=REDDIT_PASSWORD,
            check_for_async=False
        )
        reddit.user.me()
        log("Reddit'e bağlanıldı, başlıklar çekiliyor...", "OK", 1)

        subreddit = reddit.subreddit("CryptoCurrency")
        metin_blogu = ""
        for submission in subreddit.hot(limit=30):
            metin_blogu += submission.title + ". "

        if not metin_blogu:
            log("Reddit'ten başlık bulunamadı", "WARN", 1)
            return None

        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')

        prompt = f"""
        GÖREV: Aşağıdaki metin bloğunu analiz et. Çıktın SADECE geçerli bir JSON objesi olmalı.

        Metin Bloğu: "{metin_blogu[:6000]}"

        İstenen JSON Yapısı:
        {{
          "genel_duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        if not response.parts:
            log("Gemini yanıt vermedi", "ERR", 1)
            return None

        analiz = extract_json_from_text(response.text.strip())
        if analiz and "genel_duygu" in analiz and "ozet_tr" in analiz:
            log("Reddit analizi tamamlandı", "OK", 1)
            return analiz
        log("JSON ayıklanamadı", "ERR", 1)
        return None

    except praw.exceptions.PRAWException as e:
        log(f"PRAW hatası: {e}", "ERR", 1)
        return None
    except Exception as e:
        log(f"Reddit hatası: {e}", "ERR", 1)
        return None

async def get_borsa_hareketleri():
    """
    Etherscan API kullanarak büyük USDT/USDC girişlerini izler.
    Son 60 dakikada $500K üzeri transferleri tespit eder.
    """
    if not ETHERSCAN_API_KEY:
        print("UYARI (Etherscan): API anahtarı eksik.")
        return []

    # Hedef cüzdan ve kontrat adresleri
    EXCHANGE_WALLETS = {
        "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
        "0x71660c4005ba85c37ccec55d0c4493e66feef4ff": "Coinbase"
    }
    TOKEN_CONTRACTS = {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC"
    }
    
    MIN_VALUE = 500_000 * (10 ** 6)  # $500K (6 decimals)
    ONE_HOUR_AGO = int(time.time()) - 3600  # Son 60 dakika
    
    hareketler = []
    
    try:
        log("Etherscan API sorgulanıyor...", "INFO")
        
        for wallet_address, exchange_name in EXCHANGE_WALLETS.items():
            for token_address, token_name in TOKEN_CONTRACTS.items():
                try:
                    url = "https://api.etherscan.io/api"
                    params = {
                        "module": "account",
                        "action": "tokentx",
                        "contractaddress": token_address,
                        "address": wallet_address,
                        "page": 1,
                        "offset": 100,
                        "sort": "desc",
                        "apikey": ETHERSCAN_API_KEY
                    }
                    
                    response = requests.get(url, params=params, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    
                    if data.get("status") != "1" or not data.get("result"):
                        await asyncio.sleep(0.35)  # Rate limit: 3 calls/sec
                        continue
                    
                    for tx in data["result"]:
                        # Sadece INFLOW (borsaya giriş) kontrol et
                        if tx.get("to", "").lower() != wallet_address.lower():
                            continue
                        
                        # Zaman kontrolü (son 60 dakika)
                        tx_time = int(tx.get("timeStamp", 0))
                        if tx_time < ONE_HOUR_AGO:
                            continue
                        
                        # Değer kontrolü ($500K+)
                        value = int(tx.get("value", 0))
                        if value < MIN_VALUE:
                            continue
                        
                        # Değeri okunabilir formata çevir
                        value_formatted = value / (10 ** 6)
                        if value_formatted >= 1_000_000:
                            value_str = f"{value_formatted / 1_000_000:.2f}M"
                        else:
                            value_str = f"{value_formatted / 1_000:.0f}K"
                        
                        hareket = f"🚨 {exchange_name}'e {value_str} {token_name} Girişi!"
                        if hareket not in hareketler:
                            hareketler.append(hareket)
                    
                    await asyncio.sleep(0.35)  # Rate limit: 3 calls/sec
                    
                except requests.exceptions.Timeout:
                    log(f"{exchange_name}/{token_name} zaman aşımı", "WARN", 1)
                    continue
                except requests.exceptions.RequestException as e:
                    log(f"{exchange_name}/{token_name} hatası: {e}", "ERR", 1)
                    continue
        
        if hareketler:
            log(f"{len(hareketler)} büyük borsa girişi tespit edildi", "OK")
        else:
            log("Son 60 dk'da $500K+ transfer bulunamadı", "INFO")
        
        return hareketler
    
    except Exception as e:
        log(f"Etherscan hatası: {e}", "ERR")
        return []

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    if not bot_token or not chat_id:
        log("Telegram: Bot token veya Chat ID eksik", "ERR")
        return
    try:
        bot = telegram.Bot(token=bot_token)
        if len(mesaj) > 4000:
            mesaj = mesaj[:4000] + "\n\n...(Mesaj kısaltıldı)..."
        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML', disable_web_page_preview=True)
        log("Telegram bildirimi gönderildi", "OK", 1)
    except telegram.error.TelegramError as e:
        log(f"Telegram hatası: {e}", "ERR", 1)
    except Exception as e:
        log(f"Telegram hatası: {e}", "ERR", 1)

async def ana_dongu():
    gerekli_anahtarlar = {
        'NewsAPI': NEWSAPI_KEY, 'Gemini': GEMINI_API_KEY, 'Telegram Bot': TELEGRAM_BOT_TOKEN,
        'Telegram Chat': TELEGRAM_CHAT_ID, 'Binance API': BINANCE_API_KEY, 'Binance Secret': BINANCE_SECRET_KEY,
        'Reddit Client ID': REDDIT_CLIENT_ID, 'Reddit Secret': REDDIT_CLIENT_SECRET,
        'Reddit Username': REDDIT_USERNAME, 'Reddit Password': REDDIT_PASSWORD
    }
    eksik = [isim for isim, deger in gerekli_anahtarlar.items() if not deger]
    if eksik:
        print(f"UYARI: Eksik anahtarlar: {', '.join(eksik)}")

    binance_client = None
    if BINANCE_API_KEY and BINANCE_SECRET_KEY:
        try:
            binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY, requests_params={'timeout': 20})
            binance_client.ping()
            print("✅ Binance API bağlantısı başarılı.")
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
            print("❌ HATA: Binance bağlantı zaman aşımı.")
        except BinanceAPIException as e:
            print(f"❌ HATA (Binance): {e}")
        except Exception as e:
            print(f"❌ HATA (Binance): {e}")
    else:
        print("UYARI: Binance API anahtarları eksik.")

    while True:
        # Döngü istatistikleri
        dongu_baslangic = time.time()
        istatistik = {
            "Reddit": "–",
            "On-Chain": "–",
            "Çekilen Haber": 0,
            "Analiz Edilen": 0,
            "Önemli Haber": 0,
            "Telegram Gönderilen": 0
        }
        
        # Teknik analiz önbelleği (aynı döngüde aynı coin için tekrar API çağrısı yapma)
        teknik_analiz_cache = {}

        print(f"\n{'╔'+'═'*48+'╗'}", flush=True)
        print(f"║{'DÖNGÜ BAŞLADI':^48}║", flush=True)
        print(f"║{time.strftime('%Y-%m-%d %H:%M:%S'):^48}║", flush=True)
        print(f"{'╚'+'═'*48+'╝'}", flush=True)

        # ──────────────── REDDIT ANALİZİ ────────────────
        log_bolum("Reddit Duyarlılık Analizi", "📊")
        reddit_analizi = get_reddit_sentiment(GEMINI_API_KEY)
        if reddit_analizi:
            istatistik["Reddit"] = reddit_analizi.get('genel_duygu', 'Bilinmiyor')
            log(f"Duygu: {reddit_analizi.get('genel_duygu')}", "OK")
            log(f"Özet: {reddit_analizi.get('ozet_tr', '')[:80]}...", "DATA", 1)
            reddit_mesaj = (
                f"📊 <b>Reddit Duyarlılık Analizi (r/CryptoCurrency)</b>\n\n"
                f"<b>Genel Duygu:</b> {reddit_analizi.get('genel_duygu', 'Bilinmiyor')}\n"
                f"<b>Özet:</b> <i>{reddit_analizi.get('ozet_tr', '')}</i>"
            )
            await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, reddit_mesaj)
            istatistik["Telegram Gönderilen"] += 1
            await asyncio.sleep(2)
        else:
            log("Reddit analizi alınamadı", "WARN")

        # ──────────────── ON-CHAIN ANALİZİ ────────────────
        log_bolum("On-Chain Veri Analizi (Etherscan)", "🔗")
        borsa_hareketleri = await get_borsa_hareketleri()
        if borsa_hareketleri:
            istatistik["On-Chain"] = f"{len(borsa_hareketleri)} hareket"
            for hareket in borsa_hareketleri:
                log(hareket, "DATA", 1)
            onchain_mesaj = (
                f"🔗 <b>On-Chain Analiz: Büyük Borsa Girişleri</b>\n"
                f"<i>(Son 60 dakika, $500K+ transferler)</i>\n\n"
            )
            for hareket in borsa_hareketleri:
                onchain_mesaj += f"{hareket}\n"
            onchain_mesaj += "\n<i>⚠️ Büyük girişler potansiyel satış baskısı işareti olabilir.</i>"
            await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, onchain_mesaj)
            istatistik["Telegram Gönderilen"] += 1
            await asyncio.sleep(2)
        else:
            istatistik["On-Chain"] = "Hareket yok"
            log("Son 60 dk'da $500K+ transfer yok", "INFO")

        # ──────────────── HABER ANALİZİ ────────────────
        log_bolum("Haber Analizi (NewsAPI + Gemini)", "📰")
        islenmis = islenmis_haberleri_yukle()
        log(f"Veritabanında {len(islenmis)} işlenmiş haber var", "INFO")

        haberler = haberleri_cek(NEWSAPI_KEY)
        istatistik["Çekilen Haber"] = len(haberler)
        log(f"{len(haberler)} yeni haber çekildi", "OK")

        if haberler:
            for haber in haberler:
                if haber['link'] in islenmis or not haber_basligi_uygun_mu(haber['baslik']):
                    if haber['link'] not in islenmis:
                        haberi_kaydet(haber['link'])
                    continue

                baslik_kisaltilmis = haber['baslik'][:55] + "..." if len(haber['baslik']) > 55 else haber['baslik']
                log(f"İşleniyor: {baslik_kisaltilmis}", "INFO")

                icerik = get_haber_icerigi(haber['link'])
                if not icerik:
                    log("İçerik alınamadı, atlanıyor", "WARN", 1)
                    haberi_kaydet(haber['link'])
                    continue

                analiz = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'], icerik)
                istatistik["Analiz Edilen"] += 1

                if analiz == "KOTA_ASILDI":
                    log("Gemini API kotası aşıldı!", "ERR")
                    break
                if not isinstance(analiz, dict):
                    haberi_kaydet(haber['link'])
                    continue

                onem = analiz.get('onem_derecisi', 'Bulunamadı')
                duygu = analiz.get('duygu', 'Bilinmiyor')
                log(f"Önem: {onem} | Duygu: {duygu}", "DATA", 1)

                if analiz.get('kripto_ile_ilgili_mi') and onem in ['Yüksek', 'Çok Yüksek']:
                    istatistik["Önemli Haber"] += 1
                    log("🔥 ÖNEMLİ HABER! Teknik analiz yapılıyor...", "OK", 1)

                    teknik_str = ""
                    coinler = analiz.get('etkilenen_coinler', [])
                    if coinler and binance_client:
                        teknik_str = "<b>📊 Teknik Analiz (4s):</b>\n"
                        for i, coin in enumerate(coinler[:3]):
                            coin_normalized = normalize_coin_symbol(coin)
                            if not coin_normalized:
                                continue
                            
                            # Önbellekten kontrol et
                            if coin_normalized in teknik_analiz_cache:
                                ta_sonuc = teknik_analiz_cache[coin_normalized]
                                log(f"{coin_normalized}/USDT: Önbellekten alındı", "DATA", 2)
                            else:
                                # API'çağrısı yap ve önbelleğe kaydet
                                ta_sonuc = get_teknik_analiz(coin, binance_client)
                                teknik_analiz_cache[coin_normalized] = ta_sonuc
                                await asyncio.sleep(0.5)
                            
                            if ta_sonuc:
                                teknik_str += f"\n<b>• {coin_normalized}/USDT:</b>\n"
                                teknik_str += f"  {ta_sonuc}\n"
                                log(f"{coin_normalized}: {ta_sonuc}", "DATA", 2)

                    coinler_str = ", ".join(coinler) if coinler else "Belirtilmemiş"
                    mesaj = (
                        f"🚨 <b>{onem.upper()} ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n"
                        f"<b>Başlık:</b> {haber['baslik']}\n"
                        f"<b>Kaynak:</b> {haber['kaynak']}\n\n"
                        f"<b>Haber Analizi:</b>\n"
                        f"• Duygu: {analiz.get('duygu', 'N/A')}\n"
                        f"• Coinler: {coinler_str}\n\n"
                        f"{teknik_str}\n"
                        f"<b>Özet:</b> <i>{analiz.get('ozet_tr', 'Özet alınamadı.')}</i>\n\n"
                        f"<a href='{haber['link']}'>Habere Git</a>"
                    )
                    await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                    istatistik["Telegram Gönderilen"] += 1

                haberi_kaydet(haber['link'])
                await asyncio.sleep(3)

        # ──────────────── DÖNGÜ ÖZETİ ────────────────
        gecen = time.time() - dongu_baslangic
        bekleme = max(1800 - gecen, 60)
        istatistik["Süre"] = f"{gecen:.1f}s"
        istatistik["Sonraki Döngü"] = f"{bekleme/60:.1f} dk sonra"
        
        log_ozet(istatistik)
        
        await asyncio.sleep(bekleme)

if __name__ == "__main__":
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        print("\nProgram sonlandırıldı.")
    except Exception as e:
        print(f"\n❌ KRİTİK HATA: {e}")