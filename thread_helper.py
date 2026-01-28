import json
import re
from functools import wraps, lru_cache
from typing import Optional, List, Any

# irispy-client imports
from iris import ChatContext
from iris.bot.models import Message, Room, User

# 캐시 설정 (메모리 효율을 위해 크기 제한)
CACHE_SIZE = 1024


# =============================================================================
# 내부 헬퍼 함수
# =============================================================================

@lru_cache(maxsize=CACHE_SIZE)
def _get_user_enc(chat_api_wrapper, user_id: int) -> Optional[int]:
    """유저의 enc 값 조회 (캐싱 적용)."""
    try:
        # chat.api 객체 사용
        result = chat_api_wrapper.query(
            "SELECT enc FROM db2.open_chat_member WHERE user_id = ? LIMIT 1",
            [user_id]
        )
        if result:
            return int(result[0].get("enc", 0))
    except Exception as e:
        print(f"[thread_helper] enc 조회 실패: {e}")
    return None


@lru_cache(maxsize=CACHE_SIZE)
def _decrypt_cached(api_wrapper, enc: int, text: str, user_id: int) -> Optional[str]:
    """복호화 결과 캐싱."""
    try:
        return api_wrapper.decrypt(enc, text, user_id)
    except:
        return None


def _decrypt_supplement(chat: ChatContext, supplement: str, user_id: int) -> Optional[dict]:
    """supplement 필드 복호화."""
    if not supplement:
        return None
    
    # 이미 JSON인 경우
    if supplement.startswith("{"):
        try:
            return json.loads(supplement)
        except:
            return None
    
    # 암호화된 경우 복호화
    enc = _get_user_enc(chat.api, user_id)
    if not enc:
        return None
    
    try:
        plain_text = _decrypt_cached(chat.api, enc, supplement, user_id)
        if plain_text:
            return json.loads(plain_text)
    except Exception as e:
        print(f"[thread_helper] supplement 복호화 실패: {e}")
    
    return None


@lru_cache(maxsize=CACHE_SIZE)
def _get_user_name_cached(api_wrapper, user_id: int) -> Optional[dict]:
    """닉네임 정보 캐싱 (이름, enc)."""
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
            return {
                "name": result[0].get("name"),
                "enc": result[0].get("enc")
            }
    except:
        pass
    return None


def _get_user_name(chat: ChatContext, user_id: int) -> Optional[str]:
    """유저 이름 조회 (암호화된 닉네임 복호화 지원, 캐싱 적용)."""
    try:
        info = _get_user_name_cached(chat.api, user_id)
        if not info:
            return None
        
        name = info.get("name")
        enc = info.get("enc")
        
        if not name:
            return None
        
        # 이름이 암호화되어 있는지 확인 (Base64 형태)
        if enc and name and '=' in name and len(name) > 10:
            try:
                decrypted = _decrypt_cached(chat.api, int(enc), name, user_id)
                if decrypted:
                    return decrypted
            except:
                pass
        
        return name
    except Exception as e:
        print(f"[thread_helper] 유저 이름 조회 실패: {e}")
        return None


def _make_chat_from_record(chat: ChatContext, record: dict) -> Optional[ChatContext]:
    """DB 레코드로부터 ChatContext 객체 생성."""
    try:
        v = {}
        try:
            v = json.loads(record.get("v", "{}"))
        except:
            pass
        
        room = Room(
            id=int(record["chat_id"]),
            name=chat.room.name,
            api=chat.api
        )
        
        user_id = int(record["user_id"])
        sender = User(
            id=user_id,
            chat_id=int(record["chat_id"]),
            api=chat.api,
            name=_get_user_name(chat, user_id),
            bot_id=chat._bot_id
        )
        
        # 메시지 복호화
        message_text = record.get("message", "")
        attachment = record.get("attachment", "")
        
        enc = _get_user_enc(chat.api, user_id)
        if enc:
            # 메시지 복호화
            if message_text and not message_text.startswith("{") and not message_text.startswith("["):
                try:
                    decrypted = _decrypt_cached(chat.api, enc, message_text, user_id)
                    if decrypted:
                        message_text = decrypted
                except:
                    pass
            
            # attachment 복호화
            if attachment and not attachment.startswith("{") and not attachment.startswith("["):
                try:
                    decrypted = _decrypt_cached(chat.api, enc, attachment, user_id)
                    if decrypted:
                        attachment = decrypted
                except:
                    pass
        
        message = Message(
            id=int(record["id"]),
            type=int(record["type"]),
            msg=message_text,
            attachment=attachment,
            v=v
        )
        
        new_chat = ChatContext(
            room=room,
            sender=sender,
            message=message,
            raw=record,
            api=chat.api,
            _bot_id=chat._bot_id
        )
        
        return new_chat
    
    except Exception as e:
        print(f"[thread_helper] ChatContext 생성 실패: {e}")
        return None


# =============================================================================
# 공개 API 함수
# =============================================================================

def get_thread_id(chat: ChatContext) -> Optional[int]:
    """
    현재 메시지의 threadId를 추출합니다.
    """
    try:
        supplement = chat.raw.get("supplement", "")
        user_id = int(chat.raw.get("user_id", chat.sender.id))
        
        if not supplement:
            return None
        
        data = _decrypt_supplement(chat, supplement, user_id)
        if data and "threadId" in data:
            return int(data["threadId"])
    except Exception as e:
        print(f"[thread_helper] threadId 추출 실패: {e}")
    
    return None


def is_reply_or_thread(chat: ChatContext) -> bool:
    """
    기존 답장(type=26) 또는 새 스레드 답장인지 확인합니다.
    """
    if chat.message.type == 26:
        return True
    return get_thread_id(chat) is not None


def is_thread_reply(func):
    """
    스레드 답장인지 확인하는 데코레이터. (기존 @is_reply 대체)
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        chat: ChatContext = args[0]
        
        # 1. 기존 답장 시스템
        if chat.message.type == 26:
            return func(*args, **kwargs)
        
        # 2. 새 스레드 시스템
        thread_id = get_thread_id(chat)
        if thread_id:
            return func(*args, **kwargs)
        
        chat.reply("메시지에 답장하여 요청하세요.")
        return None
    
    return wrapper


def get_thread_source(chat: ChatContext) -> Optional[ChatContext]:
    """
    스레드의 원본 메시지를 가져옵니다. (기존 chat.get_source 대체)
    """
    if chat.message.type == 26:
        return chat.get_source()
    
    thread_id = get_thread_id(chat)
    if not thread_id:
        return None
    
    try:
        query = "SELECT * FROM chat_logs WHERE id = ?"
        result = chat.api.query(query, [thread_id])
        
        if not result:
            return None
        
        record = result[0]
        return _make_chat_from_record(chat, record)
    
    except Exception as e:
        print(f"[thread_helper] 원본 메시지 조회 실패: {e}")
        return None


def get_thread_messages(chat: ChatContext, source_message_id: int, limit: int = 50) -> List[ChatContext]:
    """
    특정 메시지에 달린 모든 스레드 답장들을 가져옵니다.
    """
    try:
        chat_id = chat.room.id
        query = """
            SELECT * FROM chat_logs 
            WHERE chat_id = ? 
            AND id > ? 
            AND supplement IS NOT NULL 
            AND supplement != ''
            ORDER BY id ASC 
            LIMIT ?
        """
        result = chat.api.query(query, [chat_id, source_message_id, limit * 3])
        
        thread_replies = []
        for record in result:
            supplement = record.get("supplement", "")
            user_id = int(record.get("user_id", 0))
            
            data = _decrypt_supplement(chat, supplement, user_id)
            if data and data.get("threadId") == source_message_id:
                thread_chat = _make_chat_from_record(chat, record)
                if thread_chat:
                    thread_replies.append(thread_chat)
                
                if len(thread_replies) >= limit:
                    break
        
        return thread_replies
    
    except Exception as e:
        print(f"[thread_helper] 스레드 메시지 조회 실패: {e}")
        return []


def get_thread_context(chat: ChatContext, limit: int = 5) -> List[ChatContext]:
    """
    [대응 기능] 스레드의 전체 대화 맥락을 가져옵니다.
    
    Returns:
        [원본 메시지, ..., 현재 메시지] 형태의 리스트
    """
    source = get_thread_source(chat)
    if not source:
        return [chat]
    
    all_replies = get_thread_messages(chat, source.message.id, limit=20)
    context_replies = []
    
    if len(all_replies) > limit:
        context_replies = all_replies[-limit:]
    else:
        context_replies = all_replies
        
    result = [source] + context_replies
    
    if not any(r.message.id == chat.message.id for r in result):
        result.append(chat)
        
    return result


def estimate_reply_target(chat: ChatContext) -> ChatContext:
    """
    [대응 기능] 멘션(@닉네임)을 기반으로 실질적인 답장 대상을 추정합니다.
    """
    source = get_thread_source(chat)
    if not source:
        return chat
        
    msg_content = chat.message.msg
    if not msg_content or "@" not in msg_content:
        return source
    
    mentioned_names = re.findall(r"@(\S+)", msg_content)
    if not mentioned_names:
        return source
    
    replies = get_thread_messages(chat, source.message.id, limit=30)
    
    for reply in reversed(replies):
        sender_name = reply.sender.name
        for name_hint in mentioned_names:
            if name_hint in sender_name:
                return reply
                
    for name_hint in mentioned_names:
        if name_hint in source.sender.name:
            return source
            
    return source


# =============================================================================
# 편의 별칭
# =============================================================================

get_source_universal = get_thread_source


if __name__ == "__main__":
    print("thread_helper 모듈이 성공적으로 로드되었습니다.")
