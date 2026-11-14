import json

from openai import OpenAI, OpenAIError
import streamlit as st
import requests


KAKAO_API_URL = "https://dapi.kakao.com/v2/local"


def search_kakao_places(
    location: str, api_key: str, radius: int, sort: str
) -> list[dict[str, str]]:
    headers = {"Authorization": f"KakaoAK {api_key}"}

    keyword_response = requests.get(
        f"{KAKAO_API_URL}/search/keyword.json",
        headers=headers,
        params={"query": location},
        timeout=10,
    )
    keyword_response.raise_for_status()
    keyword_documents = keyword_response.json().get("documents", [])
    if not keyword_documents:
        return []

    place = keyword_documents[0]
    category_response = requests.get(
        f"{KAKAO_API_URL}/search/category.json",
        headers=headers,
        params={
            "category_group_code": "FD6",
            "x": place["x"],
            "y": place["y"],
            "radius": radius,
            "sort": sort,
        },
        timeout=10,
    )
    category_response.raise_for_status()
    return category_response.json().get("documents", [])


def get_food_types(restaurants: list[dict[str, str]]) -> list[str]:
    food_types = set()
    for restaurant in restaurants:
        categories = restaurant.get("category_name", "").split(" > ")
        if len(categories) > 1:
            food_types.add(categories[1])
    return ["전체", *sorted(food_types)]


def get_supabase_config() -> tuple[str, dict[str, str]]:
    url = st.secrets["SUPABASE_URL"].rstrip("/")
    key = st.secrets["SUPABASE_KEY"]
    return f"{url}/rest/v1/favorites", {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def load_favorites() -> list[dict[str, str]]:
    url, headers = get_supabase_config()
    response = requests.get(
        url,
        headers=headers,
        params={
            "select": "id,place_id,place_url,nickname,memo,created_at",
            "order": "created_at.desc",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_ai_recommendations(
    restaurants: list[dict[str, str]], situation: str, api_key: str
) -> list[dict[str, str]]:
    restaurant_data = [
        {
            "id": restaurant.get("id", ""),
            "name": restaurant.get("place_name", ""),
            "category": restaurant.get("category_name", ""),
            "distance": restaurant.get("distance", ""),
        }
        for restaurant in restaurants
    ]
    system_prompt = """당신은 대학생의 점심 고민을 덜어주는 친절한 맛집 안내자입니다.
형식 규칙:
- 추천은 3곳
- 이유는 한 줄
- 존댓말
제약 규칙:
- 전달한 목록 안에서만 고르세요.
- 메뉴, 가격, 맛, 분위기는 추측하지 마세요.
출력은 {"recommendations": [{"id": "...", "reason": "..."}]} 형태의 JSON으로만 답하세요."""
    user_prompt = json.dumps(
        {"today_situation": situation, "restaurants": restaurant_data},
        ensure_ascii=False,
    )
    client = OpenAI(api_key=api_key, timeout=30.0)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    parsed = json.loads(content)
    recommendations = parsed["recommendations"]
    if not isinstance(recommendations, list):
        raise ValueError("recommendations must be a list")
    return [
        {"id": str(recommendation["id"]), "reason": str(recommendation["reason"])}
        for recommendation in recommendations[:3]
    ]


st.set_page_config(page_title="AI 맛집 추천 서비스")
st.title("AI 맛집 추천 서비스")

if "restaurants" not in st.session_state:
    st.session_state.restaurants = []
if "recommendations" not in st.session_state:
    st.session_state.recommendations = []
if "editing_favorite_id" not in st.session_state:
    st.session_state.editing_favorite_id = None

food_types = get_food_types(st.session_state.restaurants)
selected_food_type = st.sidebar.selectbox("음식 종류", food_types)
radius = st.sidebar.slider("반경", min_value=100, max_value=2000, value=500, step=100)
sort_label = st.sidebar.selectbox("정렬", ["거리순", "정확도순"])
sort = "distance" if sort_label == "거리순" else "accuracy"

with st.form("location_search_form"):
    location = st.text_input("위치", placeholder="예: 강남역")
    submitted = st.form_submit_button("검색")

if submitted:
    location = location.strip()
    if location:
        st.write(f"{location} 주변을 검색합니다")
        try:
            api_key = st.secrets["KAKAO_REST_API_KEY"]
        except (FileNotFoundError, KeyError):
            st.error("카카오 API 키가 설정되지 않았습니다.")
        else:
            st.session_state.restaurants = []
            st.session_state.recommendations = []
            with st.spinner("주변 음식점을 검색하는 중입니다..."):
                try:
                    restaurants = search_kakao_places(location, api_key, radius, sort)
                except requests.RequestException:
                    st.error("카카오 API 요청 중 오류가 발생했습니다.")
                except (KeyError, ValueError):
                    st.error("카카오 API 응답을 처리하는 중 오류가 발생했습니다.")
                else:
                    st.session_state.restaurants = restaurants
                    if not st.session_state.restaurants:
                        st.error("검색된 장소 또는 주변 음식점이 없습니다.")
    else:
        st.warning("위치를 입력해주세요")

filtered_restaurants = [
    restaurant
    for restaurant in st.session_state.restaurants
    if selected_food_type == "전체"
    or selected_food_type in restaurant.get("category_name", "").split(" > ")
]

with st.form("recommendation_form"):
    situation = st.text_input("오늘 상황", placeholder="예: 비 오는 날, 혼자, 빨리")
    recommendation_submitted = st.form_submit_button("AI 추천")

if recommendation_submitted:
    if not filtered_restaurants:
        st.info("먼저 검색해주세요")
    else:
        try:
            openai_api_key = st.secrets["OPENAI_API_KEY"]
        except (FileNotFoundError, KeyError):
            st.error("OpenAI API 키가 설정되지 않았습니다.")
        else:
            with st.spinner("AI가 맛집을 추천하는 중입니다..."):
                try:
                    st.session_state.recommendations = get_ai_recommendations(
                        filtered_restaurants, situation, openai_api_key
                    )
                except requests.RequestException:
                    st.error("OpenAI API 요청 중 오류가 발생했습니다.")
                except OpenAIError:
                    st.error("OpenAI API 요청 중 오류가 발생했습니다.")
                except (KeyError, TypeError, ValueError, IndexError):
                    st.error("AI 응답을 처리하는 중 오류가 발생했습니다.")

restaurants_by_id = {
    str(restaurant.get("id", "")): restaurant
    for restaurant in filtered_restaurants
}
recommendation_cards = [
    (recommendation, restaurants_by_id[recommendation["id"]])
    for recommendation in st.session_state.recommendations
    if recommendation.get("id") in restaurants_by_id
]

search_tab, favorites_tab = st.tabs(["맛집 검색", "즐겨찾기"])

with search_tab:
    if recommendation_cards:
        st.subheader("AI 추천 맛집")
        columns = st.columns(len(recommendation_cards))
        for column, (recommendation, restaurant) in zip(columns, recommendation_cards):
            with column:
                with st.container(border=True):
                    st.markdown(f"**{restaurant.get('place_name', '이름 없음')}**")
                    st.write(f"거리: {restaurant.get('distance', '거리 정보 없음')}m")
                    st.write(f"추천 이유: {recommendation['reason']}")
                    st.markdown(
                        f"[카카오맵에서 보기]({restaurant.get('place_url', '#')})"
                    )

    if st.session_state.restaurants and not filtered_restaurants:
        st.info("조건에 맞는 식당이 없어요")
    elif filtered_restaurants:
        st.subheader("주변 음식점")
        for index, restaurant in enumerate(filtered_restaurants):
            name = restaurant.get("place_name", "이름 없음")
            category = restaurant.get("category_name", "분류 없음")
            distance = restaurant.get("distance", "거리 정보 없음")
            place_id = str(restaurant.get("id", ""))
            place_url = restaurant.get("place_url", "#")
            st.markdown(f"### {name}")
            st.write(f"{category} | {distance}m")
            st.markdown(f"[카카오맵에서 보기]({place_url})")
            with st.form(f"favorite_form_{place_id}_{index}"):
                nickname = st.text_input("별칭", key=f"nickname_{place_id}_{index}")
                memo = st.text_input("메모 (선택)", key=f"memo_{place_id}_{index}")
                save_submitted = st.form_submit_button("저장")
            if save_submitted:
                if not nickname.strip():
                    st.warning("별칭을 입력해주세요")
                else:
                    try:
                        url, headers = get_supabase_config()
                        response = requests.post(
                            url,
                            headers={**headers, "Prefer": "return=minimal"},
                            json={
                                "place_id": place_id,
                                "place_url": place_url,
                                "nickname": nickname.strip(),
                                "memo": memo.strip(),
                            },
                            timeout=10,
                        )
                        response.raise_for_status()
                    except (FileNotFoundError, KeyError):
                        st.error("Supabase 설정이 없습니다. secrets.toml을 확인해주세요.")
                    except requests.RequestException:
                        st.error("즐겨찾기 저장 중 오류가 발생했습니다.")
                    except Exception:
                        st.error("즐겨찾기 저장 중 오류가 발생했습니다.")
                    else:
                        st.success("즐겨찾기에 저장했습니다.")

with favorites_tab:
    try:
        favorites = load_favorites()
    except (FileNotFoundError, KeyError):
        st.error("Supabase 설정이 없습니다. secrets.toml을 확인해주세요.")
    except requests.RequestException:
        st.error("즐겨찾기를 불러오는 중 오류가 발생했습니다.")
    except Exception:
        st.error("즐겨찾기를 불러오는 중 오류가 발생했습니다.")
    else:
        if not favorites:
            st.info("저장된 즐겨찾기가 없습니다.")
        for favorite in favorites:
            favorite_id = favorite.get("id")
            st.markdown(f"### {favorite.get('nickname', '')}")
            memo = favorite.get("memo", "")
            if memo:
                st.write(f"메모: {memo}")
            st.markdown(f"[카카오맵에서 보기]({favorite.get('place_url', '#')})")
            st.caption(f"저장 시각: {favorite.get('created_at', '정보 없음')}")
            if st.button("메모 수정", key=f"edit_favorite_{favorite_id}"):
                st.session_state.editing_favorite_id = favorite_id
                st.rerun()
            if st.session_state.editing_favorite_id == favorite_id:
                with st.form(f"edit_memo_form_{favorite_id}"):
                    updated_memo = st.text_input(
                        "메모",
                        value=memo,
                        key=f"updated_memo_{favorite_id}",
                    )
                    memo_save_submitted = st.form_submit_button("메모 저장")
                if memo_save_submitted:
                    try:
                        url, headers = get_supabase_config()
                        response = requests.patch(
                            url,
                            headers={**headers, "Prefer": "return=minimal"},
                            params={"id": f"eq.{favorite_id}"},
                            json={"memo": updated_memo},
                            timeout=10,
                        )
                        response.raise_for_status()
                    except (FileNotFoundError, KeyError):
                        st.error("Supabase 설정이 없습니다. secrets.toml을 확인해주세요.")
                    except requests.RequestException:
                        st.error("메모 수정 중 오류가 발생했습니다.")
                    except Exception:
                        st.error("메모 수정 중 오류가 발생했습니다.")
                    else:
                        st.session_state.editing_favorite_id = None
                        st.success("메모를 수정했습니다.")
                        st.rerun()
            if st.button("삭제", key=f"delete_favorite_{favorite_id}"):
                try:
                    url, headers = get_supabase_config()
                    response = requests.delete(
                        url,
                        headers=headers,
                        params={"id": f"eq.{favorite_id}"},
                        timeout=10,
                    )
                    response.raise_for_status()
                except (FileNotFoundError, KeyError):
                    st.error("Supabase 설정이 없습니다. secrets.toml을 확인해주세요.")
                except requests.RequestException:
                    st.error("즐겨찾기 삭제 중 오류가 발생했습니다.")
                except Exception:
                    st.error("즐겨찾기 삭제 중 오류가 발생했습니다.")
                else:
                    st.success("즐겨찾기를 삭제했습니다.")
                    st.rerun()
