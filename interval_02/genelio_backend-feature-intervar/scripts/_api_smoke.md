# API Smoke Test Results

- Base URL: `http://localhost:8000`
- Total calls: **22**
- Failures: **0**

| # | Endpoint | Method | Status | Elapsed |
|---|----------|--------|--------|---------|
| 1 | `/auth/users/` | POST | ✅ 201 | 0.318s |
| 2 | `/auth/jwt/create/` | POST | ✅ 200 | 0.129s |
| 3 | `/auth/jwt/verify/` | POST | ✅ 200 | 0.003s |
| 4 | `/auth/jwt/refresh/` | POST | ✅ 200 | 0.003s |
| 5 | `/auth/users/me/` | GET | ✅ 200 | 0.01s |
| 6 | `/auth/users/me/` | PATCH | ✅ 200 | 0.012s |
| 7 | `/auth/users/set_avatar/` | POST | ✅ 200 | 1.723s |
| 8 | `/auth/users/set_password/` | POST | ✅ 204 | 0.2s |
| 9 | `/auth/jwt/create/` | POST | ✅ 200 | 0.102s |
| 10 | `/chatbot/report/types/` | GET | ✅ 200 | 0.006s |
| 11 | `/chatbot/report/data/` | POST | ✅ 201 | 5.337s |
| 12 | `/chatbot/report/data/` | GET | ✅ 200 | 0.107s |
| 13 | `/chatbot/report/data/?report_type=oral` | GET | ✅ 200 | 0.034s |
| 14 | `/chatbot/report/data/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/` | GET | ✅ 200 | 0.029s |
| 15 | `/chatbot/chat/session/list/` | POST | ✅ 201 | 0.024s |
| 16 | `/chatbot/chat/session/list/` | GET | ✅ 200 | 0.022s |
| 17 | `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/` | GET | ✅ 200 | 0.021s |
| 18 | `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/` | PATCH | ✅ 200 | 0.019s |
| 19 | `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/send/` | POST | ✅ 201 | 0.829s |
| 20 | `/schema/` | GET | ✅ 200 | 0.205s |
| 21 | `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/` | DELETE | ✅ 204 | 0.032s |
| 22 | `/chatbot/report/data/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/` | DELETE | ✅ 204 | 0.018s |

## POST `/auth/users/`
_signup_

- Status: **201** (expected [201])
- Elapsed: 0.318s

**Request**
```json
{
  "email": "api+smoke+1776417603@example.com",
  "first_name": "Smoke",
  "last_name": "Test",
  "password": "***REDACTED***",
  "re_password": "***REDACTED***",
  "agreed_to_terms": true
}
```
**Response**
```json
{
  "id": 5,
  "email": "api+smoke+1776417603@example.com",
  "username": "",
  "first_name": "Smoke",
  "last_name": "Test",
  "avatar": null,
  "date_joined": "2026-04-17T09:20:03.640147Z"
}
```

## POST `/auth/jwt/create/`
_login_

- Status: **200** (expected [200])
- Elapsed: 0.129s

**Request**
```json
{
  "email": "api+smoke+1776417603@example.com",
  "password": "***REDACTED***"
}
```
**Response**
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTc3NzYyNzIwMywiaWF0IjoxNzc2NDE3NjAzLCJqdGkiOiIzYzBkZGYwNjZlYTk0NjhhYjdiZmQ2OWQ1MWJiZWYwZSIsInVzZXJfaWQiOjV9.9CObuJ7wtAGvhS9SL6YrppY9aLEihwyPITnUfU6olnE",
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc2NDIxMjAzLCJpYXQiOjE3NzY0MTc2MDMsImp0aSI6IjFiMTVjNzFjNzFhYjQ0MGE5NDczMjgyYmFjYTQzNWNiIiwidXNlcl9pZCI6NX0.NfR2RUeHdE1QGER8a-O96hTZWzDb3rz0XpGxT1wrb4Q"
}
```

## POST `/auth/jwt/verify/`
_jwt-verify_

- Status: **200** (expected [200])
- Elapsed: 0.003s

**Request**
```json
{
  "token": "***REDACTED***"
}
```
**Response**
```json
{}
```

## POST `/auth/jwt/refresh/`
_jwt-refresh_

- Status: **200** (expected [200])
- Elapsed: 0.003s

**Request**
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTc3NzYyNzIwMywiaWF0IjoxNzc2NDE3NjAzLCJqdGkiOiIzYzBkZGYwNjZlYTk0NjhhYjdiZmQ2OWQ1MWJiZWYwZSIsInVzZXJfaWQiOjV9.9CObuJ7wtAGvhS9SL6YrppY9aLEihwyPITnUfU6olnE"
}
```
**Response**
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc2NDIxMjAzLCJpYXQiOjE3NzY0MTc2MDMsImp0aSI6Ijc3OWYyYTQyZTk0NDRjNzViZjIxZDhlNDMxMzkxMGY0IiwidXNlcl9pZCI6NX0.aGnMmCYy_UUUHiOgaf63ZIPLJCfHRaVbSzdztxmZoFk"
}
```

## GET `/auth/users/me/`
_users-me-get_

- Status: **200** (expected [200])
- Elapsed: 0.01s

**Request**
```json
null
```
**Response**
```json
{
  "id": 5,
  "email": "api+smoke+1776417603@example.com",
  "username": "",
  "first_name": "Smoke",
  "last_name": "Test",
  "avatar": null,
  "date_joined": "2026-04-17T09:20:03.640147Z"
}
```

## PATCH `/auth/users/me/`
_users-me-patch_

- Status: **200** (expected [200])
- Elapsed: 0.012s

**Request**
```json
{
  "first_name": "Smoky"
}
```
**Response**
```json
{
  "id": 5,
  "email": "api+smoke+1776417603@example.com",
  "username": "",
  "first_name": "Smoky",
  "last_name": "Test",
  "avatar": null,
  "date_joined": "2026-04-17T09:20:03.640147Z"
}
```

## POST `/auth/users/set_avatar/`
_set_avatar_

- Status: **200** (expected [200])
- Elapsed: 1.723s

**Request**
```json
{
  "_files": [
    "avatar"
  ]
}
```
**Response**
```json
{
  "id": 5,
  "email": "api+smoke+1776417603@example.com",
  "username": "",
  "first_name": "Smoky",
  "last_name": "Test",
  "avatar": "/media/avatars/pixel.png",
  "date_joined": "2026-04-17T09:20:03.640147Z"
}
```

## POST `/auth/users/set_password/`
_set_password_

- Status: **204** (expected [204, 200])
- Elapsed: 0.2s

**Request**
```json
{
  "new_password": "***REDACTED***",
  "current_password": "***REDACTED***"
}
```
**Response**
```json
""
```

## POST `/auth/jwt/create/`
_login-after-pw-change_

- Status: **200** (expected [200])
- Elapsed: 0.102s

**Request**
```json
{
  "email": "api+smoke+1776417603@example.com",
  "password": "***REDACTED***"
}
```
**Response**
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTc3NzYyNzIwNSwiaWF0IjoxNzc2NDE3NjA1LCJqdGkiOiJmYWNmNDJhYmQ5ZWQ0MGY2YWQxNGNjNGE2NzViZGZkZSIsInVzZXJfaWQiOjV9.eZkPz3IN5g-anzH6aY8L7p34gI7VwN-IN5v8v5Jo708",
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc2NDIxMjA1LCJpYXQiOjE3NzY0MTc2MDUsImp0aSI6IjhkZTYwOWViZWJhNjQ1OGFiODhmNGRjODQ0ZjlhZGI0IiwidXNlcl9pZCI6NX0.ajJScO_VYrN10C65JU1MicmIny6U4OWtkd6ppODbevg"
}
```

## GET `/chatbot/report/types/`
_report-types_

- Status: **200** (expected [200])
- Elapsed: 0.006s

**Request**
```json
null
```
**Response**
```json
[
  {
    "value": "wgs",
    "label": "WGS",
    "implemented": false
  },
  {
    "value": "wes",
    "label": "WES",
    "implemented": false
  },
  {
    "value": "oral",
    "label": "Oral Microbiome",
    "implemented": true
  },
  {
    "value": "gut",
    "label": "Gut Microbiome",
    "implemented": true
  },
  {
    "value": "skin",
    "label": "Skin Microbiome",
    "implemented": true
  },
  {
    "value": "vaginal",
    "label": "Vaginal Microbiome",
    "implemented": true
  }
]
```

## POST `/chatbot/report/data/`
_report-upload_

- Status: **201** (expected [201])
- Elapsed: 5.337s

**Request**
```json
{
  "report_type": "oral",
  "_files": [
    "file"
  ]
}
```
**Response**
```json
{
  "_truncated": true,
  "_preview": "{\"id\": \"9ed2fb38-2709-43e1-b5c9-a97ba95b43fd\", \"report_type\": \"oral\", \"original_filename\": \"Oral Report from Arshit Arora.pdf\", \"status\": \"ready\", \"error_message\": \"\", \"file_url\": \"http://localhost:8000/media/reports/5/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/Oral_Report_from_Arshit_Arora.pdf\", \"created_at\": \"2026-04-17T09:20:06.009577Z\", \"updated_at\": \"2026-04-17T09:20:06.876529Z\", \"parsed_data\": {\"status\": \"ok\", \"site\": \"oral\", \"report\": {\"site\": \"oral\", \"display\": \"Oral Microbiome\", \"patient\": {\"Sample Type\": \"Oral swab\", \"Provincial Health Number\": \"Not Provided\"}, \"diversity\": {\"score\": 3.581, \"range\": \"1.2-3.0\", \"range_low\": 1.2, \"range_high\": 3.0, \"status\": \"above_range\"}, \"top_organisms\": [{\"name\": \"Streptococcus mitis\", \"abundance\": 15.21, \"abundance_raw\": \"15.21%\", \"reference\": \"0.11%-1.10%\", \"reference_low\": 0.11, \"reference_high\": 1.1, \"status\": \"above_range\", \"significance\": \"It is an early coloniser of oral cavity and colonises infant\\u2019s oral cavity soon after birth. It is a commensal bacterium and one of the dominant species in children up to the age of 4 years. It produces hydrogen peroxide which prevents colonisation of harmful pathogens\"}, {\"name\": \"Veillonella parvula\", \"abundance\": 4.87, \"abundance_raw\": \"4.87%\", \"reference\": \"0.58%-3.62%\", \"reference_low\": 0.58, \"reference_high\": 3.62, \"status\": \"above_range\", \"significance\": \"V. parvula is a gram-negative coccus which exists as a member of the oral microbiome. This organism can lead to periodontist and different systemic infections. Introductory studies conducted on role of this bacterium in human health pointed towards its increased abundance in patients suffering from pancreatic ductal carcinoma\"}, {\"name\": \"Rothia SGB49305\", \"abundance\": 4.48, \"abundance_raw\": \"4.48%\", \"reference\": \"ND\", \"reference_low\": null, \"reference_high\": null, \"status\": \"no_reference\", \"significance\": \"Rothia is a gram-positive and commensal bacteria. It helps in nitric oxide and gluten degradation. Some species may associate with oral infection\"}, {\"name\": \"Actinomyces johnsonii\", \"abundance\": 3.95, \"abundance_raw\": \"3.95%\", \"reference\": \"0.01%-0.10%\", \"reference_low\": 0.01, \"reference_high\": 0.1, \"status\": \"above_range\", \"significance\": \"Actinomyces johnsonii is a common oral commensal, often found in dental plaque and healthy gingival tissues. It has been linked to recurrent aphthous stomatitis (RAS), showing increased abundance and cytotoxic effects on oral epithelial cells\"}, {\"name\": \"Actinomyces massiliensis\", \"abundance\": 2.78, \"abundance_raw\": \"2.78%\", \"reference\": \"0.06%-0.86%\", \"reference_low\": 0.06, \"reference_high\": 0.86, \"status\": \"above_range\", \"significance\": \"The Actinomyces spp. are found to be abundant in healthy individuals. Decreased
```

## GET `/chatbot/report/data/`
_report-list_

- Status: **200** (expected [200])
- Elapsed: 0.107s

**Request**
```json
null
```
**Response**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "9ed2fb38-2709-43e1-b5c9-a97ba95b43fd",
      "report_type": "oral",
      "original_filename": "Oral Report from Arshit Arora.pdf",
      "status": "ready",
      "error_message": "",
      "file_url": "http://localhost:8000/media/reports/5/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/Oral_Report_from_Arshit_Arora.pdf",
      "created_at": "2026-04-17T09:20:06.009577Z",
      "updated_at": "2026-04-17T09:20:06.876529Z"
    }
  ]
}
```

## GET `/chatbot/report/data/?report_type=oral`
_report-list-filter_

- Status: **200** (expected [200])
- Elapsed: 0.034s

**Request**
```json
null
```
**Response**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "9ed2fb38-2709-43e1-b5c9-a97ba95b43fd",
      "report_type": "oral",
      "original_filename": "Oral Report from Arshit Arora.pdf",
      "status": "ready",
      "error_message": "",
      "file_url": "http://localhost:8000/media/reports/5/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/Oral_Report_from_Arshit_Arora.pdf",
      "created_at": "2026-04-17T09:20:06.009577Z",
      "updated_at": "2026-04-17T09:20:06.876529Z"
    }
  ]
}
```

## GET `/chatbot/report/data/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/`
_report-detail_

- Status: **200** (expected [200])
- Elapsed: 0.029s

**Request**
```json
null
```
**Response**
```json
{
  "_truncated": true,
  "_preview": "{\"id\": \"9ed2fb38-2709-43e1-b5c9-a97ba95b43fd\", \"report_type\": \"oral\", \"original_filename\": \"Oral Report from Arshit Arora.pdf\", \"status\": \"ready\", \"error_message\": \"\", \"file_url\": \"http://localhost:8000/media/reports/5/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/Oral_Report_from_Arshit_Arora.pdf\", \"created_at\": \"2026-04-17T09:20:06.009577Z\", \"updated_at\": \"2026-04-17T09:20:06.876529Z\", \"parsed_data\": {\"site\": \"oral\", \"report\": {\"site\": \"oral\", \"display\": \"Oral Microbiome\", \"patient\": {\"Sample Type\": \"Oral swab\", \"Provincial Health Number\": \"Not Provided\"}, \"diversity\": {\"range\": \"1.2-3.0\", \"score\": 3.581, \"status\": \"above_range\", \"range_low\": 1.2, \"range_high\": 3.0}, \"full_text\": \"\\nOral Microbiome Report\\nClient Information\\nName: Date of Birth:\\nSample Type: Oral swab Provincial He alth Number: Not Provided\\nCollection Date: Report Date:\\nReceive Date:\\nNote: Current evidence does not support the use of microbiome testing results for clinical diagnosis or treatment decisions.\\nThe findings may show links between certain types of microbiome changes and specific health conditions, but these links do\\nnot mean that one causes the other. Your results should therefore be viewed as informative rather than diagnostic, and any\\nhealth concerns should be discussed with your healthcare professional.\\nClient Name: Collection Date: Version 2 1 | Pa ge\\nWhat is the oral microbiome, and why is it important for your health?\\nThe oral cavity harbours a complex and diverse community of microorganisms, collectively forming the oral\\nmicrobiome. This intricate network profoundly influences various aspects of our lives, starting from the moment\\nof birth when the assembly of this microbiome begins. Throughout our lives, these oral microbes play crucial\\nroles in processes such as digestion and the synthesis of essential compounds that contribute to nutrient\\nabsorption.\\nThe oral microbiome's impact extends deeply into our overall health, intricately shaping the health of our teeth,\\ngums, and even influencing our systemic well-being. From breaking down food particles during the initial stages\\nof digestion to actively participating in immune system education, the oral microbiome contributes significantly\\nto our health.\\nAnalyzing the oral microbiome involves studying the genes of various microorganisms, including bacteria,\\nviruses, fungi, and other microbes present in the oral cavity. This comprehensive examination generates a\\ndetailed report outlining the microbial composition within the oral environment. Beyond identification, this\\nanalysis can detect genes related to antibiotic resistance, aiding healthcare professionals in determining effective\\ntreatment options for oral infections.\\nThe report not only lists the types of microbes but also provides detailed information about potentially harmful\\nones, offering insights 
```

## POST `/chatbot/chat/session/list/`
_session-create_

- Status: **201** (expected [201])
- Elapsed: 0.024s

**Request**
```json
{
  "title": "API smoke test",
  "report_id": "9ed2fb38-2709-43e1-b5c9-a97ba95b43fd"
}
```
**Response**
```json
{
  "id": "a3fba0b4-748b-4426-863a-d8d930920373",
  "title": "API smoke test",
  "report": {
    "id": "9ed2fb38-2709-43e1-b5c9-a97ba95b43fd",
    "report_type": "oral",
    "status": "ready"
  },
  "message_count": 0,
  "created_at": "2026-04-17T09:20:11.520978Z",
  "updated_at": "2026-04-17T09:20:11.520993Z"
}
```

## GET `/chatbot/chat/session/list/`
_session-list_

- Status: **200** (expected [200])
- Elapsed: 0.022s

**Request**
```json
null
```
**Response**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "a3fba0b4-748b-4426-863a-d8d930920373",
      "title": "API smoke test",
      "report": {
        "id": "9ed2fb38-2709-43e1-b5c9-a97ba95b43fd",
        "report_type": "oral",
        "status": "ready"
      },
      "message_count": 0,
      "created_at": "2026-04-17T09:20:11.520978Z",
      "updated_at": "2026-04-17T09:20:11.520993Z"
    }
  ]
}
```

## GET `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/`
_session-detail_

- Status: **200** (expected [200])
- Elapsed: 0.021s

**Request**
```json
null
```
**Response**
```json
{
  "id": "a3fba0b4-748b-4426-863a-d8d930920373",
  "title": "API smoke test",
  "report": {
    "id": "9ed2fb38-2709-43e1-b5c9-a97ba95b43fd",
    "report_type": "oral",
    "status": "ready"
  },
  "message_count": 0,
  "created_at": "2026-04-17T09:20:11.520978Z",
  "updated_at": "2026-04-17T09:20:11.520993Z",
  "messages": []
}
```

## PATCH `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/`
_session-rename_

- Status: **200** (expected [200])
- Elapsed: 0.019s

**Request**
```json
{
  "title": "API smoke renamed"
}
```
**Response**
```json
{
  "id": "a3fba0b4-748b-4426-863a-d8d930920373",
  "title": "API smoke renamed",
  "report": {
    "id": "9ed2fb38-2709-43e1-b5c9-a97ba95b43fd",
    "report_type": "oral",
    "status": "ready"
  },
  "message_count": 0,
  "created_at": "2026-04-17T09:20:11.520978Z",
  "updated_at": "2026-04-17T09:20:11.586303Z"
}
```

## POST `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/send/`
_session-send_

- Status: **201** (expected [201])
- Elapsed: 0.829s

**Request**
```json
{
  "content": "Give me a one-sentence summary of my oral microbiome health."
}
```
**Response**
```json
{
  "user": {
    "id": "724e420c-0757-4696-8005-1584bddacaf5",
    "role": "user",
    "content": "Give me a one-sentence summary of my oral microbiome health.",
    "created_at": "2026-04-17T09:20:11.610553Z"
  },
  "assistant": {
    "id": "1c8565d3-eb6e-4662-aac8-9a321849f0e3",
    "role": "assistant",
    "content": "Your oral microbiome shows a higher-than-healthy diversity index (SDIV) and several bacterial species are above their normal ranges, indicating potential imbalances that may warrant further attention and lifestyle adjustments for optimal oral health.",
    "created_at": "2026-04-17T09:20:12.406762Z"
  }
}
```

## GET `/schema/`
_openapi-schema_

- Status: **200** (expected [200])
- Elapsed: 0.205s

**Request**
```json
null
```
**Response**
```json
"openapi: 3.0.3\ninfo:\n  title: Genelio API\n  version: 0.1.0\n  description: Backend for the Genelio AI health-report assistant.\npaths:\n  /auth/jwt/create/:\n    post:\n      operationId: auth_jwt_create_create\n      description: |-\n        Takes a set of user credentials and returns an access and refresh JSON web\n        token pair to prove the authentication of those credentials.\n      tags:\n      - auth\n      requestBody:\n        content:\n          application/json:\n            schema:\n           "
```

## DELETE `/chatbot/chat/session/a3fba0b4-748b-4426-863a-d8d930920373/`
_session-delete_

- Status: **204** (expected [204])
- Elapsed: 0.032s

**Request**
```json
null
```
**Response**
```json
""
```

## DELETE `/chatbot/report/data/9ed2fb38-2709-43e1-b5c9-a97ba95b43fd/`
_report-delete_

- Status: **204** (expected [204])
- Elapsed: 0.018s

**Request**
```json
null
```
**Response**
```json
""
```
