import os
import requests
import logging
import json
from ai.prompts import get_system_prompt

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

def stream_ai_response(user_message, mode="fast", conversation_history=None):
    """
    يرسل للـ Groq ويرجع الرد كـ Generator عشان Streaming
    """
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY not set")
        yield "خطأ: مفتاح الـ AI مش مضبوط في السيرفر"
        return

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    # نبني الرسائل: system + التاريخ + الرسالة الجديدة
    messages = [{"role": "system", "content": get_system_prompt(mode)}]

    if conversation_history:
        messages.extend(conversation_history[-6:]) # آخر 6 رسايل بس عشان التوكن

    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": "llama-3.1-70b-versatile", # موديل Groq القوي
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 2048
    }

    try:
        response = requests.post(GROQ_API_URL, headers=headers, json=payload, stream=True, timeout=60)

        if response.status_code!= 200:
            logger.error(f"Groq API error: {response.status_code} - {response.text}")
            yield f"خطأ من سيرفر الذكاء الاصطناعي: {response.status_code}"
            return

        # نقرأ الـ stream سطر سطر
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith('data: '):
                    json_str = decoded_line[6:] # شيل 'data: '
                    if json_str.strip() == '[DONE]':
                        break
                    try:
                        chunk = json.loads(json_str)
                        delta = chunk['choices'][0]['delta'].get('content', '')
                        if delta:
                            yield delta
                    except json.JSONDecodeError:
                        continue

    except requests.exceptions.RequestException as e:
        logger.error(f"Groq request failed: {e}")
        yield "عذراً، السيرفر مشغول حالياً. جرب بعد شوية"
    except Exception as e:
        logger.error(f"AI Service error: {e}")
        yield "صار خطأ غير متوقع"
