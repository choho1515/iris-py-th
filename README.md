# Thread Helper Public Documentatio

이 모듈은 Iris 라이브러리를 사용하여 카카오톡의 댓글(스레드) 관리하기 위한 도구와 객체지향 인터페이스를 제공합니다.

## 객체지향 인터페이스

모듈을 임포트하면 `ChatContext`에 `.thread` 속성이 자동으로 추가되어 직관적으로 스레드 기능에 접근할 수 있습니다.

### Thread 클래스
스레드 자체를 나타내는 핵심 객체입니다.

**속성**
- `exists`: (bool) 현재 메시지가 유효한 스레드에 속해 있는지 여부.
- `source`: (ChatContext) 스레드의 원본(최상위) 메시지 객체.
- `author`: (ThreadAuthor) 스레드를 시작한 원본 작성자 정보.
- `participants`: (List[ThreadParticipant]) 스레드에 대화를 남긴 모든 참여자 객체 리스트.
- `stats`: (Dict) 답장 수, 고유 참여자 수 등 스레드 통계 데이터.
- `summary`: (Dict) 작성자, 메시지 수, 참여자 수를 포함한 요약 딕셔너리.

**메서드**
- `send(message: str)`: 현재 스레드에 즉시 답장을 전송합니다.
- `isOpenChannel()`: True를 반환합니다 (JS API 호환성용).

### ThreadAuthor 클래스
스레드 또는 메시지 작성자를 나타냅니다.

**속성**
- `id`: (int) 유저 ID.
- `name`: (str) 닉네임.
- `type`: (str) 발신자 타입 (HOST, MANAGER, USER 등).

### ThreadParticipant 클래스
스레드 내의 개별 참여자를 나타냅니다.

**속성**
- `name`: (str) 닉네임.
- `id`: (int) 유저 ID.
- `msgId`: (int) 해당 참여자가 스레드에서 보낸 가장 최근 메시지의 ID.
- `msg`: (str) 해당 참여자가 스레드에서 보낸 가장 최근 메시지 내용.

**메서드**
- `to_dict()`: 참여자 데이터를 딕셔너리 형식으로 변환합니다.

---

## 주요 함수 목록

### get_thread_id(chat: ChatContext) -> Optional[int]
현재 메시지의 부모 스레드 ID를 반환합니다. 스레드가 아닐 경우 None을 반환합니다.

### is_reply_or_thread(chat: ChatContext) -> bool
메시지가 레거시 답장(type 26)이거나 최신 스레드 답장인지 확인합니다.

### get_thread_source(chat: ChatContext) -> Optional[ChatContext]
스레드의 원본 메시지를 ChatContext 객체로 가져옵니다.

### get_thread_messages(chat: ChatContext, source_message_id: int, limit: int = 50) -> List[ChatContext]
특정 원본 메시지 ID를 기준으로 모든 답장 리스트를 가져옵니다.

### get_thread_participants(chat: ChatContext, limit: int = 50) -> List[User]
스레드에 참여 중인 User 객체 목록을 반환합니다.

### get_participant_list(chat: ChatContext, limit: int = 50) -> List[Dict[str, Any]]
참여자 정보를 상세 딕셔너리 리스트로 반환합니다.

### get_thread_summary(chat: ChatContext) -> Dict[str, Any]
스레드 상태(작성자, 메시지 수 등)를 요약한 딕셔너리를 반환합니다.

### filter_thread_by_user(chat: ChatContext, target_user_id: Union[int, str]) -> List[ChatContext]
스레드 내에서 특정 유저가 보낸 메시지만 필터링하여 반환합니다.

### get_thread_as_dict(chat: ChatContext, limit: int = 100) -> Optional[Dict[str, Any]]
스레드 전체 구조를 분석용 딕셔너리 형태로 변환합니다.

### get_thread_timeline(chat: ChatContext, limit: int = 50) -> List[str]
"[닉네임] 내용" 형식으로 정렬된 대화 타임라인 리스트를 반환합니다.

### send_to_thread(chat: ChatContext, message: str, thread_id: Union[str, int] = None) -> bool
특정 스레드에 메시지를 전송합니다. ID가 없으면 현재 컨텍스트의 스레드를 대상으로 합니다.

### open_thread(chat: ChatContext, target_msg_id: Union[str, int], message: str) -> bool
특정 메시지 ID에 대해 명시적으로 스레드를 생성하거나 참여합니다.

### is_thread_reply (데코레이터)
명령어가 스레드 내에서 호출되었을 때만 작동하도록 제한하는 데코레이터입니다.
