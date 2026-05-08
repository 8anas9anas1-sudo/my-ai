import os
from flask import Flask, request, render_template_string
import requests

app = Flask(__name__)

API_KEY = os.environ.get("GROQ_API_KEY")

HTML = """
<!DOCTYPE html>
<html dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0A0A0A">
<title>✨ anas Wadi ✨</title>

<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');

*{
margin:0;
padding:0;
box-sizing:border-box;
}

body{
background: linear-gradient(135deg, #0A0A0A 0%, #1a1a2e 100%);
color:#fff;
font-family: 'Tajawal', Arial, sans-serif;
min-height:100vh;
display:flex;
flex-direction:column;
}

.header{
background:rgba(0,0,0,0.4);
backdrop-filter:blur(10px);
padding:20px;
text-align:center;
border-bottom:1px solid rgba(255,255,255,0.1);
position:sticky;
top:0;
z-index:10;
}

.header h1{
font-size:28px;
font-weight:900;
background: linear-gradient(90deg, #00ff99, #00d4ff, #7a5cff);
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
text-shadow: 0 0 30px rgba(0,255,153,0.3);
}

.chat-container{
flex:1;
padding:20px;
max-width:800px;
width:100%;
margin:0 auto;
overflow-y:auto;
}

.message{
margin:15px 0;
animation: slideIn 0.3s ease;
}

@keyframes slideIn{
from{opacity:0; transform:translateY(10px);}
to{opacity:1; transform:translateY(0);}
}

.user-msg{
background:linear-gradient(135deg, #00ff99 0%, #00d4ff 100%);
color:#000;
padding:15px 20px;
border-radius:20px 20px 5px 20px;
margin-left:auto;
max-width:80%;
font-weight:500;
box-shadow:0 5px 15px rgba(0,255,153,0.3);
}

.ai-msg{
background:rgba(255,255,255,0.05);
backdrop-filter:blur(10px);
border:1px solid rgba(255,255,255,0.1);
padding:15px 20px;
border-radius:20px 20px 20px 5px;
max-width:80%;
line-height:1.8;
white-space:pre-wrap;
box-shadow:0 5px 15px rgba(0,0,0,0.3);
}

.input-area{
background:rgba(0,0,0,0.4);
backdrop-filter:blur(10px);
padding:15px;
border-top:1px solid rgba(255,255,255,0.1);
position:sticky;
bottom:0;
}

.input-wrapper{
max-width:800px;
margin:0 auto;
display:flex;
gap:10px;
}

textarea{
flex:1;
background:rgba(255,255,255,0.05);
border:1px solid rgba(255,255,255,0.1);
color:white;
border-radius:15px;
padding:15px;
font-size:16px;
font-family: 'Tajawal', Arial;
resize:none;
height:55px;
max-height:120px;
}

textarea:focus{
outline:none;
border-color:#00ff99;
box-shadow:0 0 15px rgba(0,255,153,0.2);
}

button{
background:linear-gradient(135deg, #00ff99 0%, #00d4ff 100%);
border:none;
border-radius:15px;
padding:0 25px;
font-size:18px;
font-weight:700;
cursor:pointer;
color:#000;
transition:all 0.3s;
}

button:hover{
transform:scale(1.05);
box-shadow:0 5px 20px rgba(0,255,153,0.5);
}

button:active{
transform:scale(0.95);
}

.welcome{
text-align:center;
padding:40px 20px;
opacity:0.7;
}

.welcome h2{
font-size:24px;
margin-bottom:10px;
}
</style>
</head>

<body>
<div class="header">
<h1>✨ anas Wadi ✨</h1>
</div>

<div class="chat-container">
{% if not user_message %}
<div class="welcome">
<h2>مرحبا بك في anas Wadi</h2>
<p>مساعدك الذكي الشخصي. اسأل أي شيء</p>
</div>
{% endif %}

{% if user_message %}
<div class="message">
<div class="user-msg">{{user_message}}</div>
</div>
{% endif %}

{% if response %}
<div class="message">
<div class="ai-msg">{{response}}</div>
</div>
{% endif %}
</div>

<div class="input-area">
<form method="POST" class="input-wrapper">
<textarea name="message" placeholder="اكتب رسالتك هنا..." required onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();this.form.submit();}"></textarea>
<button type="submit">إرسال</button>
</form>
</div>

<script>
// Auto scroll to bottom
window.scrollTo(0, document.body.scrollHeight);
</script>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def home():
    response_text = ""
    user_message = ""
    
    if request.method == "POST":
        user_message = request.form.get("message", "").strip()
        
        if not user_message:
            response_text = "اكتب شي الأول 😊"
        elif not API_KEY:
            response_text = "⚠️ مفتاح API مش مضاف. ضيف GROQ_API_KEY في المتغيرات البيئية على Render"
        else:
            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": user_message}]
            }
            
            try:
                response = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=30
                )
                result = response.json()
                
                if response.status_code == 200 and "choices" in result:
                    response_text = result["choices"][0]["message"]["content"]
                else:
                    response_text = f"خطأ من السيرفر: {result.get('error', {}).get('message', 'مشكلة غير معروفة')}"
                    
            except requests.exceptions.Timeout:
                response_text = "⏱️ الطلب خد وقت طويل. جرب مرة ثانية"
            except Exception as e:
                response_text = f"صار خطأ: {str(e)}"

    return render_template_string(HTML, response=response_text, user_message=user_message)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
