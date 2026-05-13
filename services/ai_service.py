import os
import requests
import logging

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self):
        self.api_key = os.environ.get('GROQ_API_KEY')
        self.api_url = "https://api.groq.com/openai/v1/chat/completions"

        if not self.api_key:
            logger.warning("GROQ_API_KEY not found in environment")

    def get_response(self, message, user_email=None):
        try:
            if not self.api_key:
                return "عذراً، مفتاح الـ AI مش مضبوط. كلّم الأدمن"

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {
                        "role": "system",
                        "content": "أنت anas wadi، مساعد ذكي ودود تتكلم باللهجة الليبية. اسمك anas wadi. جاوب باختصار وبأسلوب حلو ومرح. استخدم كلمات ليبية زي: هلبا، باهي، شن جو، تي، كيف صار."
                    },
                    {
                        "role": "user",
                        "content": message
                    }
                ],
                "temperature": 0.8,
                "max_tokens": 1000
            }

            response = requests.post(self.api_url, headers=headers, json=data, timeout=30)
            response.raise_for_status()

            result = response.json()
            ai_response = result['choices'][0]['message']['content']
            return ai_response

        except requests.exceptions.Timeout:
            logger.error("Groq API timeout")
            return "عذراً، خذيت وقت طويل. جرب تاني"
        except requests.exceptions.RequestException as e:
            logger.error(f"Groq API error: {e}")
            return "عذراً، صار خطأ في الاتصال بالـ AI"
        except Exception as e:
            logger.error(f"AI Service error: {e}")
            return "عذراً، صار خطأ غير متوقع"
