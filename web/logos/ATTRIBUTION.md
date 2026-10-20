# Operator logos – sources and licences

Every file in this folder is the original file from Wikimedia Commons, downloaded once and bundled
with the app. The app never loads logos from the internet. Machine-readable copy: `logos.json`
(kept equal to `hmaps/core/logos.py` by `tests/test_logos.py`).

Logos are trademarks of their owners. They are shown only to identify the operator of a site or
a line. Most of them are simple text/shape logos that Commons lists as public domain
(below the threshold of originality, `PD-textlogo`). Files under a CC licence are credited here.

| File | Organisation | Commons source | Licence | Author / notes |
|------|--------------|----------------|---------|----------------|
| `inpa.svg` | רשות הטבע והגנים (Israel Nature and Parks Authority) | [Flag_of_Israel_Nature_and_Parks_Authority.svg](https://commons.wikimedia.org/wiki/File:Flag_of_Israel_Nature_and_Parks_Authority.svg) | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) | Meronim. Modified: the `viewBox` is cropped to the emblem in the middle of the flag; the artwork itself is unchanged. This adapted file is shared under the same licence. |
| `kkl.svg` | קרן קיימת לישראל (KKL-JNF) | [Flag_of_Jewish_National_Fund.svg](https://commons.wikimedia.org/wiki/File:Flag_of_Jewish_National_Fund.svg) (Wikidata Q696658, "logo image") | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) | Meronim |
| `egged.svg` | אגד | [Egged_logo.svg](https://commons.wikimedia.org/wiki/File:Egged_logo.svg) | Public domain (PD-textlogo) | Trademark |
| `metropoline.svg` | מטרופולין | [Metropoline_buses_logo.svg](https://commons.wikimedia.org/wiki/File:Metropoline_buses_logo.svg) | Public domain (PD-textlogo) | |
| `kavim.svg` | קווים | [KavimLogo.svg](https://commons.wikimedia.org/wiki/File:KavimLogo.svg) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | סטודיו DoArt |
| `superbus.svg` | סופרבוס | [Superbus_logo.svg](https://commons.wikimedia.org/wiki/File:Superbus_logo.svg) | Public domain (PD-textlogo) | |
| `nateev-express.svg` | נתיב אקספרס | [Nateev_Express_Logo.svg](https://commons.wikimedia.org/wiki/File:Nateev_Express_Logo.svg) | Public domain (PD-textlogo) | |
| `galim.png` | גלים | [Logo_top_1.png](https://commons.wikimedia.org/wiki/File:Logo_top_1.png) | Public domain (PD-textlogo) | Scaled down to 128 px high |
| `beit-shemesh-express.svg` | בית שמש אקספרס | [בית_שמש_אקספרס.svg](https://commons.wikimedia.org/wiki/File:%D7%91%D7%99%D7%AA_%D7%A9%D7%9E%D7%A9_%D7%90%D7%A7%D7%A1%D7%A4%D7%A8%D7%A1.svg) | Public domain (PD-textlogo) | |
| `israel-railways.svg` | רכבת ישראל | [Israel_Railways_Logo.svg](https://commons.wikimedia.org/wiki/File:Israel_Railways_Logo.svg) | Public domain (PD-textlogo) | Trademark |
| `carmelit.svg` | כרמלית | [Carmelit.svg](https://commons.wikimedia.org/wiki/File:Carmelit.svg) | Public domain (PD-textlogo) | |
| `metronit.svg` | מטרונית | [Metronit_Logo.svg](https://commons.wikimedia.org/wiki/File:Metronit_Logo.svg) | Public domain (PD-textlogo) | |
| `jerusalem-light-rail.svg` | הרכבת הקלה בירושלים | [J-NET.svg](https://commons.wikimedia.org/wiki/File:J-NET.svg) | Public domain (PD-textlogo) | Trademark |
| `dankal.svg` | דנקל (Tel Aviv light rail, נת"ע) | [Dankal_Logo.svg](https://commons.wikimedia.org/wiki/File:Dankal_Logo.svg) | Public domain (PD-textlogo) | Trademark |
| `tevel.svg` | תבל מטרו | [Tevel_Metro_logo.svg](https://commons.wikimedia.org/wiki/File:Tevel_Metro_logo.svg) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | מקף־עברי |

Line endings were normalised to LF; otherwise the files are byte-for-byte the Commons originals
(except `inpa.svg` and `galim.png`, see above).

## Operators without a logo file

No freely licensed logo file was found on Commons for these operators (for example דן, אפיקים,
אלקטרה אפיקים, תנופה, אקסטרה, אגד תעבורה, גי.בי.טורס, מועצה אזורית גולן). The app shows a plain
text badge for them: the short name on a colour of our own choosing. It does not imitate their logos.
