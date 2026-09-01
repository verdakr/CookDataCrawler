# Phase 1 mimari girdisi

Phase 1, bu depodaki JSON Schema'ları uygulama sınırı olarak kullanmalıdır; ham
`sourceRecord` ile normalleştirilmiş `recipe` aynı belgeye gömülmemelidir.

Önerilen Mongo koleksiyon sınırları ve indeks niyetleri (production şeması değildir):

| Koleksiyon | Kimlik/indeks niyeti | Gerekçe |
|---|---|---|
| `sourceRecords` | unique `(sourceKey, sourceRecipeId)`; `contentHash` | idempotent upsert ve kaynak değişimi |
| `sourceRevisions` | `(sourceKey, sourceRecipeId, contentHash)` | değişen ham kaydın denetim izi |
| `recipes` | unique `(sourceRef.sourceKey, sourceRef.sourceRecipeId)`; `contentHash`; `language` | normalize görünüm ve kesin hash taraması |
| `ingredients` | unique `canonicalKey`; unique `(labels.language, labels.slug)` | dil bağımsız kavram/yerel etiket ayrımı |
| `reviewQueue` | `(type, fingerprint)`; `status` | ingredient, lisans ve dedup insan incelemesi |
| `etlRuns` | `runId`; `(sourceKey, startedAt)` | işletim ve kalite geçmişi |

## Zorunlu kapılar

- API, yalnız `displayAllowed=true` görseller için URL döndürmelidir.
- Atıf alanları tarif yanıtında kaybolmamalıdır.
- Sözlük/kural sürümü değiştiğinde normalizasyon yeniden çalıştırılabilmelidir.
- `recipeGroupId` yalnız kaynak açıkça dil ilişkisi verirse atanmalıdır.
- Near-duplicate kuyruğu insan kararı olmadan silme/birleştirme yapmamalıdır.
- TheMealDB verisi bulunan production veya app-store build, güncel ücretli kullanım
  hakkı doğrulanmadan başarısız olmalıdır.
- CC BY-SA içerik gösterimi için ürün seviyesinde atıf, değişiklik bildirimi ve
  ShareAlike dağıtım kararı tamamlanmalıdır.

## Bilinçli ertelenenler

REST uçları, authentication, Mongoose modelleri, sorgu performans optimizasyonu,
otomatik çeviri, image proxy/cache ve Recipe Commons adaptörü Phase 0'a dahil değildir.

