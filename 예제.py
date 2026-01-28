from iris import ChatContext, Bot
from helper.thread_helper import (
    is_thread_reply, 
    get_thread_source, 
    get_thread_context, 
    estimate_reply_target
)

# 봇 주소 설정 (환경에 맞게 변경)
bot = Bot("http://localhost:3000")

@bot.on_event("message")
def on_message(chat: ChatContext):
    # 1. 스레드 원본 확인 명령어
    if chat.message.command == "!원본확인":
        handle_source_check(chat)
        
    # 2. 스레드 문맥 확인 명령어
    elif chat.message.command == "!문맥":
        handle_context_check(chat)
        
    # 3. 답장 대상 추정 명령어
    elif chat.message.command == "!누구에게":
        handle_target_guess(chat)


# 1. 기본: 스레드 답장 감지 및 원본 확인
@is_thread_reply
def handle_source_check(chat: ChatContext):
    # 원본 메시지 가져오기
    source = get_thread_source(chat)
    if not source:
        chat.reply("원본 메시지를 찾을 수 없습니다.")
        return

    chat.reply(
        f"📌 원본 정보\n"
        f"작성자: {source.sender.name}\n"
        f"내용: {source.message.msg}"
    )


# 2. 심화: 스레드 문맥(흐름) 파악하기
@is_thread_reply
def handle_context_check(chat: ChatContext):
    # 전체 대화 흐름 가져오기 (최근 5개)
    context = get_thread_context(chat, limit=5)
    
    lines = ["📚 대화 흐름 파악중..."]
    for msg in context:
        prefix = "👉" if msg.message.id == chat.message.id else "  "
        lines.append(f"{prefix} [{msg.sender.name}] {msg.message.msg}")
        
    chat.reply("\n".join(lines))


# 3. 심화: 멘션으로 답장 대상 추정하기 (리리플 흉내)
@is_thread_reply
def handle_target_guess(chat: ChatContext):
    # 멘션(@닉네임)이 있다면 해당 유저의 최근 메시지를 찾음
    target = estimate_reply_target(chat)
    source = get_thread_source(chat)
    
    if target.message.id == source.message.id:
        msg = "원본 작성자에게 답장한 것으로 보입니다."
    else:
        msg = f"'{target.sender.name}'님의 메시지('{target.message.msg}')에 대한 답장으로 추정됩니다."
        
    chat.reply(msg)


if __name__ == "__main__":
    bot.run()
