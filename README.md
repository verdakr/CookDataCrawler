# Cook All — Phase 0 veri temeli

Bu depo yalnızca tarif verisinin güvenli biçimde alınması, kaynak kaydının korunması,
normalize edilmesi ve kalite/lisans sonuçlarının ölçülmesi için Phase 0 araçlarını
içerir. REST API, mobil uygulama, kimlik doğrulama ve production Mongo/Mongoose
şemaları bilinçli olarak kapsam dışındadır.

## Hızlı başlangıç

Python 3.11 dışında çalışma zamanı bağımlılığı yoktur.

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m cookall_data.cli collect --source themealdb --limit 50
PYTHONPATH=src python3 -m cookall_data.cli collect --source wikibooks-en --limit 50
PYTHONPATH=src python3 -m cookall_data.cli collect --source wikibooks-tr --limit 50
PYTHONPATH=src python3 -m cookall_data.cli reprocess
PYTHONPATH=src python3 -m cookall_data.cli report
```

`--limit`, normal modda kaynağın başından işlenecek uygun tarif sayısıdır. Wikibooks'tan
sonraki yeni tarif grubunu almak için resume modu kullanılır:

```bash
export COOKALL_USER_AGENT='CookAllPhase0/0.2 (mailto:YOUR_EMAIL@example.com)'
PYTHONPATH=src python3 -m cookall_data.cli collect --source wikibooks-en --limit 100 --resume
```

Resume modu mevcut source ID'lerini atlar, MediaWiki devam imlecini
`collection_checkpoints` tablosunda tutar ve `--limit` değerini "istenen yeni tarif"
olarak yorumlar. İşlem 429 veya bağlantı hatasıyla kesilirse aynı komut son tamamlanan
API sayfasından devam eder. Kaynak baştan yeniden taranacaksa (örneğin son taramadan
sonra alfabetik olarak araya yeni sayfalar eklendiyse):

```bash
PYTHONPATH=src python3 -m cookall_data.cli collect --source wikibooks-en --limit 100 --resume --reset-cursor
```

Wikimedia için iletişim URL'si veya e-posta içeren gerçek bir User-Agent kullanın.
İstemci istekleri seri ve aralıklı gönderir, `maxlag=5` ekler ve HTTP 429/503'te
`Retry-After` başlığına göre sınırlı tekrar yapar.

Komutlar varsayılan olarak `artifacts/phase0.sqlite3` dosyasını kullanır. Bu SQLite
dosyası yalnızca Phase 0 idempotency/revizyon doğrulama düzeneğidir; production veri
deposu değildir. Ham API yanıtı `source_records` tablosunda aynen JSON olarak saklanır.

Toplama komutu yalnızca resmî API uçlarına gider, tanımlı User-Agent kullanır,
istekleri sınırlar ve geçici hatalarda en fazla üç kez dener. İkinci aynı çalıştırma
yeni kaynak kaydı üretmez. Kaynak içeriği değişirse önceki sürüm `source_revisions`
tablosunda kalır.

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
