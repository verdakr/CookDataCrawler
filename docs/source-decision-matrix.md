# Kaynak karar matrisi

Değerlendirme tarihi: 2026-08-28. Bu kayıt hukuki görüş değildir; yayın öncesi yeniden doğrulanmalıdır.

| Kaynak | Erişim | İçerik/lisans izlenebilirliği | Görsel kararı | Phase 0 kararı | Production kapısı |
|---|---|---|---|---|---|
| TheMealDB | Resmî V1 API, geliştirme anahtarı `1` | Kaynak ID/URL ve API payload korunur; API kullanım koşulları kayıt altındadır | Tam lisans/üretici kayıt seviyesinde yoksa gösterilmez | Örnekleme dahil | App-store yayını öncesi ücretli abonelik ve güncel koşullar onaylanmalı |
| Türkçe Vikikitap Cookbook | MediaWiki API | Sayfa ID, kalıcı revizyon URL'si, CC BY-SA ve atıf korunur | Her Wikimedia dosyası ayrıca doğrulanmadan gösterilmez | Örnekleme dahil | Atıf gösterimi, değişiklik bildirimi ve ShareAlike dağıtım süreci hazırlanmalı |
| İngilizce Wikibooks Cookbook | MediaWiki API | Sayfa ID, kalıcı revizyon URL'si, CC BY-SA ve atıf korunur | Her Wikimedia dosyası ayrıca doğrulanmadan gösterilmez | Örnekleme dahil | Atıf gösterimi, değişiklik bildirimi ve ShareAlike dağıtım süreci hazırlanmalı |
| Recipe Commons | Belirsiz/kayıt bazlı | Yalnız açık lisansı kitap/kayıt seviyesinde doğrulanabilirse | Lisans ve üretici olmadan gösterilmez | Ertelendi; adaptör yok | Kaynak başına allowlist ve lisans kanıtı gerekir |
| `turkish-recipes-175K` | Türetilmiş derleme | Özgün URL'ler kaldırılmış; upstream hak zinciri denetlenemiyor | Denetlenemez | Reddedildi | Kaynak zinciri geri kazanılmadan kullanılmaz |
| Belirsiz Kaggle/Hugging Face derlemeleri | Değişken | Veri kartı etiketi içerik haklarını tek başına kanıtlamaz | Denetlenemez | Reddedildi | Kayıt seviyesinde kaynak ve lisans gerekir |

## Resmî uçlar

- TheMealDB: `https://www.themealdb.com/api/json/v1/1/search.php?f={letter}`
- English Wikibooks: `https://en.wikibooks.org/w/api.php`
- Türkçe Vikikitap: `https://tr.wikibooks.org/w/api.php` (`Yemek` içerik ad alanı, ID `100`)

Web sayfası scraping'i yapılmaz. MediaWiki içeriği yalnız API üzerinden, TheMealDB içeriği yalnız resmî API üzerinden alınır.
