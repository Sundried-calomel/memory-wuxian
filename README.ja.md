# Memory無限

> **2.14.3:** L2 セマンティックジョブの再実行を修正し、5 分ごとの限定バッチを 8 件に増やし、モデル呼び出しを最大 3 並列に限定しながらアーカイブ書き込みを直列化し、完全復旧監査を 5 分のホットパスから外し、操作台に段階別時間を表示し、トランザクション成功後にのみ旧 macOS セマンティックバックフィル起動項目を廃止します。**2.14.2** は同一サイズの rollout 書き換えをバイトハッシュで検証し、macOS ユーザートランザクションがコレクターの準備完了待機時間を最後まで使えるようにします。**2.14.1** は macOS インストールを修正し、オフラインで分離された PyYAML フォールバックランタイムを追加します。**2.14.0** はデバイスローカルの Project Evidence Owner が、明示された閉じたファイル選択を限定的かつモデル非依存で更新します。**2.13.0** は明示的で不変なプロジェクト証拠パッケージと独立暗号化
> `project-evidence-v1` ストリームを追加し、**2.12.7** の修正も保持します。Python とネイティブのライブ収集は、同じ旧ラウンドを共有する最後の
> 会話が閉じるまで、グローバル完了状態を進めません。

> **2.12.6:** 同じ旧ラウンド番号を共有する全ての会話に個別の最終回答が揃うまで、
> 復元されたラウンドを完了扱いにしません。

> **2.12.5:** 旧版の競合で同じラウンド番号を共有した会話を、会話単位で全ての
> 不変な原文から復元します。再構築するのは派生状態だけで、原文は変更しません。

> **2.12.4:** 意味要約の可逆ペイロードは、欠落フィールドと明示的な JSON
> `null` を区別するようになりました。異なる旧メタデータを持つレベル1要約も
> 上位要約へ進めます。Heartbeat 監査はネイティブ収集と同じアーカイブログを
> 使用し、収集バッチ途中の状態を派生投影のずれとしてキャッシュしません。

> **2.12.3:** Codex の継続アーカイブ中に transcript、index、state の再構築可能な
> 一時的ずれがあっても、意味要約の自動追跡を継続します。明示的な復旧債務がない
> 場合は直近 24 時間の深度復旧結果を再利用し、各固定ソースの SHA-256 は保存前に
> 検証します。raw 履歴の整合性エラーは引き続き実行を停止します。

> **2.12.2:** 日別チャートの日付を太字で基準線の下に戻し、端末内／全端末の棒を
> 同じ幅にそろえ、青と緑のコントラストを改善しました。Token 台帳の再構築中も、
> ネイティブ復旧はアーカイブ済みメッセージの水位を独立して保持します。保留中の
> 親要約は子要約を予約し、既存の重複派生ジョブだけを SHA-256 付き証跡へ隔離します。
> 元の履歴と保存済み要約は変更しません。

> **2.12.1:** ネイティブコレクターは、アップグレード時に保持済み rollout から
> format-v2 日次 Token 台帳を再構築し、format-v1 派生台帳があっても収集を中断しません。
> 元の rollout と追記専用メモリ記録は変更されません。

> **2.12.0:** `daily_metrics.py` はこの端末とすべての trusted synchronized devices
> を重ねた日別棒グラフを追加します。メッセージと Codex-reported Token の切替、
> 端末別内訳、同期の古さを表示し、日付境界は `Asia/Tokyo` です。federation
> protocol v2 はパスを除いた不変 Token 台帳リビジョンを交換し、protocol v1 の
> 読み取り互換性も保持します。未取得テレメトリを文字数推定やアカウント全体値として
> 扱いません。
>
> **2.11.6:** Windows 更新は検証済みのパッケージ指定 Skill ルートをプロセス SID
> より優先し、Codex サンドボックスのユーザーパスがデスクトップショートカットへ
> 混入することを防ぎます。ショートカットは原子的に作成され、最終ターゲット、作業
> ディレクトリ、アイコン、引数、起動設定を再読込して検証します。インストール後の
> 実効果ゲートは、存在していても別ユーザーや欠落バイナリを指すリンクを拒否します。
>
> **2.11.5:** バックグラウンドの正常性は、プロセスの存在ではなく実際の効果で
> 検証されます。ネイティブコレクターは AI を起動も待機もせず、独立した保守
> スケジューラが Level-2 以上の要約ジョブ作成、安全な派生索引修復、恒久的負債の
> 明示を担当します。意味索引は現在の raw ソース水位に拘束され、古い場合は閉じるか
> `semantic-index-stale-keyword-fallback` としてキーワード検索への降格を明示します。
> 中断バックアップの一時ディレクトリを清掃し、
> クラウド待機や部分失敗を成功扱いせず、既存ユーザー値を保持した設定移行を行います。
> `runtime_effect_gate.py` は隠れたフォールバックと古い水位を拒否します。
>
> **2.11.4:** 継続的な追従はインストールとアップグレードをまたいで維持されます。
> ネイティブコレクターは最古の対象境界を `collector-activation.json` に保存し、
> 保持された rollout を制限付きバッチでストリーミングし、永続カーソルから再開して
> `coverage-status.json` を生成します。raw 追記と派生状態コミットの間で中断した場合、
> 次のネイティブ再開は決定的な `heartbeat --repair` の完了を必須とします。
> `install_maintenance_supervisor.py` が登録する
> 非表示の5分間隔タスクは `maintenance_supervisor.py` を実行するため、Codex 終了中も
> 機械処理とバックアップ負債を進め、意味要約負債は Codex 利用可能時に再開します。
> 巨大ジョブはハッシュ拘束された `semantic_plan.py` の map-reduce 経路を使い、実際の
> 各プロンプトを `900,000` 文字および UTF-8 バイト未満に制限します。ダッシュボードは
> 網羅、機械処理、意味要約、バックアップの負債を分離し、回復可能な滞留を
> `catching-up` と表示します。
> グローバル網羅状態は完全な有効化範囲からのみ更新され、単一 rollout の増分イベントが
> 全ソース状態を置き換えることはありません。
> 新規内容のない検証成功でも旧カーソルの識別・完了メタデータを収束させ、0バイト負債を
> 永続させません。
> この一度限りの収束は旧版で除外済みの subagent/exec カーソルも対象にしますが、
> その内容をトップレベル記憶へ取り込みません。
> Windows の意味ランタイム確認は、`~` を含む文字列をそのまま渡さず、展開済みの
> Codex 絶対パスを実行します。ランタイムにより意味要約が阻止された場合、
> ダッシュボードは秘匿化した原因を表示します。バックグラウンド実行系のリリースには、
> 実際のスケジューラ実行ユーザーで保留 `1 -> 0`、要約登録 `0 -> 1` を証明する合成 canary が必要です。
>
> **2.4.6:** この安定版では、デバイス間セマンティック・ランタイム契約、
> 明示的なローカル E5 実装、初回セマンティック索引の線形 raw ポインタ生成を
> 追加します。プラットフォームのランタイム、モデルキャッシュ、索引を
> コピーせず、共通インターフェースと固定依存関係を同期します。
>
> Windows v1.7.8 セキュリティ注記：デスクトップのダッシュボードショートカットは、
> コマンドライン引数なしで専用のコンソール非表示ネイティブランチャーだけを起動します。
> 検証済み Python と有効なアーカイブのパスはローカルの `.codex` 設定に保存され、
> `pythonw.exe` とスクリプトを長い引数で直接起動するショートカットは作成しません。
> パッケージ入口：`memory-wuxian-dashboard-launcher.exe`；ショートカット方針：
> `no command-line arguments`。
> Windows v1.7.9 では現在の Windows SID から実ユーザープロファイルを解決し、
> 隔離されたインストーラーが `CodexSandboxOffline` を起動先に書くことも防ぎます。
> Windows v1.7.10 は Python 起動時に検証済みの通常 Windows パスを維持し、
> 非 ASCII パスを拡張パスへ変換した際の即時終了を防ぎます。
> Windows v1.7.11 はダッシュボード起動と設定読込み時の可視コンソール子プロセスを
> 廃止し、永続スナップショットを先に表示してバックグラウンド更新し、変更値を
> 軽くアニメーション表示します。Windows のインストールと更新ではネイティブ
> デスクトップショートカットを既定で再作成します。Codex Skill の単純コピーには
> 通常のインストール画面がないため、初回有効化で付属の環境確認とショートカット
> インストール処理を実行します。
> Windows v1.8.0 では従来の PowerShell 収集ループも廃止し、コンソールなし
> の直接起動、イベント駆動更新、プロジェクト・ソース・デバイスのフィルター
> を追加しました。
> 実装契約は `/api/events`、`project-filter`、`source-filter`、
> `device-filter` です。リリース前に `references/release-rehearsal.md`
> に従って `scripts/run_release_rehearsal.py` を実行し、項目別の証拠を
> 保存する必要があります。

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Memory無限は、アクティブなコンテキストウィンドウを越えて、永続的・階層的・検証可能な会話記憶を構築するファイルベースのCodex Skillです。

インストール用Skill識別子は`memory-wuxian`、プロジェクト名と表示名は`Memory無限`です。正確な原文記録を履歴上の権威ある情報源とし、要約をナビゲーションに使い、過去の記述を検証済み事実として扱う前に原文へ戻って確認します。

## 機能

- タイムスタンプとSHA-256整合性フィールドを備えた追記専用Markdown会話記録
- 会話ごとに完全かつ自動更新されるMarkdown全文
- 並行タスクでも会話単位に分離された未完了ラウンドと返信関係
- 会話単位のレベル1要約と上位レベル要約
- ソース認識型の階層監査：レベル1は原文範囲、上位層は直接の子要約IDを検査
- 会話ごとのメッセージ、タイムライン、概念、要約インデックスとグローバルルーティングインデックス
- 完了した5会話ラウンドまたは20,000可視文字でスクリプトが要約境界を判定
- 完了ラウンドで要約が必要になった時だけ一時的にAI要約を生成
- 設定したラウンド数、利用率、圧縮しきい値に応じた有界の実行時コンテキスト更新
- インデックス優先検索と原文検証
- 明示的な改訂、撤回、再確認の系譜を持つ追記専用ポリシーイベント
- 明示的に置き換えられた規則を現行規則として提示しない `current-policy` 取得モード
- プレビュー優先の状態・インデックス復旧
- Heartbeatによる検証、保守、修復モード
- 安定したソースIDとセッション別カーソルによるCodex rolloutの増分解析
- 会話ごとのCodex報告Token使用量台帳とカウンターリセット対応の履歴バックフィル
- macOSネイティブLaunchAgentまたはWindowsタスクスケジューラによるイベント駆動同期
- SHA-256マニフェストと追記専用バックアップログを持つ最新デスクトップ検証スナップショット
- 派生ファイル再構築用の最新ワークスペース復旧バックアップ
- 差分バンドル、成果物台帳カーソル、デバイス間検索を備えたフェデレーション読取専用レプリカ
- SSHと暗号化クラウドフォルダーの並列フェデレーション転送
- デバイス間で規則とSkillを検証・収束させる独立Environment Registry
- ChatGPT公式エクスポートZIPと`conversations.json`向けの実験的ローカルアダプター
- データベースに依存しない透明なファイル構造

## インストール

### 単一ファイルインストーラー

最新のGitHub ReleaseからOSに対応するインストーラーをダウンロードします。

- macOS：`MemoryWuxian-<version>-macOS-universal.pkg`
- Windows：`MemoryWuxian-<version>-Windows-x64-Setup.exe`

ステータスコンソールは、最後に成功したブラウザローカル応答と、ソース検証済みの永続統計スナップショットから起動します。アーカイブが変わっていない場合は原文履歴全体を再読込せず、古い・破損したスナップショットだけを権威ある記録から自動再構築します。任意のローカル実績システムは、アーカイブ容量、アーカイブコンテキストとメッセージのみのToken推定、Codex報告の累計使用量、会話深度、プロジェクト成長、要約階層、原文検証済み検索を記録します。

インストーラーを開くと、現在のユーザーのCodexディレクトリにSkillを配置し、`Documents/MemoryWuxianArchive`を初期化して継続的なCodex収集を有効化します。再インストールやアップグレードでは設定とアーカイブを保持します。アンインストールはプログラムとバックグラウンド統合を削除しますが、会話履歴は残します。公開ビルドは、リリース処理に署名資格情報が設定されていない限り未署名のため、OSが明示的な確認を求める場合があります。

### Codex Skillインストーラー

GitHubディレクトリからSkillをインストールし、Codexを再起動します。

Skill ZIP 検証では、`/var`、`/tmp`、`/etc` が正確な `/private` 側の対象へ解決される場合に限り、固定 macOS system path aliases を許可します。それ以外のパッケージパスのリンクや junction は拒否します。

```text
$skill-installer install https://github.com/Sundried-calomel/memory-wuxian
```

手動インストールではリポジトリを次に配置します。

```text
~/.codex/skills/memory-wuxian
```

## クイックスタート

最初に[`SKILL.md`](SKILL.md)を読んでください。実際の会話履歴にはリポジトリ外のアーカイブルートを使い、ソース更新と個人記憶データが混ざらないようにします。

公式インストーラーは安定版を毎日確認します。更新処理はブランチ、ドラフト、プレリリースを無視し、プラットフォーム用インストーラーとSHA-256ファイルの両方を取得します。チェックサムやファイル名が一致しない更新は拒否します。Windowsは次回ログイン時に検証済み更新をサイレントインストールします。既存のmacOSインストールでは、検証済みPKGからSkill payloadだけを展開し、ロールバック可能なユーザー領域トランザクションを実行するため、システムインストーラーも管理者パスワードも不要です。完全なPKGは初回インストールと復旧用に残します。`python scripts/install_auto_update.py --uninstall`で確認を無効化できます。

Windowsではインストールまたは自動更新のたびに
`~/.codex/memory-wuxian-active-root.txt`で指定された実アーカイブを保持し、
ネイティブウィンドウ依存関係を確認または導入して、現在検証済みのPythonで
デスクトップの`Memory无限状态台.lnk`をアトミックに再構築します。Codex
ランタイム更新後も、無効になった古い`pythonw.exe`絶対パスを残しません。
インストーラーはSkillの実際の配置先から本来のユーザープロファイルを解決する
ため、デスクトップクライアントの隔離`USERPROFILE`がコレクターやショート
カットをサンドボックス側アーカイブへ向けることはありません。

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"

python3 scripts/memory_cli.py --root "$ARCHIVE" init
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker user --text "Hello"
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker assistant --text "Hello."
python3 scripts/memory_cli.py --root "$ARCHIVE" sync-codex --session-file "$HOME/.codex/sessions/.../rollout-....jsonl"
python3 scripts/memory_cli.py --root "$ARCHIVE" token-usage-backfill
python3 scripts/memory_cli.py --root "$ARCHIVE" token-usage-backfill --apply
python3 scripts/memory_cli.py --root "$ARCHIVE" status
python3 scripts/memory_cli.py --root "$ARCHIVE" backup
python3 scripts/memory_cli.py --root "$ARCHIVE" heartbeat --check-only
python3 scripts/memory_cli.py --root "$ARCHIVE" retrieve --query "要約トリガー" --mode current-policy
```

継続収集自体はモデルを呼び出しません。完了した会話ラウンドが設定しきい値に達した場合だけ、スクリプトがソース範囲を固定した要約ジョブを作成します。その後、一回限りのセマンティックworkerが認証済みCodex CLIを一時モードで呼び出し、制約されたJSON要約を取り込んで終了します。

## 実行時コンテキスト更新

Memory無限は代替タスクを新規作成せず、圧縮履歴を継続中のCodexタスクへ定期的に復元できます。`context-refresh-status`が完了ラウンド間隔、コンテキスト利用段階、圧縮を検出します。更新が必要な場合、`context-capsule`は有用な最高レベルのセマンティック要約を選び、親に包含された子要約を除外し、少量の直近会話を追加して一時的な派生コンテキストを生成します。`ack-context-refresh`は読み込み済みを記録し、重複注入を防ぎます。

カプセル予算はモデルのコンテキストウィンドウから計算し、既定値は1%、ソフト上限3,000 Token、絶対上限10,000 Tokenです。カプセルはナビゲーションであり履歴上の権威ではありません。事実は追記専用原文へ戻って検証し、カプセル自体を新しいソースメッセージとして保存してはいけません。再利用可能な`AGENTS.md`規則は`agents/`と`templates/`にあります。

## ポリシーの変遷

Level-1要約は、原文に明示されたポリシーイベントを`adopted`、
`revised`、`withdrawn`、`reaffirmed`、`proposed`、`uncertain`として
記録できます。改訂または撤回が有効な旧規則を置き換えるのは、同じ
スコープで旧ステートメントを正確に参照した場合だけです。新しいという
理由だけで有効性は変更されません。派生ポリシー索引は再構築可能で、
原文会話と既存要約は変更されません。

変更された可能性がある運用規則、既定値、戦略には
`retrieve --mode current-policy`を使用します。系譜と対応原文を返し、
より新しい一致原文も検索します。この機能以前の既存要約には、別途再解析
しない限りポリシーイベントがありません。その場合は、古い記述を現行規則
として扱わず、明示的な系譜が見つからなかったことを表示します。

## ローカルステータスコンソール

macOSでは、PKGのインストールまたは更新のたびに
`~/Desktop/Memory無限操作台.app`を再生成して置き換えます。ネイティブ
WebKitランチャーは`memory-wuxian-dashboard-launcher.json`を読み、
固定された端末固有パスではなく、現在のPython、Skill、保持された
アクティブアーカイブを使用します。`install_dashboard_app_macos.py`は
アプリのバージョン、署名、実行ファイルハッシュ、設定パス、セルフチェックを
検証します。ダッシュボードに影響するリリースは、このデスクトップアプリを
置き換えて正常に開くまで完了とはみなしません。

Windowsではローカルコンソールをネイティブアプリウィンドウとして起動できます。インストール済みMicrosoft Edge WebView2と同梱アイコンを使い、ブラウザ枠なしで完全なUIを表示します。

```powershell
python scripts/memory_dashboard.py `
  --root "C:\path\to\memory-wuxian-archive" `
  --config "C:\path\to\memory-wuxian\config.yaml" `
  --window
```

環境確認でオープンソース`pywebview`が不足している場合は、`scripts/bootstrap_windows.ps1 -InstallMissing`を一度実行します。中国語、英語、日本語UIを保持し、既定で30秒ごとに静かに更新します。会話ごとのCodexタイトル、メッセージ、完了ラウンド、要約レベル、日別アーカイブ量、保留要約、可視ソース文字数、明示されたアーカイブToken推定を表示します。文字数にはユーザーと可視アシスタント会話を含み、生成要約は含みません。Token推定はCJKを考慮したヒューリスティックで、課金使用量や要約生成消費ではありません。会話ごとの最新モデル要求Tokenと公称コンテキストウィンドウの比率も表示しますが、要求には指示、ツール、推論、出力が含まれ得るため100%を超える場合があり、正確な占有率や残量ではありません。

Windowsインストーラーは初回導入または更新のたびに
`scripts/install_dashboard_shortcut_windows.ps1`を実行し、現在のSkillパス、
有効なアーカイブ、同梱アイコン、検証済み`pythonw.exe`を使って
`Memory无限状态台.lnk`を再作成します。アンインストールではショートカット
だけを削除し、記憶アーカイブは削除しません。

コンソールはlocalhostだけにバインドし、外部サービスへアーカイブを送りません。通常の状態表示は読取専用です。「記憶検索」はCLIと同じ検証済み検索エンジンを使い、キーワード、多言語意味検索、ハイブリッドの各モードを提供します。各結果は人が読める原文、タイトル、日時、話者、原文行範囲、SHA-256バックリンクを保持します。設定画面の明示的操作では、暗号化クラウド交換の有効化・無効化、即時同期、選択したChatGPTエクスポートのローカル取込みができます。`--window`を使わない場合はクロスプラットフォームのブラウザモード、`--no-browser`はローカルサーバーのみ、`--port`はポート指定です。

ローカル読取専用APIは `/api/memory-search` で、モード値は `keyword`、
`semantic`、`hybrid` です。

## macOSでCodexを自動収集

SkillのインストールだけではCodexイベントを購読しません。Rustコレクターを一度ビルドし、LaunchAgentをインストールします。

```bash
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

LaunchAgentは最適化されたRustプロセスを維持し、OSのファイル変更通知と適応型size/mtime補助確認を使います。活発な時は5秒ごと、2分間アイドル後は30秒、15分間アイドル後は5分に低下し、ネイティブイベントは即時起動します。ユーザーメッセージ、可視assistant commentary/final、トップレベルCodexタイムラインの軽量ツール活動を保存します。利用可能な場合はツール名、ネストしたツール名、コマンド文を保持し、ツール出力、システム指示、隠れた推論、サブエージェント会話は除外します。トップレベルrolloutの`token_count`テレメトリは、会話ごとの派生台帳へ別途保存し、「Codex報告モデル使用量」と表示します。これは請求使用量ではありません。累積カウンターがリセットされた場合は前の区間を確定して加算し、重複スナップショットは要求数へ重複計上しません。キャッシュ入力と推論出力は内訳であり、`total_tokens`へ再加算しません。保持されたrolloutは正確にバックフィルできますが、削除済みテレメトリ、ChatGPT Web会話、公式ChatGPTエクスポートから実際のモデル使用量は復元できません。セッション別カーソルと安定ソースIDにより再試行は冪等です。

ネイティブコレクターはイベント駆動JSONL解析、原文追記、会話別全文、決定的ルーティングインデックス、カーソル、期限到来レベル1ジョブ、デスクトップスナップショットを直接担当します。成功したCodexファイル編集は、パス、変更種別、移動先、追加・削除数、hunk行範囲、正確なunified diffを記録します。一般ツール出力と隠れた推論は除外します。既存インストールはpatchイベント履歴を一度だけ補完します。ジョブ期限時にはPython wrapperが一時Codex CLI要約プロセスを一度起動し、取込み後に終了します。Python CLIは低頻度の保守、検索、再構築、要約取込みに使います。

各会話は`memory/conversations/`に個別保存され、一つのconversation IDだけを含みます。機械可読レコードと可読メッセージの両方を保持し、個別インデックスは`memory/indexes/by-conversation/<conversation>/`にあります。`raw/`の不変ファイルが権威ある情報源で、全文とインデックスは再構築可能な決定的ビューです。

保護された`Documents`や`Desktop`にアーカイブまたはバックアップを置く場合、macOSで`bin/memory-wuxian-collector`にフルディスクアクセスを付与します。自動収集が有効と判断する前に、生成plist内の実行ファイルを確認してください。バックグラウンド定義は`/opt/homebrew/bin/python3`のような安定したPythonエントリを保持し、バージョン固有のHomebrew Cellarパスへ解決しません。通常のPython更新で新しいプライバシーIDが作られ、DesktopやDocumentsの許可が繰り返し要求されることを防ぎます。

コレクターは`imports/codex/collector-telemetry.json`へ軽量テレメトリーを公開します。コンソールはactive、idle、deep-idle、補助確認間隔、最新ファイルイベント、最新アーカイブ書込、1時間の起動回数、CPU/メモリを表示します。新しいプロセスはまず`phase=starting`と`ready=false`を報告し、初期同期が成功した後だけ`phase=ready`になります。アイドル中も各監視間隔で更新し、source watermarkとarchive watermarkを別々に保持します。起動処理中、テレメトリーの期限切れ、コレクター停止、またはsourceがarchiveより先行した場合、コンソールが警告します。

既存のmacOSインストールは`scripts/install_macos_transaction.py`で更新します。候補をステージし、隔離アーカイブで合成ユーザー/assistantメッセージを正確に取得できることを証明してから切り替えます。切替後は新しいコレクターPID、新鮮なテレメトリー、現行ダッシュボードの自己診断を確認します。切替後の失敗では旧Skill、LaunchAgent、コレクターを復元します。通常更新はこのユーザー空間トランザクションを使い、完全インストーラーや管理者パスワードを必要としません。

ファイル切替前に、トランザクションは共有アーカイブロックを待ち、ネイティブ復旧債務がないことを確認し、そのロックを保持したまま旧コレクターを停止します。代替コレクターを起動する前にロックを解放し、代替コレクターがreadyを報告した後だけ定期メンテナンスを読み込みます。このアイドル境界での引継ぎにより、処理中の書込やメンテナンス競合で通常更新が全履歴復旧監査へ変わることを防ぎます。引継ぎまたは最初のディレクトリ切替に失敗した場合、旧コレクターを直ちに復元します。

コレクターの初回同期はAI要約を待ちません。起動中の追随処理が要約しきい値に達した場合、不変の要約ジョブを永続化し、コレクターがreadyになった後で既存のsemantic-backfill workerに処理させます。これにより、原文と要約待ちを失わず、長時間のCodex CLI呼び出しがトランザクション切替を妨げません。

時間範囲付きレポートでMemory无限を使う前に、`scripts/archive_waterline.py --cutoff <ISO-8601>`を実行します。レポート締切までの保持済みsourceが永続cursorで覆われていることを検証します。`--backfill`は明示的に指定し、遅延と判定された保持sourceだけに限定されます。最終結果が`covered`になるまでレポートを続行しません。

日次アーカイブ棒グラフの高さは従来どおり文字数です。マウスでホバーするかキーボードでフォーカスすると、完全な日付、正確なアーカイブメッセージ数、正確な可視文字数を示すローカライズされたバブルが開きます。

## ChatGPT会話のインポート

通常のChatGPT会話はCodex rolloutストリームに含まれません。公式ChatGPTデータエクスポートZIP、展開済みディレクトリ、または`conversations.json`をインポートできます。

```bash
python3 scripts/memory_cli.py import-chatgpt --export /path/to/chatgpt-export.zip
```

`--conversation-id <native-id>`を複数指定して会話を選択できます。インポーターは現在の可視ブランチをたどり、systemメッセージと破棄された再生成ブランチを除外し、タイトルと安定IDを保持します。同じ、または新しいエクスポートを再取込みしても重複しません。会話IDは`chatgpt:<conversation-id>`となり、通常のバックアップ、インデックス、要約、検索、コンソール処理に入ります。これはエクスポートアダプターであり、リアルタイムChatGPTリスナーではありません。

同じアダプターは「コンソール > 設定 > ChatGPT会話をインポート」にあります。選択したZIPまたはJSONはlocalhostサーバーだけへストリーム送信され、既存インポーターで解析後、一時保存から削除されます。Memory無限はChatGPTへログインせず、アカウント資格情報を要求せず、他サービスへエクスポートをアップロードしません。

この機能は**実験的**です。自動テストは合成ZIP/JSON、可視ブランチ選択、重複防止、安定ID、ローカルコンソールアップロードを検証しています。実際のユーザーによるChatGPT公式エクスポートはまだ提供されていないため、**実データでは未検証**です。エクスポート形式は変更され得るため、最初の実データ取込みは検証実行として扱い、件数と復元会話を確認してください。

## WindowsでCodexを自動収集

最初に環境ブートストラップを実行します。Pythonバージョンと、Python、Codex CLI、同梱コレクター、Codexセッションのパスを報告します。メインランタイムは Python 3.14.x のみをサポートします。`-InstallMissing`は、サポート対象ランタイムも互換性のあるCodex同梱Pythonもない場合だけ Python 3.14 をインストールします。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_windows.ps1
```

リリースには`bin/memory-wuxian-collector.exe`が含まれるため、RustとVisual C++ Build Toolsは開発時だけ必要です。ネイティブソース変更時のみ再ビルドし、ユーザー単位の起動統合をインストールします。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_native_collector.ps1
python scripts/install_codex_autosync_windows.py `
  --archive-root "$PWD\memory" `
  --python-executable "C:\path\to\python.exe" `
  --codex-cli "C:\path\to\codex.exe" `
  --load
```

タスクはログオン時に開始し、`--load`でも直ちに開始します。ローカルポリシーがタスク登録を拒否する場合、インストーラーは現在ユーザーの`Run`レジストリへ、エンコード済みの非表示再起動コマンドを登録します。永続helperスクリプトは不要です。アーカイブは選択したワークスペースルートに残り、Windowsネイティブ監視、5秒size/mtime補助確認、アーカイブロック、セッションカーソル、要約トリガー、セマンティックworker、検証済みデスクトップスナップショットを使います。`python scripts/install_codex_autosync_windows.py --archive-root "$PWD\memory" --uninstall`で削除できます。

選択したアーカイブは`~/.codex/memory-wuxian-active-root.txt`にも記録されます。`--root`を省略したCLI検索・保守はそのアーカイブを使い、インストールSkill内の空テンプレートを実データと誤認しません。`--root`と`MEMORY_WUXIAN_ROOT`は明示的な上書きです。

検索はアーカイブの排他的書込ロックを取得しません。現在のCodexワークスペースが読取可能でも書込不可の場合、検索は成功し、`last-query.md`と検索ログ更新だけを省略します。

コレクターは16 MiB workerスタックを明示し、Windowsで大規模な初回全履歴を安全に解析・索引化します。

既定設定では、ネイティブのメモリ変更ごとに主アーカイブ書込後、`pending/backup-debt.json`をアトミックに更新します。低頻度の保守タスクが保留中の変更を1件の完全な検証済みスナップショットとして`~/Desktop/Memory無限-记忆归档备份/`へまとめ、成功後だけ債務を消去して旧スナップショットを削除します。コレクターはアーカイブ全体のコピーで起動や収集を停止しません。バックアップルートには最新復旧コピー1件と追記専用`backup-log.jsonl`が残り、より新しいスナップショットが保留中の場合はステータス画面が警告します。

適用型再構築コマンドは以前の派生ファイルを`memory/archive/`に保存できます。内部復旧コピーは`backup.workspace_retention_count`に従い、既定で最新1件だけ保持します。開発編集は置換可能なコードバックアップ1件を使い、ライブ会話アーカイブを追加複製しません。

## メモリ階層

```text
原文会話記録
  -> 会話別の完全全文
  -> 会話別インデックス
    -> 完了ラウンドまたは文字しきい値後の会話別AIレベル1要約
      -> 固定数の子要約から作る会話別上位要約
        -> グローバルルーティングインデックス
          -> 検索された原文証拠
```

既定しきい値は設定可能です。初期実装は主観的重要度スコアと長期ユーザー嗜好の自動推定を意図的に避けます。

レベル1境界は会話ごとに完了5ラウンドまたは20,000可視文字の早い方です。回答途中で20,000文字を越えると期限を記録しますが、その回答の`final_answer`でラウンドが完了するまでソース範囲を閉じません。スクリプトは正確な範囲、ハッシュ、件数、正規化ルーティング抜粋を保存し、一時AI workerだけがトピック、結論、未解決事項、概念を生成します。

インストール設定では自動セマンティック要約ジョブと一回限りworkerが有効です。期限外にAIプロセスは常駐しません。しきい値変更時も既存ジョブの不変ソース範囲を密かに書き換えません。

## フェデレーションメモリ

1.6.0以降、各デバイスのローカルアーカイブはそのデバイスだけが書き込みます。新しい原文、要約、確認済みタイトルを`.mwxb`差分バンドルとして出力し、信頼済みピアは既定の同階層ディレクトリに読取専用レプリカとして取り込みます。

```text
<archive>-federation-cache/
├── peers/<origin-node-id>/
└── global-index/
```

ピア記録は受信側ローカル`raw/`、`state.json`、ラウンド数、要約数へ入りません。再構築可能なピアインデックスは識別子を由来ノードで限定し、`retrieve-global`が検索時にローカル権威と統合します。`retrieve`はローカル専用です。

二つのノードを初期化してオフライン差分を交換します。

```bash
python3 scripts/memory_cli.py --root /path/to/node-a init-node --display-name "Node A"
python3 scripts/memory_cli.py --root /path/to/node-b init-node --display-name "Node B"
python3 scripts/memory_cli.py --root /path/to/node-b add-peer --node-id <node-a-id>
python3 scripts/memory_cli.py --root /path/to/node-a export-delta \
  --output /trusted/path/node-a-0001.mwxb \
  --target-node-id <node-b-id>
python3 scripts/memory_cli.py --root /path/to/node-b inspect-bundle \
  --bundle /trusted/path/node-a-0001.mwxb
python3 scripts/memory_cli.py --root /path/to/node-b import-delta \
  --bundle /trusted/path/node-a-0001.mwxb \
  --expected-node-id <node-a-id>
python3 scripts/memory_cli.py --root /path/to/node-b retrieve-global \
  --query "earlier topic"
```

成果物台帳は、元メッセージ範囲より後に作られたローカル権威要約・タイトルも検出します。取込みは成果物SHA-256を検証し、イベントシーケンスの欠落・重複を拒否し、非初回バンドルに取込み済み直前バンドルのSHA-256を要求します。受理済みバンドルの再取込みは冪等です。`revoke-peer`は今後の取込みとSSH pullを停止しますが、既存履歴を削除しません。

大きな未送信履歴は有界で連続したページとして出力します。`has_more`が真なら、返された`to_event_sequence`とバンドルSHA-256を次のカーソルと先行ハッシュに使います。中断した状態キャッシュは追記専用成果物台帳から再構築できます。

SSHピアを登録して次の差分を取得します。

```bash
python3 scripts/memory_cli.py --root /path/to/local add-peer \
  --node-id <remote-node-id> \
  --host user@example-host \
  --remote-root /path/to/remote/archive \
  --remote-config /path/to/remote/config.yaml \
  --remote-cli /path/to/remote/scripts/memory_cli.py \
  --remote-shell posix
python3 scripts/memory_cli.py --root /path/to/local sync-peer \
  --node-id <remote-node-id>
```

Windowsピアは`--remote-shell powershell`を使います。SSHは厳格なホスト鍵確認と設定済みユーザー資格情報で接続を暗号化・認証し、接続とコマンドに上限時間を設けます。`.mwxb`自体は圧縮のみで暗号化も暗号学的署名もないため、オフラインバンドルは信頼できる経路だけで転送してください。

フェデレーションはMemory無限のノードIDと明示的ピア記録を使用し、OpenAIセッション、Codex資格情報、OpenAIデバイスIDを再利用しません。再構築可能なキャッシュはデスクトップ主アーカイブバックアップから除外します。1.6.0にはインターネット自動探索、NAT traversal、モバイルクライアントはありません。

## 暗号化クラウドフォルダー交換

1.6.0では、ユーザー指定のiCloud Drive、OneDrive、互換同期フォルダーを使う非同期転送を追加しました。Memory無限はプロバイダー資格情報を受取・保存しません。内部`.mwxb`を送信元Ed25519鍵で署名し、対象デバイスへage/X25519で暗号化した対象別`.mwxe`エンベロープだけを書き込みます。

各デバイスの秘密IDはアーカイブ、レプリカキャッシュ、同期フォルダー外に保存します。ペアリングファイルには公開鍵とフィンガープリントだけが含まれます。取込み前に信頼できる経路で指紋を比較してください。

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"
SHARED="$HOME/Library/CloudStorage/OneDrive-Personal"

python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-configure \
  --directory "$SHARED"
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-pair-export \
  --output /trusted/path/this-device-pairing.json
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-pair-import \
  --pairing-file /trusted/path/other-device-pairing.json \
  --expected-fingerprint <fingerprint-shown-on-the-other-device>
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-sync --force
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-status
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-disable
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-enable
```

誤入力で未同期のローカルフォルダーを作らないよう、選択ディレクトリは既に存在する必要があります。Windowsではエクスプローラーに表示されるローカルOneDriveまたはiCloud Driveを選びます。
クラウドプロバイダーのルートまたは既存の
`MemoryWuxianExchange` 子ディレクトリのどちらを選んでも、同じ
`<provider-root>/MemoryWuxianExchange/v1` キューへ正規化されます。これにより、
ペアリング済みデバイスが異なる入れ子のキューを走査する状態を防ぎます。

設定後、5分ごとの短時間タスクを登録します。

```bash
python3 scripts/install_cloud_sync.py \
  --archive-root "$ARCHIVE" \
  --skill-root "$HOME/.codex/skills/memory-wuxian" \
  --python-executable "$(command -v python3)" \
  --load
```

タスクは起動ごとに利用可能なピアエンベロープを取り込みます。通常変更は15分まとめられ、約1 MiBの保留データで早期送信でき、最古の変更は60分後に送信を試みます。これはローカル同期フォルダーへの書込タイミングであり、ネットワーク送信はプロバイダークライアントが制御します。空確認はファイルを作らずAIも呼びません。

クラウドフォルダーは共有書込アーカイブではなく転送キューです。各ノードは自分のoutboxとackだけを書きます。取込み履歴は読取専用ピアレプリカに入り、`retrieve-global`はSSH・クラウドとも同じ検証ソース経路を使います。`cloud-disable`はアーカイブ、鍵、暗号化クラウドファイルを削除せず交換を停止します。

macOSでは、OneDrive Files On-Demandのエンベロープがディレクトリに表示されても、まだローカルで読めない場合があります。Memory無限は復号前のプローブで有界な取得を開始し、一時的なFile Provider可用性エラーを破損ではなく再試行可能として扱います。`environment-v1`で送信側が過去のカーソルから広い範囲を再送した場合、永続化済みの接頭イベントがすべて完全一致するときだけ安全に続行し、競合する重複は引き続き失敗閉鎖で隔離します。

1.6.1からこれらの操作はコンソール設定画面にもあります。クラウド同期スイッチは暗号化交換と5分タスクを同時に制御し、「今すぐ同期」は即時交換を1回実行します。設定済みフォルダーとタスク状態を表示するため、通常操作にAI会話や端末コマンドは不要です。

## プロジェクト証拠パッケージ

Memory Wuxian は、明示的かつ限定されたプロジェクト規則、現状、次の計画、
意思決定、QA、レポート、テンプレート、小型の補助成果物を保存・交換できます。
ワークスペース全体を走査またはアップロードしません。各パッケージは正確な
バイト列と SHA-256 を保持し、必要な場合は前世代を参照する不変世代です。
ソースルートのパスは保存されず、秘密情報の疑いがあるテキストは拒否され、
ピアコピーは常に読み取り専用です。

プロジェクト証拠は、署名済み・対象暗号化済みの独立した
`project-evidence-v1` ストリームを使います。`archive-v1` と
`environment-v1` は変更されないため、旧クライアントは新ストリームを安全に
無視できます。ダッシュボードはパッケージ数とストリームカーソルを表示します。
限定検索には `project-evidence-query`、完全な正確バイトの取得には
`project-evidence-reconstruct` を使います。詳細は
[プロジェクト証拠契約](references/project-evidence.md)を参照してください。

```text
project-evidence-build
project-evidence-list
project-evidence-query
project-evidence-reconstruct
project-evidence-status
project-evidence-owner-register
project-evidence-owner-refresh
project-evidence-owner-status
```

デバイスローカルの Project Evidence Owner は、明示された閉じたファイル選択を
維持できます。5分ごとのモデル非依存タスクは1回に最大20 ownerを更新します。
変更がなければ書込みはなく、安定した変更だけが前世代リンク付き不変世代を
作成します。ソースパスはローカルに留まり、失敗はowner単位で分離され、peer
証拠からローカルownerが自動作成されることはありません。

## Memory無限 2.0 の環境収束

2.0では、グローバル規則、プロジェクト規則、グローバルSkill、
プロジェクトSkill用の独立した第2同期プレーンを追加します。複数デバイスを
一つの共有書込アーカイブにはしません。各デバイスは自身のローカル会話権威を
維持し、他デバイスの会話は検証済み読取専用レプリカとして扱います。

環境成果物は不変のコンテンツアドレス付きリビジョンと明示的なノードローカル
バインディングを使います。選択したクラウドディレクトリには、独自のイベント
連番、前段チェーン、カーソル、確認、ステージング、検証済みSkillパッケージを
持つ、署名済み・対象暗号化済み`environment-v1`ストリームだけを置きます。
5分タスクはAIを使わず受信内容を決定論的に検証します。転送は更新を
ステージングするだけで、Skillのインストールや規則の書換えは行いません。
2.4.1以降、同一バッチ内の各登録項目は個別の安定したエクスポートIDを持ち、
プロジェクト登録も同期されます。受信したプロジェクトは読み取り専用のpeer
メタデータとして保存され、ローカルで自動作成・有効化されません。Skill
パッケージは安全な完全YAMLパーサーを使用し、正当な入れ子構造、リスト、
ブロック文字列を許可しつつ、重複キーと危険なタグを拒否します。インストーラー
は必要なPyYAML 6.x依存関係を提供します。

互換性のあるグローバル規則のfast-forwardは、そのポリシーを明示的に有効化
した場合だけ登録できます。プロジェクト成果物、Skill、分岐、ID変更、権限拡大、
永続コンポーネント追加、実行環境非互換は常にレビュー対象です。インストーラーは
変更前にロールバック材料を永続化し、アトミック切替、インストール後検査、
証拠レシート追記を行います。プロジェクト能力のグローバル昇格は、完全な
プラットフォームマトリクス、出典証拠、明示承認を必要とする別の手順です。

検証済みのローカルなアーキテクチャ知見は、不変のガバナンス提案として
記録できます。ペアリング済みデバイスは同じ署名済み・対象暗号化済み
Environmentストリームで提案を交換しますが、受信提案は読取専用の証拠の
ままです。`work-system-governor`による分類と検証、明示承認が完了するまで、
新しい規則やSkillリビジョンにはなりません。

証拠に結び付いた製品進化記録は、範囲を限定した開発履歴、検証済みの現状、
修正後の次回開発フロー、再利用可能な教訓候補を保存できます。交換後も
読取専用（read-only）の証拠であり、製品修復やグローバルガバナンス受入れを自動実行
しません。決定論的タスクが変更証拠を収集してキュー化し、AIは限定された
意味レビューが必要な時だけ呼び出されます。

ダッシュボードのEnvironmentビューでは、インベントリ、受信判定、競合、
昇格候補、手動更新確認を表示します。2.0の完全なCLIコマンド群は次の通りです。

```text
environment-init
environment-scan
environment-status
environment-list
environment-projects
environment-show
environment-diff
environment-register
environment-validate
environment-export-delta
environment-exchange-status
environment-profile-capture
environment-profile-status
environment-profile-current
environment-profile-rebuild-current
environment-profile-compare
environment-convergence-plan
environment-incoming-status
environment-process-incoming
environment-accept-incoming
environment-bindings-status
environment-register-root
environment-register-project-binding
environment-register-rule-binding
environment-register-project-rule-binding
environment-register-skill-binding
environment-discover
environment-install-rule
environment-recover-rule-installs
environment-install-skill
environment-recover-skill-installs
environment-conflict-assess
environment-conflicts
environment-conflict-resolve
environment-promotion-propose
environment-promotion-transition
environment-promotions
environment-governance-propose
environment-governance-proposals
environment-product-evolution-record
environment-product-evolution-records
environment-governance-ai-discover
environment-governance-ai-status
environment-governance-ai-enqueue
environment-governance-ai-configure
environment-governance-ai-tick
```

### 制限付きガバナンス AI

Memory無限は、AI会話を常時起動せずに意味処理タスクをキューできます。
スクリプトが5分ごとにモデルを使わず発見と期限判定を行い、互換性のある
マイクロバッチが期限に達した場合だけ一回限りのCodex workerを起動します。
製品バッチは3件または6時間（最大5件）、ガバナンス分類は同一ownerの5件
または24時間（最大10件）で起動し、1バッチ80,000文字、1日6回を上限と
します。緊急項目は件数と経過時間のしきい値を迂回できます。

この機能は既定で無効です。製品タスクは発生元デバイスだけで実行し、
グローバル分類には明示的な調整デバイスが必要です。すべての結果は厳密な
schema検証を通した人間レビュー待ちの草案です。workerは規則の承認、
Skillのインストール、製品修復、履歴書換えを実行できません。

### 説明可能な設定とデバイス互換性

Memory無限は既存YAMLを閉じた決定的なconfiguration-v1ビューへ
コンパイルしますが、元ファイルを変更せず、アーカイブも初期化しません。
各有効値は由来レイヤーを持ち、有効設定全体には安定したSHA-256があります。
未知キー、重複キー、無効な型、範囲外の値は失敗として閉じます。

`environment-capability-status`は製品、プラットフォーム、ランタイム、
プロトコル、インターフェースの互換性だけを報告します。能力オファーのない
旧デバイスは診断状態のまま既存同期を妨げません。互換判定がインストール、
信頼、権限、同期権限を与えることはありません。コンソールの「システム」
（System）
タブも同じ読取専用情報を表示します。

```bash
python3 scripts/memory_cli.py configuration-compile
python3 scripts/memory_cli.py configuration-explain
python3 scripts/memory_cli.py environment-capability-status
python3 scripts/memory_cli.py environment-capability-status --peer-offer /path/to/peer-offer.json
```

## プライバシーと統合境界

- 個人アーカイブはリポジトリ外の`--root`を使います。
- 同梱`memory/`内の可変ファイルは`.gitignore`対象です。
- 明示設定時にCLIは明らかな秘密をマスクできますが、保存可否の判断はユーザー責任です。
- 自動収集には同梱LaunchAgent、Windowsタスク、または明示設定したクライアントフックが必要です。
- オフライン`.mwxb`には読取可能な履歴が含まれます。SSHまたは信頼経路を使ってください。SHA-256は暗号化や送信者認証ではありません。
- クラウドフォルダーには署名済み・対象暗号化済み`.mwxe`と暗号化ackだけが入り、デバイス秘密IDは入りません。

## 完全な保守コマンド一覧

前述のクイックスタートは通常運用を説明しています。リリース時に未文書化の
コマンドが追加されないよう、公開保守コマンドをすべて明示します。

v1.7.4 以降、プルリクエストとインストーラーのリリースではリポジトリ内の
文書契約を実行します。機能変更では3言語のREADME、`CHANGELOG.md`、
およびレビュー済み機能契約を同時に更新する必要があります。

v1.7.5 以降、親プロセスが `PYTHONIOENCODING` で GBK などの旧式エンコー
ディングを指定しても、Windows CLI のリダイレクト出力は常に UTF-8 です。
旧式の対話コンソールでは未対応文字だけをエスケープし、メモリ操作は停止
しません。

v2.4.2以降、Windowsネイティブ状態画面ランチャーは未使用のループバック
ポートをOSに要求し、実際に割り当てられたポートを開きます。8765を前提と
しないため、別のローカルアプリが先に8765を使用してもMemory無限の画面を
置き換えることはありません。

v2.4.3以降、[`PRODUCT_ARCHITECTURE.md`](PRODUCT_ARCHITECTURE.md)を
モジュール境界の唯一の正本とし、
[`docs/module-architecture.json`](docs/module-architecture.json)を
機械可読な所有権台帳とします。各本番ファイルは必ず一つのモジュールだけに
所属し、`scripts/check_architecture_contract.py`は未登録、重複所有、および
禁止依存を拒否します。WindowsとmacOSのパッケージにこれらのゲートが
含まれない場合、リリースは失敗します。

```text
init
append
sync-codex
import-chatgpt
status
context-refresh-status
context-capsule
ack-context-refresh
backup
make-summary-job
ingest-summary
retrieve
conversation-tail
register-title
rebuild-state
rebuild-conversations
rebuild-indexes
index-generation-build
index-generation-status
index-generation-activate
index-generation-rollback
heartbeat
rebuild-deterministic-indexes
init-node
add-peer
revoke-peer
export-delta
inspect-bundle
import-delta
rebuild-global-index
retrieve-global
federation-status
sync-peer
cloud-configure
cloud-pair-export
cloud-pair-import
cloud-sync
cloud-status
cloud-enable
cloud-disable
configuration-compile
configuration-explain
environment-capability-status
```

セマンティック要約を手動復旧する場合は、さらに `semantic_worker.py` と
`semantic_backfill.py` を使用します。コミット前に文書契約を実行できます。

```bash
python3 scripts/check_documentation_contract.py
```

## 開発

バイトコードを生成せず機能テストを実行します。

```bash
$HOME/.cargo/bin/cargo test --locked --manifest-path native-collector/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

候補 CI は機能ブランチでは pull request のみ、`push` では `main` のみを
実行します。Ubuntu と Windows は job ごとに完全テストを一度だけ実行し、
macOS は pull request でプラットフォーム固有契約、`main` で完全テストを
実行します。完全テストで証明済みのリハーサル項目は
`--reuse-unittest-evidence` により個別のハッシュ付き参照ログを保持し、
同じモジュールを再実行しません。インストーラー公開は同一 SHA の
`main` 成功結果を使用します。契約を削除せず、重複実行だけを減らします。

設計判断と実装契約は[`PROJECT.md`](PROJECT.md)と[`references/`](references/)に、変更履歴は[`CHANGELOG.md`](CHANGELOG.md)にあります。`README.md`、`README.zh-CN.md`、`README.ja.md`は一つの文書契約として管理し、記載動作が変わる場合は同時に更新します。

## バージョン別実行ロードマップ

[`references/version-roadmap-v2.5-to-v3.0.md`](references/version-roadmap-v2.5-to-v3.0.md)
は v2.6 から v2.10 までの順序付き実装権限です。各バージョンは公開前に、
直前バージョンのリリース・復旧証拠、境界付き作業契約、同一候補 SHA に対する
macOS・Windows ゲート、実証済みロールバック経路を必要とします。Personal
Environment の収束は v2.10 に限定され、v3.0 は別途承認された非互換の公開契約
変更がある場合だけ検討します。

### v2.6 索引安全性

`index-generation-build` は、正確な SHA-256 で検証した raw・要約ソースの
マニフェストから不変のシャドー世代を作成し、現在の索引ファイルを変更しません。
`index-generation-status` は閉じたマニフェストと payload を検証します。
`index-generation-activate --generation-id <id>` は `--apply` が指定される
までプレビューのみで、`index-generation-rollback` も前のポインターを表示して
からポインターだけを原子的に戻します。固定 v2.6 検索ベンチマークはコーパスの
ハッシュ、ポリシー系譜、正確な曖昧性解消ケースを保持し、説明のない結果差分を
拒否します。raw 履歴の変更や受信索引の自動有効化は行いません。

## ライセンス

Memory無限は[MIT License](LICENSE.txt)で公開されています。
## v1.9 の保護された移行機能

`migration-preview` は保存先容量と不変のソースマニフェストだけを確認します。
`migration-apply` は検証済みコピーのみを行い、元アーカイブを削除しません。
3 つのマニフェストが一致し、`--switch-active` が明示された場合だけ
アクティブルートを切り替えます。`project-package-export` は会話 ID ごとの
可読パッケージを作成し、`project-package-import` はローカル raw 履歴の外に
読み取り専用レプリカとして検証・保存します。
## v1.10 履歴ビュー

`as-of` はタイムゾーン付き時刻における読み取り専用履歴を再構成します。
`decision-graph` は明示的なポリシーイベントから規則・決定系譜を派生し、
`raw_sources` にメッセージ ID、raw パス、レコードハッシュを保持します。
グラフが履歴を上書きすることはありません。
## v1.11 検索品質と任意のローカル意味索引

`retrieval-evaluate` は可読 JSONL テストセットで recall-at-k、誤引用数、
レイテンシを測定します。`semantic-index-build` の既定
`local-hash-v1` は完全オフラインで、モデルをダウンロードせず外部サービスも
利用しません。多言語ニューラル検索を使う場合は
`python scripts/install_multilingual_e5.py` を実行してから
`semantic-index-build --provider multilingual-e5-small` を実行します。
任意の 384 次元 `intfloat/multilingual-e5-small` ONNX モデルは不変リビジョンと
正確な SHA-256 に固定され、隔離環境でリモートモデルコードを無効化し、
推論を強制的にオフラインで行います。Windows の隔離環境は Python 3.12 に
固定され、中国語を含む Skill、アーカイブ、worker、索引パスを扱えます。
`semantic-retrieve` は raw SHA-256 を再検証し、会話・
メッセージ ID、raw パス、正確な行範囲を返します。
`semantic-index-clear` は再構築可能なベクトルだけを削除します。

E5 インターフェースは、独立した Environment Registry に不変の
`global-runtime-contract` として登録できます。

```bash
python scripts/memory_cli.py semantic-runtime-status
python scripts/memory_cli.py environment-register-semantic-runtime \
  --origin-node-id <node-id> --apply
python scripts/memory_cli.py environment-realize-semantic-runtime
python scripts/memory_cli.py environment-realize-semantic-runtime --apply
```

## v2.7 バックグラウンド自律処理と診断

Memory無限は、モデルを呼び出さない保守処理を、安定した冪等キー、リース、回数制限付き
再試行、再起動復旧、`quarantined` 隔離状態を備えた閉じた永続キューに保存します。
`maintenance-status` は collector と worker の期待状態と実状態を比較し、
`maintenance-diagnostics` は原文会話、認証情報、ローカルユーザーパスを含まない
秘匿化診断バンドルを生成します。完全な会話境界が `semantic_dispatch.py` により
`semantic-ready` になった場合だけ、既存の一回限りの AI worker が実行されます。
機械的 tick は AI を呼び出さず、要約失敗はネイティブ収集を停止しません。

```powershell
python scripts/memory_cli.py maintenance-enqueue --kind archive-health --idempotency-key health:manual
python scripts/memory_cli.py maintenance-requeue --job-id job-<sha256> --reason "worker contract upgraded"
python scripts/memory_cli.py maintenance-status
python scripts/memory_cli.py maintenance-tick --maximum-jobs 20
python scripts/memory_cli.py maintenance-diagnostics
```

## v2.8 ロスレスシャドウ保存と再開可能転送

任意の `exact-byte` シャドウストアは、`shadow-content-v1` にコンテンツアドレス
オブジェクトと閉じた順序付きマニフェストを保存します。各項目は安定したソース ID、
相対パス、バイト長、ファイル全体の SHA-256 を保持します。構築、復元、無効化、転送は
既定でプレビューです。ドメインごとの `checkpoint` は連続して検証済みの範囲だけを
再開し、重複再送は冪等です。欠落、重複範囲、破損、改ざん、宛先競合は明示的な説明と
ともに失敗します。シャドウ領域を削除しても、原文履歴と既存の `archive-v1`、
`environment-v1` ストリームは変わりません。

```powershell
python scripts/memory_cli.py content-shadow-build --source-root C:\snapshot --source-id node:snapshot --file raw/a.md
python scripts/memory_cli.py content-shadow-status
python scripts/memory_cli.py content-shadow-verify --manifest-id <manifest-id> --source-root C:\snapshot
python scripts/memory_cli.py content-shadow-reconstruct --manifest-id <manifest-id> --destination C:\restore
python scripts/memory_cli.py content-shadow-disable
python scripts/memory_cli.py content-transfer --manifest-id <manifest-id> --target-archive-root C:\target --domain archive --target-id <node> --start 0 --count 100
```

## v2.9 統一読み取り専用アクセスと更新ガバナンス

`readonly-query`、`readonly-http`、`readonly-mcp` は、同じ有界サービスと
`memory.query` 契約を共有します。結果には信頼度、正確な raw 出典、SHA-256、
raw 検証状態が含まれます。HTTP は GET のみを受け付け、ループバックだけに
バインドします。MCP が公開するのは一つの読み取りツールだけで、書き込み、
インストール、ペアリング、任意パス、コマンド、遠隔操作は公開しません。
セマンティック索引が古い、または利用不能な場合、hybrid は raw 検証済みの
キーワード検索へフォールバックします。

```powershell
python scripts/memory_cli.py readonly-query --query "以前の決定" --mode hybrid --limit 20
python scripts/memory_cli.py readonly-http --host 127.0.0.1 --port 8766
python scripts/memory_cli.py readonly-mcp
python scripts/memory_cli.py summary-budget-status --metrics-json metrics.json --policy-json policy.json
```

## v2.10 個人 Environment の収束

2.10 では、明示的に指定したグローバル Rule ファイルとインストール済み Skill のルートを、
決定的でデバイスパスに依存しない Profile として棚卸しできます。Profile が保持するのは、
安定したインストール ID、provider、宣言バージョン、正確なツリーまたはファイル SHA-256、
件数、バイト数、対応プラットフォーム、Memory 無限の管理 Rule ブロック ID だけです。
ソースパス、ユーザー名、ホスト名、資格情報、環境変数値、キャッシュ、モデル、アーカイブ、
会話、インデックスは保存しません。

キャプチャは既定でプレビューのみです。`--apply` を指定した場合だけ、前世代に連結された
不変世代を作成し、再構築可能な current ポインターを原子的に更新します。変更がなければ、
世代もエクスポートイベントも重複しません。既存の `environment-v1` は信頼済み peer にだけ
世代を送り、受信側では `automatic_activation=false` の読み取り専用 replica として保持します。

比較結果は `same`、`missing-local`、`missing-peer`、`content-differs`、
`platform-inapplicable`、`inventory-incomplete` の6種類です。収束計画は有界なプレビューに
限られ、system-bundled と plugin-managed の Skill は provider 参照のままです。正確な既存の
不変 Environment artifact がない項目は `evidence-only` となり、Profile 自体が Rule または
Skill installer を呼び出すことはありません。

```powershell
python scripts/memory_cli.py environment-profile-capture --specification profile-sources.json
python scripts/memory_cli.py environment-profile-capture --specification profile-sources.json --apply
python scripts/memory_cli.py environment-profile-status
python scripts/memory_cli.py environment-profile-current
python scripts/memory_cli.py environment-profile-rebuild-current
python scripts/memory_cli.py environment-profile-compare --peer-node-id node-mac
python scripts/memory_cli.py environment-convergence-plan --peer-node-id node-mac
```

任意の `--artifact-links` 入力は
`schemas/environment-convergence-artifact-links.schema.json` に準拠する必要があります。完全な例は
`examples/environment-convergence-artifact-links.json` を参照してください。有効なリンクでも既存インストーラーのプレビューだけを生成し、アクティベーションを許可しません。

ダッシュボードの Environment タブには、ローカル世代数、エクスポートイベント数、信頼済み
peer の Profile replica、読み取り専用の比較プレビューが表示されます。

更新メタデータは stable、beta、development を明示します。検証済み delta が
失敗した場合は検証済み full package に戻ります。ダウンロードは
`staged-awaiting-user-approval` のままで、既存インストーラーを呼べるのは明示的な
二つ目のコマンドで `--approve-install`、`--expected-version`、
`--expected-sha256` を同時に指定した場合だけです。beta/development または delta
メタデータは `--channel` と `--update-metadata-json` で指定します。チャンネル選択や
ダウンロードの前に、リリースメタデータの Ed25519 SSH 分離署名を固定された
`keys/update-allowed-signers` ID で検証します。要約予算判定は決定的かつモデル非依存で、完了ラウンド
について一つの冪等ジョブを登録できますが、AI は呼び出しません。

署名済み・対象暗号化済み `environment-v1` ストリームが、契約をペア済み
デバイスへ転送します。契約はモデル revision、成果物ハッシュ、ランタイム
パッケージ、query/passage プレフィックス、pooling、正規化、類似度、
インストーラー入口を固定します。受信または受理だけではインストールや
ダウンロードは行われません。各デバイスが受理済み契約を明示的にローカル
実装し、モデル、仮想環境、認証情報、セマンティック索引はデバイス内に
保持します。
