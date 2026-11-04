# API 명세 요약

담당: 민재 / 갱신 2026-10-26. 자세한 건 Swagger 문서.
공통: 모든 요청은 Authorization 헤더의 액세스 토큰이 필요하다 (/auth/google 제외).
오류 응답은 code, message, detail 세 필드로 통일한다.

## 인증
- API-1 POST /auth/google 구글 ID 토큰으로 로그인, 학교 도메인이 아니면 403
- API-2 POST /auth/refresh 리프레시 토큰으로 재발급

## 매물
- API-3 GET /listings 매물 목록, page와 size, 거래완료 제외
- API-4 POST /listings 매물 등록, 사진은 최대 5개 파일
- API-5 PATCH /listings/{id}/status 판매중, 예약중, 거래완료
- API-6 GET /listings/search?q= 제목과 과목명 검색

## 쪽지
- API-7 GET /threads 내 쪽지방 목록과 안 읽은 개수
- API-8 GET /threads/{id}/messages?after= 이후의 새 쪽지만 (앱이 10초마다 호출)
- API-9 POST /threads/{id}/messages 쪽지 보내기
- API-10 POST /threads/{id}/read 읽음 처리

## 거래와 알림
- API-11 GET /listings/{id}/account 판매자 계좌, 쪽지 이력이 있는 구매자만
- API-12 POST /devices FCM 기기 토큰 등록
