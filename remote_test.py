import requests, json
r = requests.post('http://localhost:8000/api/ai/chat',
    json={'message':'How many variants are in the database?','history':[],'max_rows':5},
    timeout=30)
d = r.json()
print('Type:', d.get('type'))
print('Response:', d.get('response','')[:300])
print('SQL:', d.get('sql',''))
