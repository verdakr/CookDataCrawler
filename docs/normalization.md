# Ingredient normalizasyonu ve inceleme politikası

Kural sürümü: `ingredient-rules@1.0.0`.

Akış sırası:

1. Unicode NFKC ve boşluk temizliği uygulanır; `originalText` değişmeden ayrıca tutulur.
2. Kaynak adaptörünün güvenilir dil bilgisi kullanılır. Otomatik çeviri yapılmaz.
3. Tam sayı, ondalık, Unicode kesir, karma kesir ve aralık ayrıştırılır.
4. Türkçe/İngilizce birimler ortak anahtara eşlenir; ham birim korunur.
5. Hazırlama niteleyicileri ile varyantlar ayrı alanlara çıkarılır.
6. Sadece aynı dildeki sürümlü alias sözlüğünde güvenli eşleşme aranır.
7. Tam alias eşleşmesi `1.0`, metin içindeki tek ve en uzun alias `0.88` güven alır.
8. Eşleşmeyen/çakışan ya da güveni `0.85` altındaki satır `review_queue`'ya girer; yeni ingredient otomatik oluşturulmaz.

`cherry domates` örneği `ingredientId=tomato`, `variant=cherry` üretir. Tarif araması
miktar/birim yerine `ingredientId` kümesini kullanabilir. Sözlük değişiklikleri yeni
bir dosya ve yeni sürüm ister; geçmiş run hangi sürümle üretildiğini kaydeder.

## Dedup

Kesin hash; normalize başlık, dil, sırasız ingredient kimliği/aday slug kümesi ve
sıralı talimatlardan oluşturulur. Aynı dilde başlık benzerliği en az `0.92` ve
ingredient Jaccard değeri en az `0.85` olan kayıtlar yakın kopya adayıdır. Kesin ve
yakın adayların ikisi de yalnız kuyruğa eklenir; otomatik silme/birleştirme yoktur.
Farklı diller hiçbir zaman bu kuralla çeviri grubu sayılmaz.

