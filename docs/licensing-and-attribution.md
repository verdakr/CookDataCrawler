# Lisans, atıf ve yayın politikası

## TheMealDB

Kaynak metadatası `TheMealDB Terms of Use` olarak tutulur ve her tarif TheMealDB'ye
atıf taşır. Ücretsiz anahtar öğrenme/geliştirme içindir. Mağaza yayını bir release
blocker'dır; ücretli kullanım hakkı doğrulanmadan bu kayıtlar production'a çıkamaz.
API'deki genel içerik izni bir görselin tam lisans adı, lisans URL'si ve üreticisini
sağlamıyorsa görselin gösterilmesine yetmez. Bu yüzden adaptör varsayılan olarak
`displayAllowed=false` üretir.

## Wikibooks

Metin kaydında sayfa adı, sayfa ID'si, revizyon ID'si ve `oldid` içeren kalıcı URL
tutulur. Atıf, kaynak uygulamada tarifle birlikte görünür olmalıdır. Metin değişirse
uygulamanın değişiklik yaptığını belirtmesi ve CC BY-SA'nın ShareAlike koşullarını
yerine getirmesi gerekir.

Bir Wikibooks sayfasında dosya adının bulunması, görsel lisansının doğrulandığı
anlamına gelmez. Gösterim için dosya sayfasından en az şu alanlar kaydedilmelidir:
canonical dosya URL'si, gerçek medya URL'si, üretici/artist, kısa lisans adı ve lisans
URL'si. Bunlardan biri eksikse görsel gösterilmez. Phase 0 adaptörü güvenli varsayılan
olarak dosya adını kanıt izi şeklinde korur fakat görseli açmaz.

## Saklama

`sourceRecord.rawPayload` değişmeden tutulur. Normalleştirilmiş kayıt ham verinin
yerini almaz. Lisans ve atıf manifesti her rapor çalışmasında kayıt seviyesinde
yeniden üretilir. Lisans koşullarının `verifiedAt` tarihi production build sırasında
eskimiş sayılmalı ve tekrar kontrol edilmelidir.

