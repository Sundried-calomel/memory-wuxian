# Memory無限

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Memory無限は、アクティブなコンテキストウィンドウを越えて、永続的・階層的・検証可能な会話記憶を構築するファイルベースのCodex Skillです。

インストール用Skill識別子は`memory-wuxian`、プロジェクト名と表示名は`Memory無限`です。正確な原文記録を履歴上の権威ある情報源とし、要約をナビゲーションに使い、過去の記述を検証済み事実として扱う前に原文へ戻って確認します。

## 機能

- タイムスタンプとSHA-256整合性フィールドを備えた追記専用Markdown会話記録
- 会話ごとに完全かつ自動更新されるMarkdown全文
- 並行タスクでも会話単位に分離された未完了ラウンドと返信関係
- 会話単位のレベル1要約と上位レベル要約
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
- macOSネイティブLaunchAgentまたはWindowsタスクスケジューラによるイベント駆動同期
- SHA-256マニフェストと追記専用バックアップログを持つ最新デスクトップ検証スナップショット
- 派生ファイル再構築用の最新ワークスペース復旧バックアップ
- 差分バンドル、成果物台帳カーソル、デバイス間検索を備えたフェデレーション読取専用レプリカ
- SSHと暗号化クラウドフォルダーの並列フェデレーション転送
- ChatGPT公式エクスポートZIPと`conversations.json`向けの実験的ローカルアダプター
- データベースに依存しない透明なファイル構造

## インストール

### 単一ファイルインストーラー

最新のGitHub ReleaseからOSに対応するインストーラーをダウンロードします。

- macOS：`MemoryWuxian-<version>-macOS-universal.pkg`
- Windows：`MemoryWuxian-<version>-Windows-x64-Setup.exe`

ステータスコンソールは、最後に成功したブラウザローカル応答と、ソース検証済みの永続統計スナップショットから起動します。アーカイブが変わっていない場合は原文履歴全体を再読込せず、古い・破損したスナップショットだけを権威ある記録から自動再構築します。任意のローカル実績システムは、アーカイブ容量、アーカイブコンテキストとメッセージのみのToken推定、会話深度、プロジェクト成長、要約階層、原文検証済み検索を記録します。

インストーラーを開くと、現在のユーザーのCodexディレクトリにSkillを配置し、`Documents/MemoryWuxianArchive`を初期化して継続的なCodex収集を有効化します。再インストールやアップグレードでは設定とアーカイブを保持します。アンインストールはプログラムとバックグラウンド統合を削除しますが、会話履歴は残します。公開ビルドは、リリース処理に署名資格情報が設定されていない限り未署名のため、OSが明示的な確認を求める場合があります。

### Codex Skillインストーラー

GitHubディレクトリからSkillをインストールし、Codexを再起動します。

```text
$skill-installer install https://github.com/Sundried-calomel/memory-wuxian
```

手動インストールではリポジトリを次に配置します。

```text
~/.codex/skills/memory-wuxian
```

## クイックスタート

最初に[`SKILL.md`](SKILL.md)を読んでください。実際の会話履歴にはリポジトリ外のアーカイブルートを使い、ソース更新と個人記憶データが混ざらないようにします。

公式インストーラーは安定版を毎日確認します。更新処理はブランチ、ドラフト、プレリリースを無視し、プラットフォーム用インストーラーとSHA-256ファイルの両方を取得します。チェックサムやファイル名が一致しない更新は保存しません。Windowsは次回ログイン時に検証済み更新をサイレントインストールし、macOSはOSのインストール承認を待つ検証済みPKGを保持します。`python scripts/install_auto_update.py --uninstall`で確認を無効化できます。

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"

python3 scripts/memory_cli.py --root "$ARCHIVE" init
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker user --text "Hello"
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker assistant --text "Hello."
python3 scripts/memory_cli.py --root "$ARCHIVE" sync-codex --session-file "$HOME/.codex/sessions/.../rollout-....jsonl"
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

Windowsではローカルコンソールをネイティブアプリウィンドウとして起動できます。インストール済みMicrosoft Edge WebView2と同梱アイコンを使い、ブラウザ枠なしで完全なUIを表示します。

```powershell
python scripts/memory_dashboard.py `
  --root "C:\path\to\memory-wuxian-archive" `
  --config "C:\path\to\memory-wuxian\config.yaml" `
  --window
```

環境確認でオープンソース`pywebview`が不足している場合は、`scripts/bootstrap_windows.ps1 -InstallMissing`を一度実行します。中国語、英語、日本語UIを保持し、既定で30秒ごとに静かに更新します。会話ごとのCodexタイトル、メッセージ、完了ラウンド、要約レベル、日別アーカイブ量、保留要約、可視ソース文字数、明示されたアーカイブToken推定を表示します。文字数にはユーザーと可視アシスタント会話を含み、生成要約は含みません。Token推定はCJKを考慮したヒューリスティックで、課金使用量や要約生成消費ではありません。会話ごとの最新モデル要求Tokenと公称コンテキストウィンドウの比率も表示しますが、要求には指示、ツール、推論、出力が含まれ得るため100%を超える場合があり、正確な占有率や残量ではありません。

コンソールはlocalhostだけにバインドし、外部サービスへアーカイブを送りません。通常の状態表示は読取専用です。設定画面の明示的操作では、暗号化クラウド交換の有効化・無効化、即時同期、選択したChatGPTエクスポートのローカル取込みができます。`--window`を使わない場合はクロスプラットフォームのブラウザモード、`--no-browser`はローカルサーバーのみ、`--port`はポート指定です。

## macOSでCodexを自動収集

SkillのインストールだけではCodexイベントを購読しません。Rustコレクターを一度ビルドし、LaunchAgentをインストールします。

```bash
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

LaunchAgentは最適化されたRustプロセスを維持し、OSのファイル変更通知と適応型size/mtime補助確認を使います。活発な時は5秒ごと、2分間アイドル後は30秒、15分間アイドル後は5分に低下し、ネイティブイベントは即時起動します。ユーザーメッセージ、可視assistant commentary/final、トップレベルCodexタイムラインの軽量ツール活動を保存します。利用可能な場合はツール名、ネストしたツール名、コマンド文を保持し、ツール出力、システム指示、隠れた推論、サブエージェント会話は除外します。セッション別カーソルと安定ソースIDにより再試行は冪等です。

ネイティブコレクターはイベント駆動JSONL解析、原文追記、会話別全文、決定的ルーティングインデックス、カーソル、期限到来レベル1ジョブ、デスクトップスナップショットを直接担当します。成功したCodexファイル編集は、パス、変更種別、移動先、追加・削除数、hunk行範囲、正確なunified diffを記録します。一般ツール出力と隠れた推論は除外します。既存インストールはpatchイベント履歴を一度だけ補完します。ジョブ期限時にはPython wrapperが一時Codex CLI要約プロセスを一度起動し、取込み後に終了します。Python CLIは低頻度の保守、検索、再構築、要約取込みに使います。

各会話は`memory/conversations/`に個別保存され、一つのconversation IDだけを含みます。機械可読レコードと可読メッセージの両方を保持し、個別インデックスは`memory/indexes/by-conversation/<conversation>/`にあります。`raw/`の不変ファイルが権威ある情報源で、全文とインデックスは再構築可能な決定的ビューです。

保護された`Documents`や`Desktop`にアーカイブまたはバックアップを置く場合、macOSで`bin/memory-wuxian-collector`にフルディスクアクセスを付与します。自動収集が有効と判断する前に、生成plist内の実行ファイルを確認してください。

コレクターは`imports/codex/collector-telemetry.json`へ軽量テレメトリーを公開します。コンソールはactive、idle、deep-idle、補助確認間隔、最新ファイルイベント、最新アーカイブ書込、1時間の起動回数、CPU/メモリを表示します。テレメトリーは活動時またはモード変化時だけ書き込みます。

## ChatGPT会話のインポート

通常のChatGPT会話はCodex rolloutストリームに含まれません。公式ChatGPTデータエクスポートZIP、展開済みディレクトリ、または`conversations.json`をインポートできます。

```bash
python3 scripts/memory_cli.py import-chatgpt --export /path/to/chatgpt-export.zip
```

`--conversation-id <native-id>`を複数指定して会話を選択できます。インポーターは現在の可視ブランチをたどり、systemメッセージと破棄された再生成ブランチを除外し、タイトルと安定IDを保持します。同じ、または新しいエクスポートを再取込みしても重複しません。会話IDは`chatgpt:<conversation-id>`となり、通常のバックアップ、インデックス、要約、検索、コンソール処理に入ります。これはエクスポートアダプターであり、リアルタイムChatGPTリスナーではありません。

同じアダプターは「コンソール > 設定 > ChatGPT会話をインポート」にあります。選択したZIPまたはJSONはlocalhostサーバーだけへストリーム送信され、既存インポーターで解析後、一時保存から削除されます。Memory無限はChatGPTへログインせず、アカウント資格情報を要求せず、他サービスへエクスポートをアップロードしません。

この機能は**実験的**です。自動テストは合成ZIP/JSON、可視ブランチ選択、重複防止、安定ID、ローカルコンソールアップロードを検証しています。実際のユーザーによるChatGPT公式エクスポートはまだ提供されていないため、**実データでは未検証**です。エクスポート形式は変更され得るため、最初の実データ取込みは検証実行として扱い、件数と復元会話を確認してください。

## WindowsでCodexを自動収集

最初に環境ブートストラップを実行します。Pythonバージョンと、Python、Codex CLI、同梱コレクター、Codexセッションのパスを報告します。`-InstallMissing`は、互換性のある`>=3.9`ランタイムもCodex同梱Pythonもない場合だけPythonをインストールします。

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

既定設定では、メモリ変更成功のたびに主アーカイブ書込後、`~/Desktop/Memory無限-记忆归档备份/`へ完全スナップショットを作成し、マニフェストを検証して旧スナップショットを削除します。バックアップルートには最新復旧コピー1件と追記専用`backup-log.jsonl`が残ります。

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

1.6.1からこれらの操作はコンソール設定画面にもあります。クラウド同期スイッチは暗号化交換と5分タスクを同時に制御し、「今すぐ同期」は即時交換を1回実行します。設定済みフォルダーとタスク状態を表示するため、通常操作にAI会話や端末コマンドは不要です。

## プライバシーと統合境界

- 個人アーカイブはリポジトリ外の`--root`を使います。
- 同梱`memory/`内の可変ファイルは`.gitignore`対象です。
- 明示設定時にCLIは明らかな秘密をマスクできますが、保存可否の判断はユーザー責任です。
- 自動収集には同梱LaunchAgent、Windowsタスク、または明示設定したクライアントフックが必要です。
- オフライン`.mwxb`には読取可能な履歴が含まれます。SSHまたは信頼経路を使ってください。SHA-256は暗号化や送信者認証ではありません。
- クラウドフォルダーには署名済み・対象暗号化済み`.mwxe`と暗号化ackだけが入り、デバイス秘密IDは入りません。

## 開発

バイトコードを生成せず機能テストを実行します。

```bash
$HOME/.cargo/bin/cargo test --locked --manifest-path native-collector/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

設計判断と実装契約は[`PROJECT.md`](PROJECT.md)と[`references/`](references/)に、変更履歴は[`CHANGELOG.md`](CHANGELOG.md)にあります。`README.md`、`README.zh-CN.md`、`README.ja.md`は一つの文書契約として管理し、記載動作が変わる場合は同時に更新します。

## ライセンス

Memory無限は[MIT License](LICENSE.txt)で公開されています。
