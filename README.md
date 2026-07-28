# Finance Digest

텔레그램 금융/경제 채널의 최근 메시지를 수동으로 수집하고, Claude 또는 Codex로 요약한 뒤, 텔레그램 봇으로 리포트를 보내는 로컬 CLI 도구입니다.

현재 목표는 **수동 실행 가능한 MVP**입니다. macOS 배치 등록, 서버 배포, DB 저장, 웹 UI는 포함하지 않습니다.

## 전체 구조

```text
config.yml
.env
summary_prompt.yml
        |
        v
finance-digest CLI
        |
        v
Telegram 개인 계정 + Telethon
        |
        v
지정 채널/그룹 메시지 수집
        |
        v
data/collections
JSON + Markdown
        |
        v
summary.provider
        |
        +-- claude --> Claude Code CLI --+
        |                                |
        +-- codex  --> Codex CLI -------+
                                         |
                                         v
                                  요약 리포트
                                         |
                       +-----------------+-----------------+
                       |                                   |
                       v                                   v
                data/reports                    Telegram Bot API
                Markdown                        send.telegram.enabled=true
                                                           |
                                                           v
                                                   내 DM 또는 그룹방
```

## 설치

```bash
uv sync
cp config.example.yml config.yml
cp .env.example .env
```

`config.yml`과 `.env`는 개인 계정, 채널, 봇 설정을 담는 로컬 파일입니다. 공개 저장소에는 `config.example.yml`과 `.env.example`만 올리고, 실제 설정 파일은 커밋하지 않습니다.

## `.env` 채우는 법

`.env`에는 비밀값과 계정별 값을 넣습니다. 이 파일은 `.gitignore`에 포함되어 있습니다.

```bash
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=

TELEGRAM_BOT_TOKEN=
TELEGRAM_DELIVERY_CHAT_ID=
```

### `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`

텔레그램 개인 계정으로 채널/그룹 메시지를 읽기 위한 API 정보입니다.

1. https://my.telegram.org 접속
2. 텔레그램 계정으로 로그인
3. `API development tools` 이동
4. 앱 생성
5. 발급된 `api_id`, `api_hash`를 `.env`에 입력

예:

```bash
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
```

### `TELEGRAM_PHONE`

텔레그램 개인 계정 전화번호입니다. 국제번호 형식을 권장합니다.

```bash
TELEGRAM_PHONE=your_phone_number_with_country_code
```

### `TELEGRAM_BOT_TOKEN`

요약 리포트를 보내는 봇의 토큰입니다.

1. 텔레그램에서 `@BotFather` 열기
2. `/newbot` 실행
3. 봇 이름과 username 생성
4. BotFather가 주는 token을 `.env`에 입력

예:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
```

이미 만들어둔 봇이 있으면 그 봇 토큰을 사용해도 됩니다.

### `TELEGRAM_DELIVERY_CHAT_ID`

리포트를 받을 대상 chat id입니다. 내 DM일 수도 있고, 봇이 초대된 그룹방일 수도 있습니다.

#### 내 DM으로 받을 때

먼저 텔레그램에서 해당 봇에게 `/start`를 보냅니다. 그 다음 개인 계정 로그인을 한 뒤 아래 명령으로 내 user id를 확인합니다.

```bash
uv run finance-digest login --config config.yml
uv run finance-digest me --config config.yml
```

출력 예:

```text
id: your_user_id
name: My Name
username: @my_username

Use this in .env: TELEGRAM_DELIVERY_CHAT_ID=your_user_id
```

이 값을 `.env`에 넣습니다.

```bash
TELEGRAM_DELIVERY_CHAT_ID=your_user_id
```

#### 그룹방으로 받을 때

1. 봇을 그룹방에 초대
2. 그룹방에서 봇에게 메시지 전송
3. 아래 명령 실행

```bash
source .env
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" | python -m json.tool
```

출력에서 그룹 메시지의 `message.chat.id`를 찾습니다.

```json
"chat": {
  "id": -1001234567890,
  "title": "Finance Friends",
  "type": "supergroup"
}
```

그 값을 `.env`에 넣습니다.

```bash
TELEGRAM_DELIVERY_CHAT_ID=target_chat_id
```

`result: []`만 나오면 봇에게 새 메시지를 보내지 않았거나, 기존 webhook/다른 서버가 업데이트를 소비 중일 수 있습니다.

## `config.yml` 설정

### 수집 대상 채널/그룹 추가

수집 대상은 `config.yml`의 `chats`에 직접 적습니다.

공개 채널은 username을 사용할 수 있습니다.

```yaml
chats:
  - name: Market News
    identifier: market_news_channel
    enabled: true
```

비공개 채널/그룹은 `finance-digest chats` 명령으로 identifier를 확인합니다.

```bash
uv run finance-digest chats --config config.yml --limit 500
```

출력 예:

```text
-1001234567890    channel    @market_news    Market News
-987654321        group                     Private Finance Room
```

첫 번째 컬럼을 `identifier`에 그대로 넣습니다.

```yaml
chats:
  - name: Private Finance Room
    identifier: -1001234567890
    enabled: true
```

일시적으로 제외하려면 `enabled: false`로 바꾸면 됩니다.

```yaml
chats:
  - name: 잠시 제외할 채널
    identifier: -1001234567890
    enabled: false
```

### 수집 기간

```yaml
collection:
  timezone: Asia/Seoul
  recent_hours: 24
  monday_weekend: true
  output_dir: data/collections
```

- 기본은 실행 시각 기준 최근 24시간입니다.
- 월요일에 `monday_weekend: true`이면 최근 72시간을 수집합니다.
- 수집 결과는 `data/collections`에 JSON과 Markdown으로 저장됩니다.

### 요약 엔진 선택

기본값은 Claude이며, Claude 실행이 실패하면 자동으로 Codex로 폴백합니다. 둘 다 실패하면 두 에러를 함께 출력합니다.

```yaml
summary:
  provider: claude
```

Codex로 바꾸려면:

```yaml
summary:
  provider: codex
```

요약 프롬프트는 루트의 `summary_prompt.yml`에서 관리합니다.

```yaml
summary:
  output_dir: data/reports
  provider: claude
  prompt_file: summary_prompt.yml
```

Telegram은 표와 헤딩을 렌더링하지 못하므로, 현재 프롬프트는 Markdown 표·`#` 헤딩·`---` 수평선을 금지하고, `**1. 제목**` / `**7-1. 제목**` 형태의 볼드 넘버링 섹션과 bullet 중심으로 출력하도록 되어 있습니다.

### 텔레그램 발송

```yaml
send:
  telegram:
    enabled: false
    bot_token_env: TELEGRAM_BOT_TOKEN
    delivery_chat_id_env: TELEGRAM_DELIVERY_CHAT_ID
    disable_web_page_preview: true
    max_chars: 3900
```

- `enabled: false`: 리포트 파일만 만들고 발송하지 않습니다.
- `enabled: true`: 요약 결과를 텔레그램으로 보냅니다.
- `disable_web_page_preview: true`: 링크 미리보기 카드를 끕니다.
- `max_chars`: 텔레그램 메시지당 4096자 하드리밋을 피하기 위한 분할 기준입니다. 초과 시 문단/줄 경계에서 나누어 `[1/2]` 접두어를 붙여 여러 통으로 보냅니다.

발송 시 리포트 Markdown은 Telegram HTML로 변환되어 `parse_mode=HTML`로 전송됩니다.

- `**볼드**`, `` `코드` ``, `[텍스트](url)` 링크, `>` 인용은 그대로 렌더링됩니다.
- `#` 헤딩은 볼드로 변환되고, `---` 수평선은 제거됩니다.
- `& < >`는 이스케이프되므로 요약 원문이 전송을 깨뜨리지 않습니다.

## 실행 방법

### 1. 최초 로그인

```bash
uv run finance-digest login --config config.yml
```

처음 실행하면 텔레그램 인증코드나 2FA 비밀번호를 물어볼 수 있습니다. 성공하면 `data/telegram.session` 세션 파일이 생성됩니다.

### 2. 채널 identifier 확인

```bash
uv run finance-digest chats --config config.yml --limit 500
```

여기서 나온 첫 번째 컬럼을 `config.yml`의 `chats[].identifier`에 넣습니다.

### 3. 수집만 테스트

```bash
uv run finance-digest collect --config config.yml --dry-run
```

이 명령은 메시지 개수만 확인합니다.

- 파일 저장 안 함
- 요약 안 함
- 텔레그램 발송 안 함

### 4. 수집 + 요약 테스트

```bash
uv run finance-digest run --config config.yml --dry-run
```

이 명령은 수집 후 Claude 또는 Codex로 요약하고 결과를 터미널에 출력합니다.

- 파일 저장 안 함
- 텔레그램 발송 안 함
- 요약 엔진 사용량은 발생할 수 있음

### 5. 실제 실행

```bash
uv run finance-digest run --config config.yml
```

이 명령은 다음 작업을 수행합니다.

1. `config.yml`과 `.env` 로드
2. Telegram 개인 계정으로 대상 채널/그룹 메시지 수집
3. `data/collections`에 원문 수집본 저장
4. Claude 또는 Codex로 요약
5. `data/reports`에 요약 리포트 저장
6. `send.telegram.enabled: true`이면 Telegram Bot API로 발송

## 명령어 요약

```bash
uv run finance-digest login --config config.yml
uv run finance-digest me --config config.yml
uv run finance-digest chats --config config.yml --limit 500
uv run finance-digest collect --config config.yml --dry-run
uv run finance-digest summarize --config config.yml
uv run finance-digest run --config config.yml --dry-run
uv run finance-digest run --config config.yml
```

## 문제 해결

### `Could not find the input entity for PeerChannel...`

비공개 채널/그룹 id를 Telethon이 바로 해석하지 못할 때 발생합니다. 현재 코드는 대화 목록에서 `peer_id`를 찾아 보정합니다.

그래도 실패하면:

```bash
uv run finance-digest chats --config config.yml --limit 500
```

출력 첫 번째 컬럼을 다시 복사해 `identifier`에 넣으세요. 목록에 안 나오면 텔레그램 앱에서 해당 방을 한 번 열어본 뒤 다시 시도하세요.

### `getUpdates`가 `result: []`만 반환

대부분 아래 중 하나입니다.

- 봇에게 아직 새 메시지를 보내지 않음
- 봇이 그룹방에 초대되지 않음
- 기존 webhook 또는 다른 서버가 업데이트를 소비 중

먼저 봇 DM이나 그룹방에서 봇에게 새 메시지를 보낸 뒤 다시 실행하세요.

## 주의사항

- 이 도구는 텔레그램 개인 계정으로 메시지를 읽습니다. `data/telegram.session` 파일을 공유하지 마세요.
- `.env`, `config.yml`, `data/`, `logs/`, `*.session`은 git에 올리지 마세요.
- `data/collections`와 `data/reports`에는 원문 메시지와 요약 결과가 저장될 수 있으므로 공개 저장소에 포함하지 않습니다.
- 원문 채널/그룹의 공유 제한이나 사적 정보 포함 여부를 확인하세요.
