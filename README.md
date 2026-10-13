# Finance Digest

텔레그램 금융·경제 채널의 메시지를 모아 Claude 또는 Codex로 요약하고, 텔레그램 봇으로 리포트를 보내는 로컬 CLI입니다. macOS launchd 같은 스케줄러에서 `run`을 주기적으로 호출하는 용도이며, 스케줄러 등록 스크립트·서버 배포·웹 UI는 포함하지 않습니다.

## 동작 방식

[![Finance Digest 아키텍처](assets/architecture.svg)](https://ds-argus.github.io/finance-digest/assets/architecture.html)

그림을 누르면 노드 포커스와 경로 추적이 되는 인터랙티브 버전이 열립니다. 로컬에서는 [`assets/architecture.html`](assets/architecture.html)을 브라우저로 여세요.

1. 채널마다 수집 커서(`data/state.json`)부터 지금까지를 수집 기간으로 잡습니다.
2. 텔레그램 개인 계정(Telethon)으로 메시지를 읽어 `data/collections`에 JSON과 Markdown으로 저장합니다. 한 채널이 실패해도 나머지는 계속 수집합니다.
3. Claude Code CLI로 요약하고, 실패하면 Codex CLI로 폴백합니다.
4. 리포트를 `data/reports`에 저장하고, 비밀값 가드를 통과하면 텔레그램 봇으로 보냅니다.
5. 저장과 발송이 모두 성공해야 커서를 갱신합니다. 실패하면 실패 알림을 보내고, 다음 실행이 같은 구간부터 다시 수집합니다.

## 빠른 시작

```bash
uv sync
cp config.example.yml config.yml
cp .env.example .env
chmod 600 config.yml .env
```

1. `.env`를 채웁니다. 값을 얻는 법은 [`.env`](#env)에 있습니다. 내 DM으로 받을 `TELEGRAM_DELIVERY_CHAT_ID`는 로그인 후에 알 수 있으니 5단계에서 채워도 됩니다.
2. 텔레그램 개인 계정으로 로그인합니다. 인증코드나 2FA 비밀번호를 물으면 입력하세요. 성공하면 `data/telegram.session`이 생깁니다.

   ```bash
   uv run finance-digest login
   ```

3. 수집할 채널을 `config.yml`의 `chats`에 넣습니다. 방법은 [수집 대상](#수집-대상-chats)에 있습니다.
4. 파일 저장과 발송 없이 결과를 확인합니다.

   ```bash
   uv run finance-digest collect --dry-run   # 채널별 메시지 수만 확인
   uv run finance-digest run --dry-run       # 수집 + 요약 결과 출력 (요약 엔진 사용량 발생)
   ```

5. `.env`에 `TELEGRAM_DELIVERY_CHAT_ID`를 채우고, `config.yml`에서 `send.telegram.enabled: true`로 바꾼 뒤 실행합니다.

   ```bash
   uv run finance-digest run
   ```

모든 명령은 현재 디렉터리의 `config.yml`을 읽습니다. 다른 파일은 `--config <경로>`로 지정합니다.

## 설정

전체 키와 기본값은 [`config.example.yml`](config.example.yml)에 있습니다.

### `.env`

비밀값과 계정별 값입니다. `config.yml`의 `env_file`로 읽으며, 이미 설정된 환경 변수가 있으면 그 값이 우선합니다.

| 변수 | 내용 | 얻는 법 |
|---|---|---|
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | 개인 계정 API 키 | https://my.telegram.org → API development tools → 앱 생성 |
| `TELEGRAM_PHONE` | 개인 계정 전화번호 | 국제번호 형식 (예: `+82…`) |
| `TELEGRAM_BOT_TOKEN` | 리포트를 보낼 봇 토큰 | `@BotFather`에서 `/newbot` |
| `TELEGRAM_DELIVERY_CHAT_ID` | 리포트를 받을 채팅 id | 아래 참고 |

`TELEGRAM_DELIVERY_CHAT_ID`:

- **내 DM**: 봇에게 `/start`를 보내고, 로그인한 뒤 `uv run finance-digest me`가 출력하는 `id`를 넣습니다.
- **그룹방**: 봇을 초대하고 그룹에서 봇에게 메시지를 보낸 뒤, 아래 출력에서 그 메시지의 `message.chat.id`(그룹은 음수)를 넣습니다.

  ```bash
  source .env
  curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" | python -m json.tool
  ```

  `result: []`만 나오면 봇에게 새 메시지를 보내지 않았거나, 봇이 그룹에 없거나, webhook이나 다른 서버가 업데이트를 가져가고 있는 경우입니다.

### 수집 대상 (`chats`)

```yaml
chats:
  - name: Market News          # 리포트에 표시할 이름
    identifier: market_news    # 공개 채널 username 또는 chat id
    enabled: true              # false면 수집에서 제외
```

비공개 채널·그룹의 id는 `uv run finance-digest chats --limit 500`으로 확인하고, 출력의 첫 번째 컬럼을 `identifier`에 그대로 넣습니다.

```text
-1001234567890    channel    @market_news    Market News
-987654321        group                     Private Finance Room
```

`enabled: false`로 껐던 채널도 커서는 남아 있습니다. 다시 켜면 그 커서부터 그동안 쌓인 메시지를 한 번에 수집합니다.

### 수집 기간 (`collection`)

```yaml
collection:
  timezone: Asia/Seoul
  recent_hours: 24
  monday_weekend: true
  output_dir: data/collections
  state_file: data/state.json
```

- `run`은 채널마다 마지막으로 발송까지 끝난 시점(`state_file`의 수집 커서)부터 지금까지 수집합니다.
- 커서는 리포트 저장과 발송(`send.telegram.enabled: true`일 때)이 모두 성공한 뒤에만 갱신되므로, 실패한 구간은 다음 실행이 다시 수집합니다.
- 커서가 없는 채널(첫 실행, 새로 추가한 채널)은 최근 `recent_hours`시간을 수집합니다. 월요일이고 `monday_weekend: true`이면 48시간을 더합니다.
- 수집에 실패한 채널은 리포트 끝 **수집 실패 채널**에 표시되고, 커서가 그대로라 다음 실행에서 다시 시도합니다.

### 요약 (`summary`)

```yaml
summary:
  provider: claude               # claude 또는 codex
  prompt_file: summary_prompt.yml
  timeout_seconds: 600
  claude:
    command: claude
    model: ""                    # 비우면 CLI 기본 모델
  codex:
    command: codex
    model: ""
```

- `claude`가 실패하면 Codex로 폴백하고, 둘 다 실패하면 두 에러를 함께 보고합니다. `codex`는 폴백이 없습니다.
- `timeout_seconds`를 넘긴 요약 CLI는 프로세스 그룹째 종료되고 실패로 처리됩니다.
- 요약 에러에는 CLI 출력의 마지막 10줄만 남깁니다. Codex가 stderr에 되풀이하는 프롬프트와 수집 원문은 로그에 남지 않습니다.
- Telegram은 표와 헤딩을 렌더링하지 못하므로, `summary_prompt.yml`은 표·`#` 헤딩·`---`를 금지하고 `**1. 제목**` 형태의 볼드 번호 섹션과 bullet로 쓰게 합니다.

### 발송 (`send.telegram`)

```yaml
send:
  telegram:
    enabled: false
    bot_token_env: TELEGRAM_BOT_TOKEN
    delivery_chat_id_env: TELEGRAM_DELIVERY_CHAT_ID
    disable_web_page_preview: true
    max_chars: 3900
```

- `enabled: false`면 리포트 파일만 만들고 보내지 않습니다.
- 리포트 Markdown은 Telegram HTML(`parse_mode=HTML`)로 바꿔 보냅니다. `**볼드**`, `` `코드` ``, `[텍스트](url)`, `>` 인용은 그대로 보이고, `#` 헤딩은 볼드가 되며 `---`는 지웁니다. 원문의 `& < >`는 이스케이프됩니다.
- 리포트가 `max_chars`를 넘으면 문단·줄 경계에서 나눠 `[1/2]` 같은 번호를 붙여 보냅니다. Telegram의 메시지당 한도는 4096자입니다.
- 네트워크 오류, 429, 5xx에는 최대 3번까지 시도합니다. 429면 Telegram이 알려준 대기 시간(최대 60초)을 따릅니다.
- 발송이 켜져 있으면 `run`이 실패할 때 같은 채팅으로 짧은 실패 알림을 일반 텍스트로 보냅니다.

## 명령어

| 명령 | 하는 일 |
|---|---|
| `login` | 텔레그램 개인 계정 로그인, 세션 파일 생성 |
| `me` | 내 user id 출력 |
| `chats [--limit N]` | 대화 목록과 id 출력 (기본 100개) |
| `collect [--dry-run]` | 수집해서 `data/collections`에 저장. 커서는 그대로 |
| `summarize [--input JSON] [--dry-run]` | 수집본으로 리포트 작성. 기본은 가장 최근 수집본 |
| `send [--input MD]` | 저장된 리포트 발송. 기본은 가장 최근 리포트 |
| `run [--dry-run]` | 수집 → 요약 → 저장 → 발송 → 커서 갱신 |

`--dry-run`은 파일 저장, 발송, 커서 갱신 없이 결과만 터미널에 출력합니다. `collect`와 `run`은 `--since`, `--until`로 수집 기간을 직접 정할 수 있습니다.

## 실패 복구

다음 정기 `run`이 커서부터 이어서 수집하므로 보통은 할 일이 없습니다. 바로 복구하려면:

```bash
# 발송만 실패했을 때: 저장된 리포트를 다시 발송
uv run finance-digest send
uv run finance-digest send --input data/reports/<label>.md

# 리포트가 없을 때: 저장된 수집본으로 리포트를 만든 뒤 발송
uv run finance-digest summarize --input data/collections/<label>.json
uv run finance-digest send --input data/reports/<label>.md

# 지난 구간을 별도 리포트로 만들기
uv run finance-digest run --since 2026-09-18T08:00 --until 2026-09-21T08:00
```

- `send`도 비밀값 가드와 재시도를 거칩니다.
- `--since`만 주면 모든 채널을 그 시각부터 지금까지 수집하고, 성공하면 커서를 지금으로 맞춥니다.
- `--until`을 주면 과거 구간 리포트가 됩니다. 시간대 없는 값은 `collection.timezone` 기준입니다.
- `send`와 `--until` 리포트는 그 구간이 기존 커서를 이어서 덮을 때만 커서를 앞으로 옮깁니다. 지난 리포트를 다시 보내도 커서는 그대로입니다.

## 보안

- 요약 CLI는 수집 원문에 섞인 지시를 실행하지 못하도록 빈 임시 디렉터리에서 도구를 끈 채 실행합니다.
  - Claude: `--tools ""`, `--strict-mcp-config`로 내장 도구와 MCP 서버를 끕니다.
  - Codex: `--sandbox read-only`, `--ignore-user-config`, `--ignore-rules`로 사용자 MCP·hook·규칙을 빼고, shell·웹 검색·앱 같은 기능을 끕니다.
- 발송 직전 리포트에 `TELEGRAM_API_HASH`나 봇 토큰 값이 들어 있으면 발송을 거부합니다. 실패 알림에서는 그 값을 `<TELEGRAM_BOT_TOKEN>` 같은 이름으로 가립니다.
- `data/telegram.session`은 개인 계정 전체 권한을 가진 파일이니 공유하지 마세요. 실행할 때마다 권한을 `600`으로 맞춥니다.
- `.env`, `config.yml`, `data/`, `logs/`, `*.session`은 `.gitignore`에 있습니다. `data/`에는 원문 메시지와 요약이 쌓이니 공개 저장소에 올리지 마세요.
- 원문 채널·그룹의 공유 제한과 사적 정보 포함 여부를 확인하세요.

## 문제 해결

### 리포트에 `Could not resolve chat identifier`가 뜰 때

설정한 chat id를 대화 목록에서 찾지 못한 경우입니다. `uv run finance-digest chats --limit 500` 출력의 첫 번째 컬럼을 다시 넣으세요. 목록에 없으면 텔레그램 앱에서 그 방을 한 번 연 뒤 다시 시도하세요.

### `Could not find summarizer command`

launchd처럼 `PATH`가 짧은 환경에서 `claude`나 `codex`를 못 찾은 경우입니다. `PATH`에 더해 `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.npm-global/bin`까지 찾아봅니다. 그래도 없으면 `summary.claude.command`나 `summary.codex.command`에 절대 경로를 넣으세요.
