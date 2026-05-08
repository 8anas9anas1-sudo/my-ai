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
<title>My AI</title>

<style>
body{
background:#111;
color:white;
font-family:Arial;
padding:20px;
max-width:700px;
margin:auto;
}

h1{
text-align:center;
}

textarea{
width:100%;
height:140px;
background:#222;
color:white;
border:none;
border-radius:10px;
padding:15px;
font-size:18px;
box-sizing:border-box;
}

button{
width:100%;
padding:15px;
margin-top:10px;
border:none;
border-radius:10px;
background:#00ff99;
font-size:20px;
font-weight:bold;
cursor:pointer;
}

.response{
margin-top:20px;
background:#222;
padding:15px;
border-radius:10px;
white-space:pre-wrap;
line-height:1.7;
}
</style>
</head>

<body>

<h1>🔥 My AI</h1>

<form method="POST">
<textarea name="message" placeholder="اكتب أي شيء..."></textarea>
<button type="submit">إرسال</button>
</form>

{% if response %}
<div class="response">
{{response}}
</div>
{% endif %}

</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def home():

    response_text = ""

    if request.method == "POST":

        user_message = request.form["message"]

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "model": "openai/gpt-oss-120b",
            "messages": [
                {
                    "role": "user",
                    "content": user_message
                }
            ]
        }

        try:

            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data
            )

            result = response.json()

            response_text = result["choices"][0]["message"]["content"]

        except Exception as e:

            response_text = str(e)

    return render_template_string(
        HTML,
        response=response_text
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
