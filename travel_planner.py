"""
travel_planner.py - 날짜 기반 국내 여행 추천 CLI

흐름:
  [1/3] LLM(Gemini)  : date -> 1차 추천 JSON (recommended_city, weather, events, reason)
  [2/3] Kakao Local  : recommended_city -> 맛집 N곳
  [3/3] LLM(Gemini)  : 1차 JSON + 맛집 목록 -> Markdown 리포트

사용법:
  python travel_planner.py --date "2026-10-03"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

LLM_TIMEOUT = 60      # 초
KAKAO_TIMEOUT = 10    # 초
PLACE_COUNT = 5       # 맛집 권장 5곳


# ─────────────────────────────────────────────
# 에러 관리
# ─────────────────────────────────────────────
class ApiError(Exception):
    """외부 API 호출 실패를 표현하는 예외. type 은 errors 배열에 그대로 기록된다."""

    def __init__(self, err_type: str, message: str):
        super().__init__(message)
        self.type = err_type
        self.message = message


def add_error(errors: list, step: str, err_type: str, message: str) -> None:
    errors.append({"step": step, "type": err_type, "message": mask_secrets(message)})


def mask_secrets(text: str) -> str:
    """혹시라도 메시지에 키 값이 섞여 들어가면 가린다 (로그/결과 파일 유출 방지)."""
    for name in ("GEMINI_API_KEY", "KAKAO_REST_API_KEY"):
        value = os.getenv(name)
        if value:
            text = text.replace(value, "***")
    return text


def classify_http_error(status: int) -> str:
    if status in (401, 403):
        return "AUTH_ERROR"
    if status == 429:
        return "QUOTA_ERROR"
    return "HTTP_ERROR"


# ─────────────────────────────────────────────
# CLI / 환경 변수
# ─────────────────────────────────────────────
def valid_date(value: str) -> str:
    """argparse type 함수: YYYY-MM-DD 형식 + 실제 존재하는 날짜인지 검사."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"날짜 형식이 올바르지 않습니다: '{value}' (예: 2026-10-03)")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"존재하지 않는 날짜입니다: '{value}'")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="travel_planner.py",
        description="날짜를 입력하면 LLM + 지도 API로 국내 여행 리포트를 만들어 줍니다.",
    )
    parser.add_argument("--date", required=True, type=valid_date, help='여행 날짜 "YYYY-MM-DD"')
    return parser.parse_args()


def load_keys() -> tuple[str, str, str]:
    """.env 를 읽고 키가 없으면 즉시 종료 + 설정 방법 안내."""
    load_dotenv(BASE_DIR / ".env")
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    kakao_key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL

    missing = [n for n, v in (("GEMINI_API_KEY", gemini_key), ("KAKAO_REST_API_KEY", kakao_key)) if not v]
    if missing:
        print(f"[오류] API 키가 설정되지 않았습니다: {', '.join(missing)}")
        print("  설정 방법:")
        print("   1) .env.example 파일을 복사해 .env 파일을 만듭니다.  (cp .env.example .env)")
        print("   2) .env 안에 발급받은 키를 입력합니다.")
        print("      - GEMINI_API_KEY     : https://aistudio.google.com/apikey")
        print("      - KAKAO_REST_API_KEY : https://developers.kakao.com (앱 > REST API 키, 카카오맵 사용 설정 ON)")
        print("   또는 환경변수로 설정: export GEMINI_API_KEY=\"YOUR_KEY\"  /  PowerShell: $env:GEMINI_API_KEY=\"YOUR_KEY\"")
        sys.exit(1)
    return gemini_key, kakao_key, model


# ─────────────────────────────────────────────
# LLM (Gemini) - POST
# ─────────────────────────────────────────────
def call_gemini(api_key: str, model: str, prompt: str, want_json: bool) -> str:
    """Gemini generateContent 호출. 성공 시 응답 텍스트를 돌려주고, 실패 시 ApiError."""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if want_json:
        body["generationConfig"] = {"responseMimeType": "application/json"}

    try:
        resp = requests.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=LLM_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        raise ApiError("NETWORK_ERROR", f"timeout after {LLM_TIMEOUT}s")
    except requests.exceptions.RequestException as e:
        raise ApiError("NETWORK_ERROR", type(e).__name__)

    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")[:200]
        except ValueError:
            pass
        err_type = classify_http_error(resp.status_code)
        # Gemini는 잘못된 키를 400(API_KEY_INVALID)으로 돌려주기도 한다
        if resp.status_code == 400 and "API key" in detail:
            err_type = "AUTH_ERROR"
        raise ApiError(err_type, f"HTTP {resp.status_code} {detail}".strip())

    try:
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    except (ValueError, KeyError, IndexError, TypeError):
        raise ApiError("LLM_EMPTY_RESPONSE", "응답에 텍스트가 없습니다 (안전 필터/차단 가능성)")
    if not text.strip():
        raise ApiError("LLM_EMPTY_RESPONSE", "빈 응답")
    return text


def extract_json(text: str) -> dict:
    """LLM 텍스트에서 JSON 객체만 뽑아 파싱. ```json 코드블록이 섞여도 처리."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSON 객체를 찾지 못했습니다")
    return json.loads(cleaned[start:end + 1])


def validate_first(data: dict) -> dict:
    """1차 JSON 스키마 검증 + 정규화. 문제가 있으면 ValueError."""
    if not isinstance(data, dict):
        raise ValueError("최상위가 객체가 아닙니다")
    for key in ("recommended_city", "weather", "reason"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"'{key}' 가 비었거나 문자열이 아닙니다")
    events = data.get("events")
    if not isinstance(events, list) or not events or not all(isinstance(e, str) for e in events):
        raise ValueError("'events' 는 문자열 1~3개 배열이어야 합니다")

    result = {
        "recommended_city": data["recommended_city"].strip(),
        "weather": data["weather"].strip(),
        "events": [e.strip() for e in events][:3],
        "reason": data["reason"].strip(),
    }
    return result


def build_first_prompt(date: str) -> str:
    return f"""너는 국내 여행 추천 도우미다.
날짜 {date} 에 국내(대한민국)로 여행을 가기 좋은 지역을 추천하라.

반드시 아래 규칙을 지켜라.
- 출력은 JSON 객체 하나만. 설명 문장, 마크다운, 코드블록(```) 금지.
- 키와 타입:
  - "recommended_city": string, 시/군 단위 지역명 하나 (예: "제주", "강릉", "경주")
  - "weather": string, 해당 시기의 일반적인 날씨 요약 1문장
  - "events": string 배열, 그 시기 해당 지역의 행사/축제 후보 1~3개
  - "reason": string, 추천 근거 2~4문장
- 모든 값은 한국어로 작성.

출력 예시:
{{"recommended_city": "제주", "weather": "평균 15°C 내외, 바람이 있으나 온화함", "events": ["유채꽃 관련 지역 행사"], "reason": "..."}}"""


def build_retry_prompt(date: str, bad_output: str) -> str:
    keys = "recommended_city, weather, events, reason"
    return (
        f"직전 출력이 유효한 JSON이 아니었거나 필수 키가 빠졌다.\n"
        f"날짜 {date} 기준 국내 여행 추천을, 필수 키({keys})만 포함한 JSON 객체 하나로 다시 출력하라.\n"
        f"다른 텍스트는 절대 쓰지 마라.\n\n직전 출력(참고용):\n{bad_output[:1000]}"
    )


def step1_recommend(api_key: str, model: str, date: str, errors: list) -> dict | None:
    """1차 추천. JSON 파싱/검증 실패 시 프롬프트를 바꿔 딱 1회만 재시도."""
    prompt = build_first_prompt(date)
    for attempt in (1, 2):  # 최초 1회 + 재시도 1회 (무한 재시도 금지)
        try:
            text = call_gemini(api_key, model, prompt, want_json=True)
        except ApiError as e:
            add_error(errors, "recommend", e.type, e.message)
            return None  # 인증/쿼터/네트워크 문제는 재시도해도 의미 없으므로 중단
        try:
            return validate_first(extract_json(text))
        except (ValueError, json.JSONDecodeError) as e:
            add_error(errors, "recommend", "PARSE_ERROR", f"attempt {attempt}: {e}")
            if attempt == 1:
                print("  - JSON 파싱 실패 → 필수 키만 다시 요청합니다 (재시도 1회)")
                prompt = build_retry_prompt(date, text)
    return None


# ─────────────────────────────────────────────
# 지도/장소 (Kakao Local) - GET
# ─────────────────────────────────────────────
def search_restaurants(api_key: str, city: str, errors: list) -> list[dict]:
    """Kakao 키워드 검색으로 맛집 검색. 실패/0건이어도 예외를 던지지 않고 [] 반환."""
    params = {
        "query": f"{city} 맛집",
        "category_group_code": "FD6",  # 음식점
        "size": PLACE_COUNT,
    }
    try:
        resp = requests.get(
            KAKAO_URL,
            headers={"Authorization": f"KakaoAK {api_key}"},
            params=params,
            timeout=KAKAO_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        add_error(errors, "place_search", "NETWORK_ERROR", f"timeout after {KAKAO_TIMEOUT}s (query={params['query']})")
        print(f"  - 오류: 네트워크 타임아웃. 맛집 섹션은 '데이터 없음'으로 처리합니다.")
        return []
    except requests.exceptions.RequestException as e:
        add_error(errors, "place_search", "NETWORK_ERROR", f"{type(e).__name__} (query={params['query']})")
        print(f"  - 오류: 네트워크 연결 실패. 맛집 섹션은 '데이터 없음'으로 처리합니다.")
        return []

    if resp.status_code != 200:
        err_type = classify_http_error(resp.status_code)
        detail = ""
        try:
            detail = resp.json().get("message", "")[:200]
        except ValueError:
            pass
        add_error(errors, "place_search", err_type, f"HTTP {resp.status_code} {detail}".strip())
        hint = {
            "AUTH_ERROR": "키 값 / 카카오맵 사용 설정(ON) / 앱 권한을 확인하세요",
            "QUOTA_ERROR": "일일 쿼터를 초과했습니다",
        }.get(err_type, "잠시 후 다시 시도하세요")
        print(f"  - 오류: {err_type}(HTTP {resp.status_code}). {hint}.")
        print("  - 맛집 섹션은 '데이터 없음'으로 처리하고 계속 진행합니다.")
        return []

    try:
        documents = resp.json().get("documents", [])
    except ValueError:
        add_error(errors, "place_search", "PARSE_ERROR", "Kakao 응답 JSON 파싱 실패")
        return []

    if not documents:
        add_error(errors, "place_search", "EMPTY_RESULT", f"0 results for query={params['query']}")
        print("  - 검색 결과 0건 (키워드를 바꿔 재시도하지 않고 다음 단계로 진행)")
        return []

    places = []
    for d in documents[:PLACE_COUNT]:
        places.append({
            "name": d.get("place_name", ""),
            "address": d.get("road_address_name") or d.get("address_name", ""),
            "category": d.get("category_name", ""),
            "url": d.get("place_url", ""),
            "x": float(d["x"]) if d.get("x") else None,   # 경도(lng)
            "y": float(d["y"]) if d.get("y") else None,   # 위도(lat)
        })
    return places


# ─────────────────────────────────────────────
# 리포트
# ─────────────────────────────────────────────
def build_report_prompt(date: str, first: dict, restaurants: list) -> str:
    return f"""너는 국내 여행 리포트 작성자다. 아래 데이터만 근거로 Markdown 리포트를 작성하라.

[여행 날짜]
{date}

[1차 추천 데이터(JSON)]
{json.dumps(first, ensure_ascii=False, indent=2)}

[맛집 검색 결과(JSON 배열, 빈 배열이면 데이터 없음)]
{json.dumps(restaurants, ensure_ascii=False, indent=2)}

작성 규칙:
- 아래 제목 구조를 그대로 사용하고, 순서를 바꾸지 마라.
  # {date} 국내 여행 추천 리포트
  ## 추천 지역
  ## 추천 이유
  ## 날씨 요약
  ## 행사/축제
  ## 맛집 추천
  ## 1일 일정 제안
- "맛집 추천": 각 맛집을 "- **이름** (카테고리) - 주소 - [카카오맵](url)" 형식으로 나열.
  맛집 목록이 빈 배열이면 "- 데이터 없음 (장소 검색 결과 0건 또는 검색 실패)" 한 줄만 쓴다.
  데이터에 없는 가게 이름을 지어내지 마라.
- "1일 일정 제안": 오전 / 오후 / 저녁 3구간으로 작성. 가능하면 위 맛집을 점심·저녁 일정에 활용.
- 오류 요약 섹션은 쓰지 마라 (프로그램이 자동으로 붙인다).
- 코드블록(```)으로 감싸지 말고 Markdown 본문만 출력."""


def render_errors_section(errors: list) -> str:
    lines = ["## 오류 요약(errors)"]
    if not errors:
        lines.append("- 없음")
    else:
        for e in errors:
            lines.append(f"- [{e['step']}] {e['type']}: {e['message']}")
    return "\n".join(lines)


def build_fallback_report(date: str, first: dict, restaurants: list) -> str:
    """리포트 생성 LLM 호출이 실패했을 때 쓰는 템플릿 리포트 (프로그램이 멈추지 않도록)."""
    lines = [f"# {date} 국내 여행 추천 리포트", "", "## 추천 지역", f"- {first['recommended_city']}", "",
             "## 추천 이유", first["reason"], "", "## 날씨 요약", first["weather"], "", "## 행사/축제"]
    lines += [f"- {e}" for e in first["events"]]
    lines += ["", "## 맛집 추천"]
    if not restaurants:
        lines.append("- 데이터 없음 (장소 검색 결과 0건 또는 검색 실패)")
    for p in restaurants:
        lines.append(f"- **{p['name']}** ({p['category']}) - {p['address']} - [카카오맵]({p['url']})")
    lines += ["", "## 1일 일정 제안",
              "- (LLM 리포트 생성 실패로 자동 일정 제안을 생략했습니다. 오류 요약을 확인하세요.)"]
    return "\n".join(lines)


def strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 혹시 LLM이 오류 섹션을 써버렸으면 제거 (프로그램이 정확한 값으로 다시 붙인다)
    text = re.split(r"\n##\s*오류 요약", text)[0]
    return text.strip()


def step3_report(api_key: str, model: str, date: str, first: dict, restaurants: list, errors: list) -> str:
    try:
        body = strip_code_fence(call_gemini(api_key, model, build_report_prompt(date, first, restaurants), want_json=False))
    except ApiError as e:
        add_error(errors, "report", e.type, e.message)
        print(f"  - 오류: {e.type}. 템플릿 리포트로 대체합니다.")
        body = build_fallback_report(date, first, restaurants)
    return body + "\n\n" + render_errors_section(errors) + "\n"


# ─────────────────────────────────────────────
# 저장
# ─────────────────────────────────────────────
def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    gemini_key, kakao_key, model = load_keys()

    RESULTS_DIR.mkdir(exist_ok=True)
    raw_path = RESULTS_DIR / f"{args.date}_raw.json"
    md_path = RESULTS_DIR / f"{args.date}_travel_plan.md"
    rel = lambda p: p.relative_to(BASE_DIR).as_posix()
    errors: list = []

    # [1/3] 1차 추천 (LLM)
    print("[1/3] 1차 추천 생성 중(LLM)...")
    first = step1_recommend(gemini_key, model, args.date, errors)
    if first is None:
        save_json(raw_path, {"date": args.date, "first_recommendation": None, "restaurants": [], "errors": errors})
        last = errors[-1]
        print(f"  - 오류: {last['type']} - {last['message']}")
        if last["type"] == "AUTH_ERROR":
            print("  - GEMINI_API_KEY 값을 확인하세요.")
        print(f"\n1차 추천에 실패해 중단합니다. 오류 내역: {rel(raw_path)}")
        sys.exit(1)
    print(f'  - recommended_city: "{first["recommended_city"]}"')

    # [2/3] 맛집 검색 (지도/장소 API) - 1차 JSON의 recommended_city 를 입력으로 사용
    print("[2/3] 맛집 검색 중(지도/장소 API)...")
    restaurants = search_restaurants(kakao_key, first["recommended_city"], errors)
    if restaurants:
        print(f"  - 맛집 {len(restaurants)}곳 검색 완료")

    # 3단계 전에 한 번 저장 (리포트 단계에서 죽어도 원본 데이터는 남도록)
    save_json(raw_path, {"date": args.date, "first_recommendation": first,
                         "restaurants": restaurants, "errors": errors})

    # [3/3] 최종 리포트 (LLM)
    print("[3/3] 최종 리포트 생성 중(LLM)...")
    report = step3_report(gemini_key, model, args.date, first, restaurants, errors)
    md_path.write_text(report, encoding="utf-8")
    save_json(raw_path, {"date": args.date, "first_recommendation": first,
                         "restaurants": restaurants, "errors": errors})
    print("  - 리포트 생성 완료")

    print(f"\n완료! {rel(md_path)} 를 확인하세요.")
    print(f"      원본 데이터: {rel(raw_path)}")


if __name__ == "__main__":
    main()
