# 날짜 기반 국내 여행 추천 CLI (travel_planner)

날짜를 입력하면 **LLM(Google Gemini)** 이 여행지·날씨·행사를 추천하고, **지도 API(Kakao Local)** 로 그 지역 맛집을 검색합니다. 그다음 다시 LLM이 **최종 여행 리포트(Markdown)** 를 만들어 주는 Python CLI 프로그램입니다.

## 1. 프로그램 개요

```
--date ─▶ [1/3] Gemini (POST) ─▶ 1차 추천 JSON
                                 { recommended_city, weather, events[], reason }
                                        │ recommended_city
                                        ▼
          [2/3] Kakao Local (GET) ─▶ 맛집 N곳 [{ name, address, category, url, x, y }]
                                        │
                                        ▼
          [3/3] Gemini (POST) ─▶ 1차 JSON + 맛집 목록 → Markdown 리포트
                                        │
                                        ▼
          results/YYYY-MM-DD_raw.json  +  results/YYYY-MM-DD_travel_plan.md
```

| 구분 | 사용 API | HTTP 메서드 | 인증 방식 |
|---|---|---|---|
| LLM | Google Gemini API (`generateContent`) | POST (프롬프트를 요청 본문 JSON으로 전송) | 헤더 `x-goog-api-key` |
| 지도/장소 | Kakao Local 키워드 검색 (`/v2/local/search/keyword.json`) | GET (검색어를 쿼리스트링으로 전송) | 헤더 `Authorization: KakaoAK {키}` |

## 2. 실행 환경 및 설치

- Python 3.10 이상

```bash
pip install -r requirements.txt
```

## 3. API 키 설정 방법

### 3-1. 키 발급
- **Gemini API 키**: [Google AI Studio](https://aistudio.google.com/apikey)에서 `Create API key`를 눌러 발급합니다. 무료 티어로도 사용할 수 있습니다.
  - Gemini 앱 구독(Gemini Pro 등)과 API 키는 별개입니다. 반드시 AI Studio에서 API 키를 따로 발급해야 합니다.
- **Kakao REST API 키**: [Kakao Developers](https://developers.kakao.com)에서 발급합니다.
  1. 내 애플리케이션 > 애플리케이션 추가하기
  2. 앱 키 > **REST API 키**를 복사합니다.
  3. **카카오맵 > 사용 설정 > 상태 ON**으로 바꿉니다. 이 설정을 하지 않으면 403 오류가 납니다.

### 3-2. `.env` 파일 만들기 (권장)
```bash
cp .env.example .env        # Windows: copy .env.example .env
```
`.env` 파일을 열고 키를 입력합니다.
```
GEMINI_API_KEY=발급받은_키
KAKAO_REST_API_KEY=발급받은_키
GEMINI_MODEL=              # 선택, 비워두면 gemini-3.5-flash-lite
```

### 3-3. 또는 환경변수로 설정 (현재 터미널 세션에만 적용)
```bash
# macOS / Linux
export GEMINI_API_KEY="YOUR_KEY"
export KAKAO_REST_API_KEY="YOUR_KEY"
```
```powershell
# Windows PowerShell
$env:GEMINI_API_KEY="YOUR_KEY"
$env:KAKAO_REST_API_KEY="YOUR_KEY"
```

키가 하나라도 없으면 프로그램이 즉시 종료되고, 설정 방법 안내가 출력됩니다.

## 4. 실행 방법

```bash
python travel_planner.py --date "2026-10-03"
```

- `--date "YYYY-MM-DD"`: **필수.** 여행 날짜입니다. 형식이 틀렸거나 존재하지 않는 날짜이면 사용법을 출력하고 종료합니다.

실행 예시:
```
[1/3] 1차 추천 생성 중(LLM)...
  - recommended_city: "강릉"
[2/3] 맛집 검색 중(지도/장소 API)...
  - 맛집 5곳 검색 완료
[3/3] 최종 리포트 생성 중(LLM)...
  - 리포트 생성 완료

완료! results/2026-10-03_travel_plan.md 를 확인하세요.
      원본 데이터: results/2026-10-03_raw.json
```

## 5. 결과물 확인 방법

실행하면 `results/` 폴더에 두 파일이 생성됩니다.

| 파일 | 내용 |
|---|---|
| `results/YYYY-MM-DD_raw.json` | 원본 데이터: `first_recommendation`(1차 추천 파싱 결과), `restaurants`(맛집 리스트, 0건 가능), `errors`(오류 목록) |
| `results/YYYY-MM-DD_travel_plan.md` | 최종 리포트: 추천 지역 / 추천 이유 / 날씨 요약 / 행사·축제 / 맛집 추천 / 1일 일정(오전·오후·저녁) / 오류 요약 |

`raw.json`의 구조는 다음과 같습니다.
```json
{
  "date": "2026-10-03",
  "first_recommendation": {
    "recommended_city": "강릉",
    "weather": "...",
    "events": ["..."],
    "reason": "..."
  },
  "restaurants": [
    {"name": "...", "address": "...", "category": "...", "url": "...", "x": 128.9, "y": 37.7}
  ],
  "errors": [
    {"step": "place_search", "type": "EMPTY_RESULT", "message": "0 results for query=..."}
  ]
}
```
- `x`는 경도(lng), `y`는 위도(lat)입니다.

## 6. 에러 처리 정책

| 상황 | 동작 | errors.type |
|---|---|---|
| API 키 미설정 | 즉시 종료하고 설정 방법을 안내합니다. | - |
| 날짜 형식 오류 | 사용법을 출력하고 종료합니다(argparse). | - |
| LLM 응답 JSON 파싱 또는 스키마 검증 실패 | "필수 키만 다시 JSON으로" 프롬프트로 **재시도 1회**만 합니다(무한 재시도 금지). 그래도 실패하면 중단합니다. | `PARSE_ERROR` |
| LLM 인증 실패 (401/403, 잘못된 키) | 1단계라면 중단하고, 원인을 raw.json에 기록합니다. | `AUTH_ERROR` |
| 지도 API 인증 실패 (401/403) | 맛집 섹션을 "데이터 없음"으로 처리하고 **리포트 생성은 계속 진행**합니다. | `AUTH_ERROR` |
| 쿼터 초과 (429) | 위와 동일합니다. | `QUOTA_ERROR` |
| 네트워크 오류 / 타임아웃 | 위와 동일합니다. | `NETWORK_ERROR` |
| 맛집 검색 결과 0건 | 키워드를 바꿔 재시도하지 않고 다음 단계로 진행합니다. | `EMPTY_RESULT` |
| 3단계 리포트 LLM 실패 | 템플릿 리포트로 대체해 파일을 반드시 생성합니다. | 해당 오류 |

- 오류는 모두 `{step, type, message}` 형태로 누적됩니다. `raw.json`과 리포트의 "오류 요약(errors)" 섹션에 똑같이 기록됩니다. 오류가 없으면 빈 리스트입니다.
- 리포트의 오류 요약 섹션은 LLM이 쓰지 않고 프로그램이 직접 붙입니다. 그래서 항상 정확한 값이 들어갑니다.

## 7. API 키 유출 주의사항

- **API 키를 코드, README, 로그, 결과 파일에 직접 쓰지 마세요.** 이 프로그램은 키를 `.env` 또는 환경변수에서만 읽습니다.
- `.env`는 `.gitignore`에 등록되어 있어 Git에 커밋되지 않습니다. 커밋하기 전에 `git status`로 `.env`가 목록에 없는지 확인하세요.
- `.env.example`에는 **키 이름만** 두고 값은 비워 둡니다.
- 두 API 모두 키를 URL이 아닌 **HTTP 헤더**로 보냅니다. 그래서 에러 메시지나 로그에 URL이 찍혀도 키가 노출되지 않습니다. 추가로, 오류 메시지에 키 값이 섞이면 `***`로 가리는 처리가 들어 있습니다.
- 키가 유출되었다면 즉시 콘솔(AI Studio, Kakao Developers)에서 **키를 폐기하고 재발급**하세요. 키를 코드에 쓰지 않았기 때문에 `.env` 값만 바꾸면 되고, 코드는 수정할 필요가 없습니다.

**왜 `.env`나 환경변수로 관리하나요?**
1. 협업하거나 코드를 공유할 때 실수로 키가 공개되는 것을 막습니다.
2. 키를 교체해도 코드를 수정할 필요가 없어서 운영과 배포에 유리합니다.
3. 과금이나 쿼터가 걸린 서비스에서 키가 도용되어 생기는 사고를 예방합니다.
