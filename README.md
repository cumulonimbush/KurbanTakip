# Kurban Takip Sistemi (Sacrificial Animal Tracking System)

Yerel (local) organizasyonlar, çiftlikler ve dernekler için geliştirilmiş, yüksek performanslı ve çevrimdışı (offline) çalışabilen kurban ve hissedar takip uygulaması. Finansal veri bütünlüğünü ve veri güvenliğini ön planda tutan bir mimariyle tasarlanmıştır.

## Özellikler
* **Dinamik Hissedar Yönetimi:** Her hayvan için esnek hisse payı (fractional share) ataması.
* **Finansal Kesinlik:** Kayan nokta (float) hatalarını önleyen `Decimal` tabanlı kuruş/gram hassasiyetinde hesaplama.
* **E.164 Telefon Doğrulaması:** KVKK uyumlu, uluslararası telefon numarası standardizasyonu (`phonenumbers` entegrasyonu).
* **Güvenli Veritabanı:** İşlem (transaction) tabanlı SQLite mimarisi ve tam ilişkisel yapı (Foreign Keys & Cascade Deletion).
* **Otomatik Yedekleme:** Olası veri kayıplarına karşı asenkron çalışan timestamp tabanlı yedekleme modülü.
* **Excel Raporlama:** Renk kodlu ve dinamik hücre boyutlandırmalı detaylı Excel (`.xlsx`) dışa aktarımı.

## Tech Stack
* **Dil:** Python 3.10+
* **Arayüz (GUI):** PyQt6 (MVC Architecture)
* **Veritabanı:** SQLite3 (Repository Pattern ile izole edilmiştir)
* **Derleme/Dağıtım:** Nuitka & Inno Setup

## Kurulum ve Kullanım
**Son Kullanıcılar İçin (Önerilen):**
[Releases](https://github.com/ChestnutRisenKamehameha/KurbanTakip/releases) sayfasından en güncel `KurbanTakip_Kurulum.exe` dosyasını indirin ve kurun. Harici bir programa veya internet bağlantısına ihtiyaç duymaz.

**Geliştiriciler İçin (Development Setup):**
1. Repoyu klonlayın: `git clone https://github.com/ChestnutRisenKamehameha/KurbanTakip.git`
2. Gereksinimleri yükleyin: `pip install -r requirements.txt`
3. Uygulamayı başlatın: `python src/main.py`