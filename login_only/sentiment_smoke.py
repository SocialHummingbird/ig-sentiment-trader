from openai import OpenAI
import os

client = OpenAI()  # uses OPENAI_API_KEY from env
prompt = "Headline: Inflation cools to 2.3% YoY, stocks rally. Rate cuts odds rise.\nRate the market sentiment from -1 (very bearish) to +1 (very bullish). Return JSON with keys: score,label,reason."

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    temperature=0,
    messages=[
        {"role":"system","content":"You are a precise market sentiment rater."},
        {"role":"user","content":prompt}
    ]
)

print(resp.choices[0].message.content)
