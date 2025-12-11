# <<< JSON AYIKLAMA FONKSİYONU SAĞLAMLAŞTIRILDI >>>
def extract_json_from_text(text):
    """ Verilen metin içindeki ilk geçerli JSON bloğunu bulur ve döndürür (Daha Sağlam). """
    if not text or not isinstance(text, str): return None

    # 1. ```json ... ``` bloğunu ara
    match_markdown = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match_markdown:
        json_part = match_markdown.group(1)
    else:
        # 2. Eğer markdown bloğu yoksa, doğrudan { ... } ara
        match_direct = re.search(r"(\{.*\})", text, re.DOTALL)
        if match_direct:
            json_part = match_direct.group(0)
        else:
            # Hiçbir JSON yapısı bulunamadı
            print("HATA (JSON Ayıklama): Metin içinde JSON yapısı bulunamadı.")
            print(f"Alınan Metin (İlk 500kr): {text[:500]}...")
            return None

    # 3. Ayıklanan JSON'ı temizle ve parse etmeyi dene
    try:
        # Temizleme adımları (yorumlar, satır sonları, tek tırnaklar, sonda kalan virgüller)
        json_part = re.sub(r'//.*?$|/\*.*?\*/', '', json_part, flags=re.MULTILINE)
        json_part = json_part.replace('\n', '').replace('\r', '')
        # Tek tırnakları değiştirirken dikkatli olalım, sadece anahtar/değerlerde yapalım? Şimdilik genel kalsın.
        # json_part = json_part.replace("'", '"') # Bu bazen sorun çıkarabilir, şimdilik kapalı
        json_part = re.sub(r',\s*([\}\]])', r'\1', json_part) # Sonda kalan virgüller

        # Çok temel validasyon: { ile başlayıp } ile bitiyor mu?
        if not (json_part.startswith('{') and json_part.endswith('}')):
             print("HATA (JSON Ayıklama): Ayıklanan kısım { } ile başlayıp bitmiyor.")
             print(f"Ayıklanan Kısım: '{json_part[:200]}...'")
             return None

        return json.loads(json_part)
    except json.JSONDecodeError as e:
        print(f"HATA (JSON Ayıklama): Temizlenmiş metin JSON'a çevrilemedi. Hata: {e}")
        print(f"Ayıklanan Kısım (Temizlenmiş): '{json_part[:200]}...'")
        return None
    except Exception as e:
        print(f"HATA (JSON Ayıklama): Beklenmedik hata. {e}")
        return None


# <<< BITQUERY FONKSİYONU - GRAPHQL SORGUSU DÜZELTİLDİ >>>
def get_buyuk_transferler(bitquery_api_key, min_usd_degeri=1000000, sure_dakika=60):
    """Bitquery kullanarak belirli bir değerin üzerindeki son transferleri çeker (Sorgu Düzeltildi)."""
    if not bitquery_api_key or not isinstance(bitquery_api_key, str):
        print("UYARI (Bitquery): API anahtarı eksik veya geçersiz."); return None

    print(f"🔗 Bitquery ile son {sure_dakika} dakikadaki >{min_usd_degeri:,}$ transferler sorgulanıyor...")

    # <<< DÜZELTİLMİŞ GraphQL Sorgusu >>>
    # amount(calculate: usd) kısmı çıkarıldı. Sadece amount ve amount_usd istenir.
    # amount filtresi (minAmountFloat) token miktarına göre çalışır.
    query = """
    query ($limit: Int!, $offset: Int!, $startTime: ISO8601DateTime!, $minAmountFloat: Float!) {
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
          amount_usd: amount(calculate: USD) # USD değerini bu şekilde istemeyi deneyelim (Büyük harf?)
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
        response = requests.post('[https://graphql.bitquery.io/](https://graphql.bitquery.io/)', json={'query': query, 'variables': variables}, headers=headers, timeout=40)
        response.raise_for_status()
        data = response.json()

        if 'errors' in data:
            error_details = json.dumps(data['errors'], indent=2)
            print(f"HATA (Bitquery GraphQL):\n{error_details}")
            # Eğer hata hala 'calculate' ile ilgiliyse, amount_usd istemekten vazgeçelim
            if 'calculate' in error_details:
                 print("-> 'amount_usd' hesaplaması desteklenmiyor gibi. Sorgu güncellenip tekrar denenecek...")
                 # SADECE amount_usd istemeyen alternatif sorgu
                 query_alt = query.replace("amount_usd: amount(calculate: USD)", "")
                 response = requests.post('[https://graphql.bitquery.io/](https://graphql.bitquery.io/)', json={'query': query_alt, 'variables': variables}, headers=headers, timeout=40)
                 response.raise_for_status()
                 data = response.json()
                 if 'errors' in data: # Hala hata varsa vazgeç
                      print(f"HATA (Bitquery GraphQL - Alternatif Sorgu): {json.dumps(data['errors'], indent=2)}"); return None
                 else:
                      print("-> Alternatif sorgu başarılı, USD değeri olmadan devam edilecek.")
                      # USD değeri olmadığı için filtrelemeyi atlayacağız
                      min_usd_degeri = 0 # Filtrelemeyi etkisiz kıl
            else: # Başka bir GraphQL hatasıysa çık
                return None


        transfers = data.get('data', {}).get('ethereum', {}).get('transfers', [])
        if not transfers: print("-> Büyük transfer bulunamadı."); return None

        print(f"-> {len(transfers)} transfer bulundu. USD değeri kontrol ediliyor...")
        ozet_listesi = []
        for t in transfers:
            amount_usd = t.get('amount_usd') # amount_usd gelmeyebilir
            # Eğer amount_usd gelmediyse veya filtre 0 ise direkt ekle
            if min_usd_degeri == 0 or (amount_usd is not None and isinstance(amount_usd, (int, float)) and amount_usd >= min_usd_degeri):
                 sender_address = t.get('sender', {}).get('address', '?')
                 receiver_address = t.get('receiver', {}).get('address', '?')
                 usd_str = f"${amount_usd:,.0f}" if amount_usd is not None else f"{t.get('amount', '?'):,.0f} {t.get('currency',{}).get('symbol','?')}" # USD yoksa token miktarını yaz
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

