# 200 ingredient satırı örneklem incelemesi

Örneklem 100 İngilizce ve 100 Türkçe satırdan oluşur. İnceleme çalışma sayfası
`artifacts/reports/ingredient-manual-review.csv` dosyasındadır; bağımsız insan
inceleyicinin imzası için `reviewer_result` ve `reviewer_notes` sütunları bilerek boş
bırakılmıştır.

2026-08-28 ön kontrol sonucu:

| Ölçüm | Sonuç |
|---|---:|
| Toplam satır | 200 |
| İngilizce / Türkçe | 100 / 100 |
| Miktar ayrıştırıldı | 174 |
| Birim normalize edildi | 141 |
| Varyant çıkarıldı | 12 |
| Ingredient eşleşti | 120 |
| Eşleşmedi | 78 |
| Belirsiz | 2 |

İnceleme sırasında bitişik ölçü (`175g`, `1.25kg`), Unicode fraction slash (`1⁄2`),
Türkçe bitişik yazım (`su bardağıun`) ve uzun birleşik alias'ın kısa alias'a yenilmesi
(`egg plants`, `peanut butter`, `coconut milk`) kusurları bulundu. Kural ve regresyon
testleri eklenerek düzeltildi; ham 152 kayıt ağ çağrısı olmadan yeniden işlendi.

Kalan 78 eşleşmeyen ve 2 belirsiz satır yeni ingredient'e otomatik bağlanmamaktadır.
Bunların çoğu başlangıç sözlüğünde olmayan balık, meyve, baharat ve yöresel ürünlerdir;
bu durum sözlüğü tahminle büyütmek yerine insan kuyruğuna yönlendirme politikasının
beklenen sonucudur. `Juice of 1 Lemon`, `as required salt` gibi miktarı başta olmayan
ve serbest biçimli ölçüler ile kaynak yazım hataları (`adetyumurta`, `pake tkabartma`)
özgün metinleriyle korunur ve bağımsız incelemede özellikle kontrol edilmelidir.

Bu ön kontrol hukuki/lisans incelemesi veya gıda güvenliği onayı değildir. Phase 1'e
geçmeden CSV'deki iki reviewer sütunu bir insan veri sorumlusu tarafından doldurulmalıdır.

