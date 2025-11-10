import google.generativeai as genai
import os

# LÜTFEN GEMINI API ANAHTARINIZI BURAYA GİRİN
GEMINI_API_KEY = "AIzaSyBB9GKC6KrX1Ibw91yTGmR94g6cAF5zhW8"

if "BURAYA" in GEMINI_API_KEY:
    print("Lütfen kodun içindeki GEMINI_API_KEY değişkenine kendi anahtarınızı girin.")
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)

        print("API anahtarınızın kullanabileceği ve 'generateContent' metodunu destekleyen modeller:\n")
        
        model_found = False
        for model in genai.list_models():
            # Bizim projemiz 'generateContent' metodunu kullanıyor, bu yüzden sadece bunu destekleyenleri listeliyoruz.
            if 'generateContent' in model.supported_generation_methods:
                print(f"- {model.name}")
                model_found = True
        
        if not model_found:
            print("Kullanılabilir model bulunamadı. Lütfen API anahtarınızı ve projenizin durumunu Google AI Studio'dan kontrol edin.")

    except Exception as e:
        print(f"Bir hata oluştu: {e}")