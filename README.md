# Cook All — MongoDB crawler ve admin API

Bu depo tarif crawler'ını, MongoDB veri katmanını, FastAPI admin API'sini ve tek
worker'lı kalıcı iş kuyruğunu içerir. SQLite artık runtime veri kaynağı değildir;
`artifacts/phase0.sqlite3` yalnız eski Phase 0 artefaktıdır.

## Kurulum ve API

```bash
python3 -m pip install -e .
cp .env.example .env
PYTHONPATH=src python3 -m cookall_data.cli hash-password
# Çıktıyı ADMIN_PASSWORD_HASH olarak .env içine ekleyin.
cookall-api
```

API varsayılan olarak `http://localhost:8000`, OpenAPI dokümanı `/docs` adresindedir.
`MONGODB_URI`, `DB_NAME`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `AUTH_SECRET` ve
`ADMIN_ORIGIN` zorunludur. `.env` Git'e alınmaz.

## MongoDB CLI

Python 3.11+ gerekir.

```bash
python3 -m unittest discover -s tests -v
cookall-data collect --source themealdb --limit 50
cookall-data collect --source wikibooks-en --limit 50
cookall-data collect --source wikibooks-tr --limit 50
cookall-data reprocess
cookall-data report
```

`--limit`, normal modda kaynağın başından işlenecek uygun tarif sayısıdır. Wikibooks'tan
sonraki yeni tarif grubunu almak için resume modu kullanılır:

```bash
export COOKALL_USER_AGENT='CookAllPhase0/0.2 (mailto:YOUR_EMAIL@example.com)'
cookall-data collect --source wikibooks-en --limit 100 --resume
```

Resume modu mevcut source ID'lerini atlar, MediaWiki devam imlecini
`collectionCheckpoints` koleksiyonunda tutar ve `--limit` değerini "istenen yeni tarif"
olarak yorumlar. İşlem 429 veya bağlantı hatasıyla kesilirse aynı komut son tamamlanan
API sayfasından devam eder. Çıktıdaki `sourceExhausted=true`, API'nin sonuna
ulaşıldığını ve o taramada istenen sayıda yeni uygun tarif bulunamayabileceğini
gösterir; hata anlamına gelmez. Kaynak baştan yeniden taranacaksa (örneğin son taramadan
sonra alfabetik olarak araya yeni sayfalar eklendiyse):

```bash
cookall-data collect --source wikibooks-en --limit 100 --resume --reset-cursor
```

Wikimedia için iletişim URL'si veya e-posta içeren gerçek bir User-Agent kullanın.
İstemci istekleri seri ve aralıklı gönderir, `maxlag=5` ekler ve HTTP 429/503'te
`Retry-After` başlığına göre sınırlı tekrar yapar.

Komutlar `.env` içindeki MongoDB veritabanını kullanır. Ham API yanıtları
`sourceRecords`, normalize tarifler `recipes`, eski sürümler ilgili revision
koleksiyonlarında saklanır.

Toplama komutu yalnızca resmî API uçlarına gider, tanımlı User-Agent kullanır,
istekleri sınırlar ve geçici hatalarda en fazla üç kez dener. İkinci aynı çalıştırma
yeni kaynak kaydı üretmez. Kaynak içeriği değişirse önceki sürüm `sourceRevisions`
koleksiyonunda kalır.

## Teslimatlar

- JSON Schema sözleşmeleri: [`contracts/`](contracts/)
- Başlangıç ingredient sözlüğü: [`data/ingredient_dictionary.v1.json`](data/ingredient_dictionary.v1.json)
- Kaynak ve lisans kararları: [`docs/source-decision-matrix.md`](docs/source-decision-matrix.md), [`docs/licensing-and-attribution.md`](docs/licensing-and-attribution.md)
- Normalizasyon ve inceleme kuralları: [`docs/normalization.md`](docs/normalization.md)
- 200 satır ön kontrol bulguları: [`docs/sample-review-findings.md`](docs/sample-review-findings.md)
- Üretilen kalite/dedup raporları: `artifacts/reports/`

## Yayın kapısı

Bu prototip verisi doğrudan mağaza/production yayınına uygun kabul edilmez.
TheMealDB aboneliği ve kullanım koşulları yeniden değerlendirilmeden yayın yapılamaz.
Wikibooks içeriği atıf, revizyon URL'si ve CC BY-SA paylaşım koşullarıyla birlikte
taşınmalıdır. Kayıt seviyesinde lisansı doğrulanamayan görseller `displayAllowed=false`
olarak işaretlenir.
