Tabii. Önce en önemli nokta: `artifacts/phase0.sqlite3` production veritabanı değil. Phase 0 sırasında veri toplama, değişiklik izleme ve idempotency mantığını deneyebilmek için kullanılan yerel SQLite veritabanı.

Komutu çalıştırdığında akış kabaca şöyledir:

```text
TheMealDB API
    ↓
Ham kaynak kaydı (sourceRecord)
    ↓
Tarif ve ingredient normalizasyonu
    ↓
SQLite tablolarına upsert
    ↓
Belirsiz kayıtları inceleme kuyruğuna ekleme
    ↓
ETL çalışma sonucunu kaydetme
```

## Tablolar ne işe yarıyor?

| Tablo | Basit açıklaması |
|---|---|
| `source_records` | API’den gelen güncel ham kayıt |
| `source_revisions` | Ham kaydın önceki sürümleri |
| `recipes` | Uygulamanın kullanabileceği normalize edilmiş tarif |
| `review_queue` | Otomatik karar verilemeyen ingredient ve duplicate adayları |
| `etl_runs` | Her veri toplama çalışmasının özeti |

### 1. `source_records`

TheMealDB’den gelen tarifin mümkün olduğunca ham hali burada saklanır.

Örneğin:

```json
{
  "idMeal": "52768",
  "strMeal": "Apple Frangipan Tart",
  "strIngredient1": "digestive biscuits",
  "strMeasure1": "175g/6oz",
  "strInstructions": "...",
  "strMealThumb": "..."
}
```

Bu payload `record_json` içinde bulunur.

Ayrıca şu bilgiler de saklanır:

- Kaynak: `themealdb_api`
- Kaynaktaki tarif ID’si
- Kaynak URL’si
- Çekilme zamanı
- Lisans ve atıf bilgileri
- İçeriğin hash’i

Aynı kaynağın aynı tarifi şu ikiliyle tanınır:

```text
(source_key, source_recipe_id)
```

Örneğin:

```text
themealdb_api + 52768
```

Bu ikili tablonun primary key’idir. Böylece aynı tarif tekrar indirildiğinde ikinci bir satır oluşturulmaz.

### 2. `source_revisions`

Kaynak tarif değiştiğinde eski ham veri silinmez, bu tabloya taşınır.

Örneğin TheMealDB’de tarifin talimatı değişirse:

```text
Eski payload → source_revisions
Yeni payload → source_records
```

Böylece şu sorular cevaplanabilir:

- Tarif daha önce nasıldı?
- Kaynakta ne zaman değişiklik oldu?
- Hangi içeriğin üzerine yazıldı?
- Değişiklik gerçekten kaynakta mı olmuş?

Bu, veri geçmişi ve denetlenebilirlik için kullanılıyor.

### 3. `recipes`

Bu tabloda ham API verisi değil, ortak formata çevrilmiş tarif bulunur.

Örneğin TheMealDB şu şekilde veri verir:

```json
{
  "strIngredient1": "Tomatoes",
  "strMeasure1": "2 cups"
}
```

Biz bunu şöyle normalize ederiz:

```json
{
  "originalText": "2 cups Tomatoes",
  "quantity": {
    "min": 2,
    "max": 2
  },
  "unit": "cup",
  "unitRaw": "cups",
  "ingredientId": "tomato",
  "normalizationStatus": "matched",
  "confidence": 1.0
}
```

Buradaki önemli ayrımlar:

- `originalText`: Kaynaktan gelen bilgi kaybolmasın diye tutulur.
- `quantity`: Sayısal miktar.
- `unitRaw`: Kaynakta yazan birim.
- `unit`: Bizim ortak birimimiz.
- `ingredientId`: Dil bağımsız ingredient kimliği.
- `confidence`: Eşleşmeye ne kadar güvendiğimiz.
- `reviewRequired`: İnsan incelemesi gerekip gerekmediği.

İngilizce `tomato` ve Türkçe `domates`, aynı ingredient kavramına bağlanabilir:

```text
tomato → ingredientId=tomato
domates → ingredientId=tomato
cherry domates → ingredientId=tomato, variant=cherry
```

### 4. `review_queue`

Normalizasyon sistemi her ingredient için tahminde bulunmaz.

Örneğin:

```text
2 scoops mystery powder
```

Sistem şunları anlayabilir:

```text
quantity = 2
unit = bilinmiyor
ingredient = bilinmiyor
```

Ancak otomatik olarak yeni bir ingredient oluşturmaz. Bunun yerine `review_queue` tablosuna ekler.

Bu tabloda şu tür kayıtlar olabilir:

- Eşleşmeyen ingredient
- Birden fazla ingredient ile eşleşen belirsiz değer
- Bilinmeyen birim
- Düşük güvenli eşleşme
- Yakın duplicate tarif adayı

Amaç yanlış veriyi sessizce veritabanına bağlamamak.

Örneğin sistemin şunu yapmasını istemeyiz:

```text
“mystery powder” → flour
```

Emin değilse insan incelemesine bırakır.

### 5. `etl_runs`

Her `collect` komutunun çalışma özeti burada tutulur.

ETL şu anlama gelir:

```text
Extract   → API’den al
Transform → Normalize et
Load      → Veritabanına kaydet
```

Bir satır yaklaşık şu bilgileri içerir:

```text
source_key        = themealdb_api
read_count        = 50
inserted_count    = 50
updated_count     = 0
unchanged_count   = 0
rejected_count    = 0
started_at        = ...
finished_at       = ...
```

Aynı komutu tekrar çalıştırırsan genellikle şöyle görünür:

```text
read_count        = 50
inserted_count    = 0
updated_count     = 0
unchanged_count   = 50
```

Bu, idempotency kontrolüdür: aynı işlem tekrar çalıştırıldığında duplicate tarif üretmemesi.

## Hash nedir?

Hash, bir içeriğin kısa ve sabit uzunlukta dijital parmak izidir.

Örneğin temsili olarak:

```text
"Domates Çorbası" → sha256:a81c...
```

İçerikte çok küçük bir değişiklik bile hash’i değiştirir:

```text
"Domates Çorbası"  → sha256:a81c...
"Domates çorbası." → sha256:49fe...
```

Hash burada şifreleme için kullanılmıyor. Şifre, parola veya gizli bilgi saklamıyoruz.

Amaçları:

- İçerik değişmiş mi anlamak
- Aynı içeriği tekrar kaydetmemek
- Kesin duplicate kayıtları bulmak
- Bir tarifin hangi ham kayıttan üretildiğini kanıtlamak

## Tam olarak neler hash’leniyor?

### Kaynak hash’i: `source_records.content_hash`

API’den gelen `rawPayload` hash’leniyor.

Kod önce JSON alanlarını kararlı bir sıraya koyuyor:

```json
{"a": 1, "b": 2}
```

ile:

```json
{"b": 2, "a": 1}
```

aynı veri sayılıyor. Daha sonra SHA-256 hash’i üretiliyor:

```text
sha256:...
```

Bu hash’e çekilme zamanı dahil değil. Dolayısıyla aynı tarif bugün ve yarın indirilirse, API içeriği değişmediyse hash aynı kalır.

Mantık şöyledir:

```text
Aynı source ID + aynı hash
→ unchanged

Aynı source ID + farklı hash
→ updated
→ eski kayıt source_revisions'a taşınır
```

### Tarif hash’i: `recipes.content_hash`

Normalize edilmiş tarifin şu temel kimliği hash’leniyor:

- Normalize başlık
- Dil
- Ingredient kimlikleri/kümesi
- Sıralı talimatlar

Kabaca:

```json
{
  "title": "tomato soup",
  "language": "en",
  "ingredients": ["salt", "tomato", "water"],
  "instructions": [
    "chop the tomatoes",
    "cook for twenty minutes"
  ]
}
```

Bu hash duplicate kontrolüne yardımcı olur.

Görsel URL’si veya çekilme zamanı gibi alanlar bu tarif hash’inin ana amacı değildir. Çünkü görsel URL’si değişti diye tarifin kendisini tamamen farklı bir tarif saymak istemeyiz.

### Review fingerprint

`review_queue` kayıtlarında da bir çeşit benzersiz parmak izi vardır.

Örneğin:

```text
ingredient:themealdb_api:52768:3:ingredient-rules@1.0.0
```

Bu şu anlama gelir:

```text
Kaynak      = themealdb_api
Tarif       = 52768
Satır       = 3
Kural sürümü = ingredient-rules@1.0.0
```

Aynı problem tekrar bulunduğunda ikinci bir review satırı açmak yerine mevcut kayıt güncellenir.

## Neden hem ham hem normalize veri saklanıyor?

Sadece normalize veriyi saklasaydık şu sorulara cevap veremezdik:

- Kaynakta aslında ne yazıyordu?
- Normalizasyon hatalı mıydı?
- Yeni kurallarla tekrar çalıştırabilir miyiz?
- Miktar veya varyant kayboldu mu?
- Kaynak sonradan değişti mi?

Bu nedenle iki katman var:

```text
source_records
    Ham ve kaynak odaklı veri
    Değiştirilmeden saklanır

recipes
    Uygulamanın anlayacağı ortak format
    Kurallar değişince yeniden üretilebilir
```

Örneğin normalizasyon algoritmasını geliştirdiğimizde API’ye tekrar gitmeden şu komutu çalıştırabiliriz:

```bash
PYTHONPATH=src python3 -m cookall_data.cli reprocess
```

Bu komut `source_records` içindeki ham payload’ları okuyup `recipes` kayıtlarını yeniden üretir.

## Kod yapısı

Akışın kod tarafındaki karşılığı şöyledir:

```text
cli.py
  Komutları karşılar
    ↓
adapters/themealdb.py
  TheMealDB verisini okur ve ortak formata dönüştürür
    ↓
source_record.py
  Ham kaynak kaydını oluşturur ve hash’ler
    ↓
normalization.py
  Miktar, birim, ingredient, hazırlama ve varyantları ayrıştırır
    ↓
pipeline.py
  Doğrulama, kayıt ve review işlemlerini yönetir
    ↓
storage.py
  SQLite tablolarına insert/update yapar
```

İnceleyebileceğin temel dosyalar:

- [CLI](/Users/trend/Desktop/projects/cook_all/src/cookall_data/cli.py)
- [TheMealDB adaptörü](/Users/trend/Desktop/projects/cook_all/src/cookall_data/adapters/themealdb.py)
- [Normalizasyon](/Users/trend/Desktop/projects/cook_all/src/cookall_data/normalization.py)
- [Pipeline](/Users/trend/Desktop/projects/cook_all/src/cookall_data/pipeline.py)
- [SQLite işlemleri](/Users/trend/Desktop/projects/cook_all/src/cookall_data/storage.py)
- [Veri sözleşmesi](/Users/trend/Desktop/projects/cook_all/contracts/recipe.schema.json)

Özetle:

```text
source_records  = Kaynaktan ne geldi?
source_revisions = Daha önce ne gelmişti?
recipes          = Biz bunu nasıl anladık?
review_queue      = Nelerden emin olamadık?
etl_runs          = Toplama işlemi nasıl sonuçlandı?
hash              = İçerik aynı mı, değişmiş mi?
```