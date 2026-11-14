# カップ麺ポチ

日清食品・明星食品・サンヨー食品・エースコックの**カップ麺の新商品**を1日1回巡回して記録し、発売日・価格・内容量・カロリー・アレルゲンをあとから引けるアーカイブにするサイト（ https://cupmen.netaful.jp/ ）。
構造は姉妹サイト［回転寿司ポチ］（`sushi-pochi`）のエンジンを引き継いでいる（識別子の `chain` は会社＝メーカーのこと）。

姉妹サイト [電書ポチ](https://book.netaful.jp/)・[家電ポチ](https://kaden.netaful.jp/)・
[林檎ポチ](https://apple.netaful.jp/)・[漫画ポチ](https://manga.netaful.jp/)・
[コンビニポチ](https://conbini.netaful.jp/)・[ファストフードポチ](https://fastfood.netaful.jp/)・
[カフェポチ](https://cafe.netaful.jp/)・[回転寿司ポチ](https://sushi.netaful.jp/)と同じ作り。
サイトの色は `#C2185B`（ダークモードは `#F48FB1`）。GA4（`G-CEY0THEB0W`）とAdSenseは設定済み。

**状態: 公開済み・運用中。** GitHub Pagesで公開し、定期実行で毎朝更新している。

## 方針

- **終了は追わない。** 一覧から消えても何もせず、記録は残す。例外は日清の詳細ページの「この商品の生産は終了しています」だけ（`discontinued: true` を保存し、商品ページに「生産終了」と出す。新規取得時と、30日以上たった記録の再確認時＝1回3件までしか取り直さない）。
- 軸は**発売日**。トップは発売日の新しい順の1本の一覧（見出しで分けない。これから発売の商品も先頭に並ぶ）。会社別ページ・発売月別アーカイブ・検索・RSS・sitemap あり。
- 価格は**希望小売価格の税別表示**（「259円（税別）」）。税込に換算しない。
- 保存・表示するのは事実だけ（商品名・価格・内容量・発売日・発売地域・JAN・栄養成分・アレルゲン）。**画像・商品説明文・原材料名は保存も表示もしない。**
- 発売日が `config.json` の `record_from`（2026-08-01）より前の商品は記録しない。以後は新しく見つかったカップ麺を記録する。

## 使い方

```bash
python3 scripts/crawl.py            # 全社を巡回して data/products/ に蓄積（標準ライブラリのみ）
python3 scripts/crawl.py nissin     # 会社を指定すると、その会社だけ
python3 scripts/fetch_netaful_reviews.py   # ネタフルの記事を取得・商品とひも付け
python3 scripts/generate_site.py    # docs/ 一式を生成
python3 -m unittest discover -s tests      # 合成フィクスチャでテスト（公式データのコピーは使わない）
```

定期実行は `.github/workflows/update.yml`（workflow_dispatch のみ。schedule も push トリガーも無い）を Cloud Scheduler から毎朝9:00 JSTに起動している。
通知は `NTFY_TOPIC`（GitHub Secrets）。未設定なら送らない。一時停止はリポジトリ変数 `NTFY_PAUSED=true`。

## 取得元と注意点

UA は `CupmenPochi/1.0 (+https://cupmen.netaful.jp/)`、各社2秒間隔、1回の実行は1社50リクエストまで（robots.txt も数える）。
403・チャレンジが返ったらその会社を止めて通知する（回避しない）。東洋水産（maruchan.co.jp）は対象外で、アクセスしない。

| 会社 | 取得元 | 発売日 | カップ判定 |
|---|---|---|---|
| 日清食品 `nissin` | 新発売一覧 `/jp/product/new/` → 詳細 `/jp/product/items/{ID}/`（静的HTML） | 一覧の見出し「M月D日発売」。**年は書かれていない**ので、先頭は今日に最も近い年、以降は新しい順に並ぶ前提で年またぎを補う（`infer_nissin_dates`） | 詳細に「必要なお湯の目安量」があり、内容量の欄が「(麺量)」、荷姿に「パック」が無く、商品名に メシ・スープ・パック・袋 を含まない |
| 明星食品 `myojo` | 一覧JSON `/api/v1.1/ja_jp/products/list?company=myojofoods` → 詳細JSON `/api/v1/ja_jp/products/detail?id=` | `date`（リニューアル品は date が古く renew_date だけ新しいので、record_from で除かれる） | subcategory が カップ麺／カップ麺(ビッグ)／焼そば／焼そば(ビッグ) |
| サンヨー食品 `sanyo` | サイトマップ `/wp-sitemap-posts-products-1.xml` → 詳細 `/products/{ID}/` | **月まで**（`launch_month: "2026-11"`。表示は「2026年11月」、並び順は月の1日） | 商品名（カップ・タテビッグ・タテ型・大口径・どんぶり・ビッグ。「パック」「袋」は除外）。語が無いときは荷姿が「N食入」でパックでないこと |
| エースコック `acecook` | 新着 `/products/arrival/` → 詳細 `/products/{6桁ID}/` | 詳細の「発売日」 | 栄養成分の欄に「必要なお湯の目安量」があり、商品名に（袋）・5食パック・スープを含まない |

- **明星は `company=myojofoods` が無いと日清の商品が返る**（必須）。1ページ100件で切れる（`x-total-count` が全件数）ので `page=2` 以降も取る。一覧に日清の商品が多く混ざったら記録せず「一覧取得失敗」にする。
- サイトマップに更新日が無いサンヨーは、未取得のURLだけをIDの大きい順に詳細取得する。上限に達したら残りは翌日に回す（`deferred`）。
- エースコックのアレルゲンは、表の色塗り（`cell-beige`）が読めたときだけ保存する。色が1つも読めなければ保存せず、公式ページへのリンクで案内する（「なし」と推測しない）。
- 判定で除いた商品は `data/skipped.json` にID・名前・理由を残し、毎日取り直さない。誤判定の確認はこのファイルで行う。
- 歯止め: 新規候補が会社ごとの閾値を超えたら構造変更の疑いで記録を保留（初回は対象外）。一覧が前回の半分未満に急減したら3日続くまで新規を記録しない（エースコックは一覧が元々小さく入れ替わるので対象外）。
