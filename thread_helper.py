import json
import re
import requests
from functools import wraps
from typing import Optional, List, Dict, Any, Union

from iris import ChatContext
from iris.bot.models import Message, Room, User
from iris.bot._internal.iris import IrisAPI

def _silent_parse(self, res):
    try: data = res.json()
    except: data = {}
    if not 200 <= res.status_code <= 299:
        raise Exception(f"Iris 오류: {data.get('message', '알 수 없는 오류')}")
    return data
IrisAPI._IrisAPI__parse = _silent_parse

def _get_user_enc(chat_api_wrapper, user_id: int):
    if not user_id: return None
    try:
        result = chat_api_wrapper.query("SELECT enc FROM db2.open_chat_member WHERE user_id = ? LIMIT 1", [user_id])
        if result: return int(result[0].get("enc", 0))
    except: pass
    return None

def _decrypt_cached(api_wrapper, enc: int, text: str, user_id: int):
    if not text or not enc: return None
    try: return api_wrapper.decrypt(enc, text, user_id)
    except: return None

def _decrypt_supplement(chat: ChatContext, supplement: str, user_id: int):
    if not supplement: return None
    if supplement.startswith("{"):
        try: return json.loads(supplement)
        except: return None
    enc = _get_user_enc(chat.api, user_id)
    if not enc: return None
    try:
        plain_text = _decrypt_cached(chat.api, enc, supplement, user_id)
        if plain_text: return json.loads(plain_text)
    except: pass
    return None

def _get_user_name_cached(api_wrapper, user_id: int):
    try:
        query = """
            WITH info AS (SELECT ? AS user_id) 
            SELECT 
                COALESCE(open_chat_member.nickname, friends.name) AS name,
                COALESCE(open_chat_member.enc, friends.enc) AS enc
            FROM info 
            LEFT JOIN db2.open_chat_member ON open_chat_member.user_id = info.user_id 
            LEFT JOIN db2.friends ON friends.id = info.user_id
        """
        result = api_wrapper.query(query, [user_id])
        if result and result[0]:
            return {"name": result[0].get("name"), "enc": result[0].get("enc")}
    except: pass
    return None

def _get_user_name(chat: ChatContext, user_id: int):
    try:
        info = _get_user_name_cached(chat.api, user_id)
        if not info: return None
        name, enc = info.get("name"), info.get("enc")
        if not name: return None
        if enc and name and not any(c in name for c in ['가', '나', '다', ' ']):
            try:
                decrypted = _decrypt_cached(chat.api, int(enc), name, user_id)
                if decrypted: return decrypted
            except: pass
        return name
    except: return None

def _make_chat_from_record(chat: ChatContext, record: dict):
    try:
        v = {}
        try: v = json.loads(record.get("v", "{}"))
        except: pass
        room = Room(id=int(record["chat_id"]), name=chat.room.name, api=chat.api)
        user_id = int(record["user_id"])
        sender = User(id=user_id, chat_id=int(record["chat_id"]), api=chat.api, name=_get_user_name(chat, user_id), bot_id=chat._bot_id)
        message_text, attachment = record.get("message", ""), record.get("attachment", "")
        enc = _get_user_enc(chat.api, user_id)
        if enc:
            if message_text and not message_text.startswith("{") and not message_text.startswith("["):
                try:
                    decrypted = _decrypt_cached(chat.api, enc, message_text, user_id)
                    if decrypted: message_text = decrypted
                except: pass
            if attachment and not attachment.startswith("{") and not attachment.startswith("["):
                try:
                    decrypted = _decrypt_cached(chat.api, enc, attachment, user_id)
                    if decrypted: attachment = decrypted
                except: pass
        message = Message(id=int(record["id"]), type=int(record["type"]), msg=message_text, attachment=attachment, v=v)
        return ChatContext(room=room, sender=sender, message=message, raw=record, api=chat.api, _bot_id=chat._bot_id)
    except: return None

def get_thread_id(chat: ChatContext) -> Optional[int]:
    """
    [사용법] thread_id = get_thread_id(chat)
    현재 메시지가 스레드 답장일 경우 원본 메시지의 ID를 반환합니다. 스레드가 아니면 None을 반환합니다.
    """
    try:
        supplement = chat.raw.get("supplement", "")
        user_id = int(chat.raw.get("user_id", chat.sender.id))
        if not supplement: return None
        data = _decrypt_supplement(chat, supplement, user_id)
        if data and "threadId" in data: return int(data["threadId"])
    except: pass
    return None

def is_reply_or_thread(chat: ChatContext) -> bool:
    """
    [사용법] if is_reply_or_thread(chat):
    메시지가 레거시 답장(type 26)이거나 새로운 스레드 답장인지 확인합니다.
    """
    if chat.message.type == 26: return True
    return get_thread_id(chat) is not None

def get_thread_source(chat: ChatContext) -> Optional[ChatContext]:
    """
    [사용법] source_chat = get_thread_source(chat)
    스레드의 원본 메시지 정보를 ChatContext 객체로 가져옵니다.
    """
    if chat.message.type == 26: return chat.get_source()
    thread_id = get_thread_id(chat)
    if not thread_id: return None
    try:
        result = chat.api.query("SELECT * FROM chat_logs WHERE id = ?", [thread_id])
        if result: return _make_chat_from_record(chat, result[0])
    except: pass
    return None

def get_thread_messages(chat: ChatContext, source_message_id: int, limit: int = 50) -> List[ChatContext]:
    """
    [사용법] replies = get_thread_messages(chat, source_id)
    원본 메시지 ID를 기준으로 해당 스레드에 달린 모든 답장들을 리스트로 가져옵니다.
    """
    try:
        result = chat.api.query("SELECT * FROM chat_logs WHERE chat_id = ? AND id > ? AND supplement IS NOT NULL ORDER BY id ASC LIMIT ?", [chat.room.id, source_message_id, limit * 3])
        thread_replies = []
        for record in result:
            data = _decrypt_supplement(chat, record.get("supplement", ""), int(record.get("user_id", 0)))
            if data and data.get("threadId") == source_message_id:
                thread_chat = _make_chat_from_record(chat, record)
                if thread_chat: thread_replies.append(thread_chat)
                if len(thread_replies) >= limit: break
        return thread_replies
    except: return []

def get_thread_participants(chat: ChatContext, limit: int = 50) -> List[User]:
    """
    [사용법] users = get_thread_participants(chat)
    스레드에 참여 중인 모든 유저(원본 작성자 포함) 목록을 중복 없이 가져옵니다.
    """
    participants = {}
    source = get_thread_source(chat)
    if source:
        participants[source.sender.id] = source.sender
        thread_id = source.message.id
    else:
        thread_id = get_thread_id(chat)
        if not thread_id: return [chat.sender]
    if thread_id:
        for reply in get_thread_messages(chat, thread_id, limit):
            if reply.sender.id not in participants: participants[reply.sender.id] = reply.sender
    return list(participants.values())

def is_thread_reply(func):
    """
    [사용법] @is_thread_reply
    명령어가 답장 또는 스레드 내에서 호출되었는지 확인하는 데코레이터입니다.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        chat = args[0]
        if is_reply_or_thread(chat):
            tid = get_thread_id(chat)
            if tid:
                try:
                    att = json.loads(chat.message.attachment) if isinstance(chat.message.attachment, str) else (chat.message.attachment or {})
                    if "src_logId" not in att:
                        att["src_logId"] = tid
                        chat.message.attachment = json.dumps(att)
                except: pass
            return func(*args, **kwargs)
        chat.reply("메시지에 답장하여 요청하세요.")
        return None
    return wrapper

def get_thread_context(chat: ChatContext, limit: int = 5) -> List[ChatContext]:
    """
    [사용법] context = get_thread_context(chat, limit=5)
    스레드의 대화 흐름을 가져옵니다. [원본, ...최근 답장들, 현재 메시지] 순서입니다.
    """
    source = get_thread_source(chat)
    if not source: return [chat]
    replies = get_thread_messages(chat, source.message.id, limit=20)
    context = [source] + (replies[-limit:] if len(replies) > limit else replies)
    if not any(r.message.id == chat.message.id for r in context): context.append(chat)
    return context

def estimate_reply_target(chat: ChatContext) -> ChatContext:
    """
    [사용법] target = estimate_reply_target(chat)
    멘션을 분석하여 현재 메시지가 스레드 내 누구의 메시지에 대한 답장인지 추정합니다.
    """
    source = get_thread_source(chat)
    if not source: return chat
    names = re.findall(r"@(\S+)", chat.message.msg or "")
    if not names: return source
    for reply in reversed(get_thread_messages(chat, source.message.id, limit=30)):
        for name in names:
            if name in reply.sender.name: return reply
    for name in names:
        if name in source.sender.name: return source
    return source

def send_to_thread(chat: ChatContext, message: str, thread_id: Union[str, int] = None) -> bool:
    """
    [사용법] send_to_thread(chat, "내용", thread_id="12345")
    특정 스레드(또는 현재 스레드)로 메시지를 전송합니다. 성공 시 True를 반환합니다.
    thread_id가 주어지면 해당 ID의 메시지에 스레드를 새로 열거나 기존 스레드에 답장합니다.
    """
    if not thread_id and is_reply_or_thread(chat):
        source = get_thread_source(chat)
        if source:
            thread_id = source.message.id

    payload = {
        "type": "text", 
        "room": str(chat.room.id), 
        "data": message, 
        "threadId": str(thread_id) if thread_id else None
    }
    try:
        res = requests.post(f"{chat.api.iris_endpoint}/reply", json=payload, timeout=5)
        return res.ok
    except: return False

class Participant:
    """스레드 참여자 객체"""
    def __init__(self, name: str, user_id: int, msg_id: int, msg: str):
        self.name = name
        self.id = user_id
        self.msg_id = msg_id
        self.msg = msg
    
    def __repr__(self):
        return f"ThreadUser(name='{self.name}')"

class Channel:
    """채팅방 객체 [object OpenChannel]"""
    def __init__(self, chat: ChatContext):
        self._chat = chat
        self.id = chat.room.id
        self.name = chat.room.name
    
    def isOpenChannel(self) -> bool:
        return True
    
    def send(self, msg: str):
        return self._chat.reply(msg)
    
    def __repr__(self):
        return "[object OpenChannel]"

class Author:
    """발신자 객체 [object User]"""
    def __init__(self, chat: ChatContext):
        self._chat = chat
        self.id = chat.sender.id
        self.name = chat.sender.name
        self.type = str(chat.sender.type).upper()
    
    def __repr__(self):
        return f"[object User:{self.name}]"

def get_participant_list(chat: ChatContext, limit: int = 50) -> List[Dict[str, Any]]:
    """
    [사용법] participants = get_participant_list(chat)
    스레드 참여자 정보를 딕셔너리 리스트 형식으로 반환합니다.
    """
    source = get_thread_source(chat)
    participants = {} # user_id -> dict
    
    def _info(c):
        return {
            "name": c.sender.name,
            "id": c.sender.id,
            "msgId": c.message.id,
            "msg": c.message.msg
        }

    if source:
        participants[source.sender.id] = _info(source)
        tid = source.message.id
    else:
        tid = get_thread_id(chat)
        if not tid: return [_info(chat)]

    replies = get_thread_messages(chat, tid, limit=limit)
    for r in replies:
        participants[r.sender.id] = _info(r)

    participants[chat.sender.id] = _info(chat)
    return list(participants.values())

def get_thread_summary(chat: ChatContext) -> Dict[str, Any]:
    """
    [사용법] summary = get_thread_summary(chat)
    스레드의 상태 정보를 딕셔너리 형식으로 반환합니다.
    """
    data = get_thread_as_dict(chat)
    if not data: return {"error": "Not a thread"}
    
    m = data['metadata']
    s = data['source']
    return {
        "owner": s['sender']['name'],
        "msgCount": m['reply_count'] + 1,
        "participantCount": m['unique_participants']
    }

def filter_thread_by_user(chat: ChatContext, target_user_id: Union[int, str]) -> List[ChatContext]:
    """
    [사용법] user_msgs = filter_thread_by_user(chat, 1234567)
    스레드 내에서 특정 유저가 보낸 메시지들만 리스트로 반환합니다.
    """
    source = get_thread_source(chat)
    if not source: return [chat] if str(chat.sender.id) == str(target_user_id) else []
    
    all_msgs = [source] + get_thread_messages(chat, source.message.id, limit=200)
    return [m for m in all_msgs if str(m.sender.id) == str(target_user_id)]

def is_thread_starter(chat: ChatContext) -> bool:
    """
    [사용법] if is_thread_starter(chat):
    현재 메시지를 보낸 유저가 이 스레드를 처음 시작한 사람(원본 작성자)인지 여부를 반환합니다.
    권한 제어나 작성자 강조 기능을 만들 때 유용합니다.
    """
    source = get_thread_source(chat)
    if not source: return False
    return str(source.sender.id) == str(chat.sender.id)

def get_thread_as_dict(chat: ChatContext, limit: int = 100) -> Optional[Dict[str, Any]]:
    """
    [사용법] thread_data = get_thread_as_dict(chat)
    스레드의 모든 정보를 Dictionary 형태로 반환합니다. 유저의 UUID 정보를 포함합니다.
    """
    source = get_thread_source(chat)
    if not source: return None
    
    replies = get_thread_messages(chat, source.message.id, limit=limit)
    
    def _user(u):
        return {"name": u.name, "id": u.id}

    # 시간 차이 계산
    try:
        start_ts = int(source.raw.get("created_at", 0))
        end_ts = int(replies[-1].raw.get("created_at", start_ts)) if replies else start_ts
        duration = end_ts - start_ts
    except: duration = 0

    return {
        "source": {
            "id": source.message.id,
            "sender": _user(source.sender),
            "content": source.message.msg,
            "timestamp": source.raw.get("created_at")
        },
        "replies": [
            {
                "id": r.message.id,
                "sender": _user(r.sender),
                "content": r.message.msg,
                "timestamp": r.raw.get("created_at")
            } for r in replies
        ],
        "metadata": {
            "reply_count": len(replies),
            "unique_participants": len(set([source.sender.id] + [r.sender.id for r in replies])),
            "duration_seconds": duration,
            "room_id": chat.room.id
        }
    }

def get_thread_timeline(chat: ChatContext, limit: int = 50) -> List[str]:
    """
    [사용법] timeline = get_thread_timeline(chat)
    스레드 대화를 "[닉네임] 내용" 형태의 깔끔한 문자열 리스트로 반환합니다.
    Gemini 등의 AI 프롬프트로 대화 맥락을 전달할 때 즉시 활용 가능합니다.
    """
    source = get_thread_source(chat)
    if not source: return []
    
    messages = [source] + get_thread_messages(chat, source.message.id, limit=limit)
    return [f"[{m.sender.name}] {m.message.msg}" for m in messages]

class ThreadParticipant:
    """스레드 참여자 상세 객체"""
    def __init__(self, name: str, user_id: int, msg_id: int, msg: str):
        self.name = name
        self.id = user_id
        self.msg_id = msg_id
        self.msg = msg
    
    def to_dict(self) -> Dict[str, Any]:
        """데이터를 딕셔너리 형태로 반환"""
        return {
            "name": self.name,
            "id": self.id,
            "msgId": self.msg_id,
            "msg": self.msg
        }

    def __repr__(self):
        return f"ThreadParticipant(name='{self.name}')"

class ThreadAuthor:
    """스레드 원본/메시지 작성자 객체"""
    def __init__(self, chat: ChatContext):
        self._chat = chat
        self.id = chat.sender.id
        self.name = chat.sender.name
        self.type = str(chat.sender.type).upper()
    
    def __repr__(self):
        return f"[object ThreadAuthor:{self.name}]"

class Thread:
    """
    [사용법] t = Thread(chat) 또는 chat.thread
    카카오톡 스레드를 하나의 객체로 다루는 전문 인터페이스입니다.
    """
    def __init__(self, chat: ChatContext):
        self._chat = chat
        self._cached_source = None
    
    @property
    def exists(self) -> bool:
        """현재 메시지가 유효한 스레드(답장 타래)에 속해 있는지 확인"""
        return is_reply_or_thread(self._chat)
    
    @property
    def source(self) -> Optional[ChatContext]:
        """스레드의 원본(최상위) 메시지 객체"""
        if not self._cached_source:
            self._cached_source = get_thread_source(self._chat)
        return self._cached_source

    @property
    def author(self) -> Optional[ThreadAuthor]:
        """스레드를 시작한 작성자 정보"""
        return ThreadAuthor(self.source) if self.source else None

    @property
    def participants(self) -> List[ThreadParticipant]:
        """참여자 리스트를 ThreadParticipant 객체 목록으로 반환"""
        return [ThreadParticipant(**p) for p in get_participant_list(self._chat)]

    @property
    def stats(self) -> Dict[str, Any]:
        """스레드 통계 (답장 수, 참여자 수, 진행 시간 등)"""
        d = get_thread_as_dict(self._chat)
        return d.get("metadata", {}) if d else {}

    @property
    def summary(self) -> Dict[str, Any]:
        """스레드 상태 요약 데이터 (딕셔너리)"""
        return get_thread_summary(self._chat)

    def send(self, message: str) -> bool:
        """이 스레드(타래)에 즉시 답장 전송"""
        tid = self.source.message.id if self.source else get_thread_id(self._chat)
        return send_to_thread(self._chat, message, thread_id=tid)

    def isOpenChannel(self) -> bool:
        """오픈채팅 스레드 여부 (JS API 호환성)"""
        return True

    def __repr__(self):
        return "[object OpenChannel:Thread]"

# ChatContext에 thread 전문 속성 주입
ChatContext.thread = property(lambda self: Thread(self))

# 하위 호환성용 별칭
Channel = Thread
Author = ThreadAuthor
Participant = ThreadParticipant

def open_thread(chat: ChatContext, target_msg_id: Union[str, int], message: str) -> bool:
    """
    [사용법] open_thread(chat, "원본메시지ID", "전송할내용")
    특정 메시지에 스레드를 여는 명시적 함수입니다. (내부적으로 send_to_thread 사용)
    """
    return send_to_thread(chat, message, thread_id=target_msg_id)

get_source_universal = get_thread_source
