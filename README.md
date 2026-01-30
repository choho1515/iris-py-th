# Thread Helper Public 가이드

이 라이브러리는 Iris 라이브러리의 `ChatContext`를 확장하여 카카오톡 댓글(스레드)를 객체지향적으로 관리할 수 있게 돕습니다.

---

## 1. 퀵 레퍼런스 (Quick Reference)

모듈을 임포트하면 `chat.thread`를 통해 즉시 모든 기능을 사용할 수 있습니다.

### 원본 데이터 접근 (Getter)
| 속성 경로 | 설명 | 비고 |
|:---|:---|:---|
| `chat.thread.sender.name` | 스레드 원본 작성자의 닉네임 | 가장 많이 사용됨 |
| `chat.thread.message.msg` | 스레드 원본 메시지의 텍스트 내용 | |
| `chat.thread.id` | 스레드 원본 메시지 ID (숫자) | 일반 메시지일 경우 `None` |
| `chat.thread.exists` | 현재 메시지가 스레드에 속해 있는지 여부 | `True` / `False` |
| `chat.thread.room.name` | 원본 메시지가 발생한 채팅방 이름 | |
| `chat.thread.raw` | 스레드 전체 정보를 구조화한 날것의 데이터(Thread Raw). 스레드의 모든 메타데이터와 답장 목록이 포함됩니다. | 딕셔너리 형태 |

### 핵심 동작 (Methods)
| 메서드 | 설명 | 사용 예시 |
|:---|:---|:---|
| `chat.thread.reply(text)` | 현재 스레드에 답장 전송 | `chat.thread.reply("확인했습니다.")` |
| `chat.thread.timeline()` | [ {name:..., content:..., time:...}, ... ] 리스트 생성 | 생성 시간(Unix TS) 포함 |
| `chat.thread.messages()` | 스레드 내 모든 답장 객체 리스트 조회 | `limit` 인자 사용 가능 |
| `chat.thread.is_starter` | 현재 발신자가 스레드 시작자인지 확인 | 프로퍼티 (권한 체크용) |

---

## 2. 상세 상세 속성 및 메서드

### Thread 객체 (chat.thread)
스레드 원본 메시지의 컨텍스트를 대변하며, 동시에 관리 도구 역할을 수행합니다. 스레드가 아닐 경우 현재 메시지를 원본으로 간주하여 에러를 방지합니다.

- **데이터 프록시**: `sender`, `message`, `room`, `api` 속성은 원본 메시지의 정보를 직접 가리킵니다.
- `raw`: (Dict) 스레드 전체 정보를 구조화한 날것의 데이터(Thread Raw). 스레드의 모든 메타데이터와 답장 목록이 포함됩니다.
- `participants`: (List[ThreadParticipant]) 참여자 리스트.
- `is_starter`: (bool) 본인이 원본 작성자인지 확인.
- `stats`: (Dict) 스레드 상세 통계.
- `summary`: (Dict) 스레드 간략 요약.

**주요 메서드 (Methods)**
- `reply(message: str)`: 현재 스레드에 답장을 전송합니다. (`chat.thread.reply("안녕")`)
- `send(message: str)`: `reply()`와 동일합니다.
- `messages(limit: int=50)`: 전체 답장 목록 조회.
- `timeline(limit: int=50)`: AI 프롬프트 등에 활용하기 좋게 `{"name": "...", "content": "...", "time": 1769780000}` 형태의 딕셔너리 리스트를 반환합니다.
- `get_context(limit: int=5)`: 최근 대화 흐름 조회.
- `filter_by_user(user_id)`: 특정 유저 메시지 필터링.
- `estimate_reply_target()`: 답장 대상 추정.
- `isOpenChannel()`: 오픈채팅 스레드 여부 확인.

### ThreadParticipant 객체
참여자 목록(`chat.thread.participants`)에 들어있는 개별 요소입니다.
- `name`, `id`: 참여자 닉네임 및 ID
- `msg`, `msgId`: 해당 참여자가 남긴 마지막 메시지 및 ID
- `to_dict()`: 딕셔너리 형태로 변환

---

## 3. 데이터 출력 예시 (Expected Output)

### `chat.thread.summary`
```json
{
    "owner": "홍길동",
    "msgCount": 5,
    "participantCount": 3
}
```

### `chat.thread.stats`
```json
{
    "reply_count": 4,
    "unique_participants": 3,
    "duration_seconds": 120,
    "room_id": 123456789
}
```

---

## 4. 유틸리티

### @is_thread_reply (데코레이터)
명령어가 답장(스레드) 내에서 호출되었을 때만 작동하도록 제한합니다. 스레드가 아닐 경우 사용자에게 안내 메시지를 전송하고 실행을 중단합니다.

### open_thread(chat, target_id, text)
기존 메시지에 강제로 스레드를 열어 첫 번째 답장을 보냅니다.

---

## 5. `chat.thread.timeline()`
```json
[
    { "name": "홍길동", "content": "이것은 원본입니다.", "time": 1769780000 },
    { "name": "이몽룡", "content": "첫 번째 답장입니다.", "time": 1769780010 }
]
```
