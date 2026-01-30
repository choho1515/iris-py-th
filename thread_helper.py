import json
import re
import requests
from functools import wraps
from typing import Optional, List, Dict, Any, Union

from iris import ChatContext
from iris.bot.models import Message, Room, User
from iris.bot._internal.iris import IrisAPI

def _silent_parse(self, res):
    """Iris API 응답 파싱 및 에러 처리 (Monkeypatch)"""
    try: data = res.json()
    except: data = {}
    if not 200 <= res.status_code <= 299:
        raise Exception(f"Iris 오류: {data.get('message', '알 수 없는 오류')}")
    return data

IrisAPI._IrisAPI__parse = _silent_parse

def _get_user_enc(chat_api_wrapper, user_id: int):
    """유저의 암호화 키(enc) 조회"""
    if not user_id: return None
    try:
        result = chat_api_wrapper.query("SELECT enc FROM db2.open_chat_member WHERE user_id = ? LIMIT 1", [user_id])
        if result: return int(result[0].get("enc", 0))
    except: pass
    return None

def _decrypt_cached(api_wrapper, enc: int, text: str, user_id: int):
    """캐시된 키를 이용한 텍스트 복호화"""
    if not text or not enc: return None
    try: return api_wrapper.decrypt(enc, text, user_id)
    except: return None

def _decrypt_supplement(chat: ChatContext, supplement: str, user_id: int):
    """추가 정보(supplement) 복호화 및 파싱"""
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
    """DB에서 유저 닉네임과 암호화 키 정보 조회"""
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
    """유저의 최종 닉네임 조회 (복호화 포함)"""
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
    """DB 레코드를 ChatContext 객체로 변환"""
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
            if message_text and not message_text.startswith("{"):
                try:
                    decrypted = _decrypt_cached(chat.api, enc, message_text, user_id)
                    if decrypted: message_text = decrypted
                except: pass
        message = Message(id=int(record["id"]), type=int(record["type"]), msg=message_text, attachment=attachment, v=v)
        return ChatContext(room=room, sender=sender, message=message, raw=record, api=chat.api, _bot_id=chat._bot_id)
    except: return None

def get_thread_id(chat: ChatContext) -> Optional[int]:
    """현재 메시지의 원본 스레드 ID 반환"""
    try:
        supplement = chat.raw.get("supplement", "")
        user_id = int(chat.raw.get("user_id", chat.sender.id))
        if not supplement: return None
        data = _decrypt_supplement(chat, supplement, user_id)
        if data and "threadId" in data: return int(data["threadId"])
    except: pass
    return None

def is_reply_or_thread(chat: ChatContext) -> bool:
    """메시지가 답장 또는 스레드인지 확인"""
    if chat.message.type == 26: return True
    return get_thread_id(chat) is not None

def get_thread_source(chat: ChatContext) -> Optional[ChatContext]:
    """스레드의 원본 메시지 객체 조회"""
    if chat.message.type == 26: return chat.get_source()
    thread_id = get_thread_id(chat)
    if not thread_id: return None
    try:
        result = chat.api.query("SELECT * FROM chat_logs WHERE id = ?", [thread_id])
        if result: return _make_chat_from_record(chat, result[0])
    except: pass
    return None

def get_thread_messages(chat: ChatContext, source_message_id: int, limit: int = 50) -> List[ChatContext]:
    """특정 원본에 달린 답장 리스트 조회"""
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

def get_participant_list(chat: ChatContext, limit: int = 50) -> List[Dict[str, Any]]:
    """스레드 참여자 정보를 상세 딕셔너리 리스트로 반환"""
    source = get_thread_source(chat)
    participants = {} 
    
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
    """스레드 상태 요약 정보 (딕셔너리)"""
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
    """스레드 내 특정 유저 메시지만 필터링"""
    source = get_thread_source(chat)
    if not source: return [chat] if str(chat.sender.id) == str(target_user_id) else []
    all_msgs = [source] + get_thread_messages(chat, source.message.id, limit=200)
    return [m for m in all_msgs if str(m.sender.id) == str(target_user_id)]

def get_thread_as_dict(chat: ChatContext, limit: int = 100) -> Optional[Dict[str, Any]]:
    """스레드 전체를 데이터 구조화하여 반환"""
    source = get_thread_source(chat)
    if not source: return None
    
    replies = get_thread_messages(chat, source.message.id, limit=limit)
    
    def _user(u):
        return {"name": u.name, "id": u.id}

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

def get_thread_timeline(chat: ChatContext, limit: int = 50) -> List[Dict[str, Any]]:
    """타임라인 리스트 생성"""
    source = get_thread_source(chat)
    if not source: return []
    messages = [source] + get_thread_messages(chat, source.message.id, limit=limit)
    return [{
        "name": m.sender.name, 
        "content": m.message.msg,
        "time": int(m.raw.get("created_at", 0))
    } for m in messages]

def is_thread_starter(chat: ChatContext) -> bool:
    """현재 유저가 원본 작성자인지 확인"""
    source = get_thread_source(chat)
    if not source: return False
    return str(source.sender.id) == str(chat.sender.id)

def send_to_thread(chat: ChatContext, message: str, thread_id: Union[str, int] = None) -> bool:
    """특정 스레드로 메시지 전송"""
    if not thread_id:
        tid = get_thread_id(chat)
        if tid: thread_id = tid
        else:
            source = get_thread_source(chat)
            if source: thread_id = source.message.id

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

class ThreadParticipant:
    """스레드 참여자 상세 객체"""
    def __init__(self, name: str, user_id: int, msg_id: int, msg: str):
        self.name = name
        self.id = user_id
        self.msg_id = msg_id
        self.msg = msg
    
    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "id": self.id, "msgId": self.msg_id, "msg": self.msg}

    def __repr__(self):
        return f"ThreadParticipant(name='{self.name}')"

class Thread:
    """카카오톡 스레드 통합 인터페이스"""
    def __init__(self, chat: ChatContext):
        self._chat = chat
        self._cached_source = None
    
    @property
    def exists(self) -> bool:
        """스레드 여부 확인"""
        return is_reply_or_thread(self._chat)
    
    @property
    def id(self) -> Optional[int]:
        """원본 메시지 ID"""
        return get_thread_id(self._chat)

    def get_thread_id(self) -> Optional[int]:
        """원본 메시지 ID (메서드)"""
        return self.id

    @property
    def source(self) -> ChatContext:
        """원본 메시지 (Fallback 포함)"""
        if not self._cached_source:
            src = get_thread_source(self._chat)
            self._cached_source = src if src else self._chat
        return self._cached_source

    @property
    def sender(self) -> User:
        """스레드 원본 메시지의 발신자 객체"""
        return self.source.sender

    @property
    def message(self) -> Message:
        """스레드 원본 메시지 객체"""
        return self.source.message

    @property
    def room(self) -> Room:
        """스레드 원본 메시지가 속한 방 객체"""
        return self.source.room

    @property
    def api(self) -> IrisAPI:
        """스레드 원본 메시지의 API 객체"""
        return self.source.api

    @property
    def raw(self) -> Optional[Dict[str, Any]]:
        """스레드의 모든 정보를 구조화된 딕셔너리 형태로 반환 (Thread Raw)"""
        return get_thread_as_dict(self._chat)

    @property
    def participants(self) -> List[ThreadParticipant]:
        """참여자 객체 리스트"""
        return [ThreadParticipant(**p) for p in get_participant_list(self._chat)]

    @property
    def stats(self) -> Dict[str, Any]:
        """스레드 메타데이터 통계"""
        d = self.raw
        return d.get("metadata", {}) if d else {}

    @property
    def summary(self) -> Dict[str, Any]:
        """스레드 상태 요약"""
        return get_thread_summary(self._chat)

    @property
    def is_starter(self) -> bool:
        """현재 발신자가 시작자인지 확인"""
        return is_thread_starter(self._chat)

    def messages(self, limit: int = 50) -> List[ChatContext]:
        """스레드 내의 모든 답장 메시지 목록을 가져옵니다."""
        tid = self.id if self.id else self._chat.message.id
        return get_thread_messages(self._chat, tid, limit=limit)

    def filter_by_user(self, user_id: Union[int, str]) -> List[ChatContext]:
        """특정 유저 메시지만 필터링"""
        return filter_thread_by_user(self._chat, user_id)

    def timeline(self, limit: int = 50) -> List[Dict[str, Any]]:
        """타임라인 데이터 생성"""
        return get_thread_timeline(self._chat, limit=limit)

    def get_context(self, limit: int = 5) -> List[ChatContext]:
        """최근 대화 흐름 조회"""
        return get_thread_context(self._chat, limit=limit)

    def estimate_reply_target(self) -> ChatContext:
        """답장 대상 추정"""
        return estimate_reply_target(self._chat)

    def send(self, message: str, target_id: Union[str, int] = None) -> bool:
        """이 스레드(타래) 또는 특정 메시지에 답장 전송"""
        tid = target_id if target_id else self.id
        return send_to_thread(self._chat, message, thread_id=tid)

    def isOpenChannel(self) -> bool:
        return True

    def __repr__(self):
        return f"[object Thread(id={self.id}, sender='{self.sender.name}')]"

ChatContext.thread = property(lambda self: Thread(self))

Channel = Thread
Author = ChatContext
Participant = ThreadParticipant

def is_thread_reply(func):
    """스레드 전용 명령어 데코레이터"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        chat = args[0]
        if is_reply_or_thread(chat):
            return func(*args, **kwargs)
        chat.reply("메시지에 답장하여 요청하세요.")
        return None
    return wrapper

def get_thread_context(chat: ChatContext, limit: int = 5) -> List[ChatContext]:
    """전역 함수 형태의 대화 흐름 조회"""
    source = get_thread_source(chat)
    if not source: return [chat]
    replies = get_thread_messages(chat, source.message.id, limit=20)
    context = [source] + (replies[-limit:] if len(replies) > limit else replies)
    if not any(r.message.id == chat.message.id for r in context): context.append(chat)
    return context

def estimate_reply_target(chat: ChatContext) -> ChatContext:
    """전역 함수 형태의 답장 대상 추정"""
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
