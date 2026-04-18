# ai-creator-auto

Investment OS 기반 자동화 크리에이터 운영 레포입니다.

## 현재 구조

- `src/data_fetcher.py`
  - yfinance/FRED 기반 원천 데이터 수집
  - 20일 수익률 및 0~100 스코어 신호 계산
- `src/claude_caller.py`
  - Investment OS 규칙 프롬프트 주입
  - Claude 응답 JSON 추출/파싱
- `src/x_publisher.py`
  - X(Twitter) 발행 텍스트 조합 및 포스팅
- `src/main.py`
  - 전체 파이프라인 오케스트레이션
  - 실행 로그/이력 저장
- `src/ops_enhancer.py`
  - Core Data 품질 점검(QA)
  - 운영자 브리프(.md) + 번들(.json) 자동 저장

## 실행

```bash
python src/main.py --session postmarket --dry-run
```

옵션:
- `--session`: `premarket | postmarket | intraday | crisis`
- `--dry-run`: X 미포스팅, 분석만 실행
- `--override key=value`: 신호 수동 오버라이드

## 산출물

- 로그: `logs/run_YYYYMMDD.log`
- 발행 이력: `data/post_history/*.json`
- 운영 번들:
  - `data/exports/*_SESSION.bundle.json`
  - `data/exports/*_SESSION.brief.md`

## 이번 고도화 포인트

1. **운영 안정성 강화**
   - Claude 응답 필수 필드(`market_regime`, `trading_signal.signal`) 검증
   - Critical 실패 시 X 포스팅 자동 차단
2. **콘텐츠 재활용성 강화**
   - 실행 결과를 JSON 번들로 저장해 Notion/Telegram/서브 파이프라인에서 재사용 가능
3. **운영 가시성 강화**
   - 사람이 바로 읽을 수 있는 운영 브리프 마크다운 생성

## 다음 Phase 제안 (운영 중인 투자/코믹 자동화 확장)

1. **Router Layer 추가**
   - 채널별 템플릿을 분리(`x`, `telegram`, `notion`, `comic-script`)
   - 동일 core_data에서 멀티 채널 동시 생성
2. **Editorial Calendar 자동화**
   - 주 2회 코믹(Investment Comic Gemini)와 일일 요약 간 충돌 없는 캘린더 자동 생성
3. **이상탐지/회고 루프**
   - 성과 지표(CTR, 저장수, 공유수)를 회수해 다음 프롬프트 가중치에 반영
4. **Failover LLM 전략**
   - 1차 모델 실패 시 백업 모델 자동 전환
5. **Notion 양방향 동기화**
   - 설계 문서(노션 DB)의 상태/우선순위를 코드 파이프라인에 반영하는 동기화 스크립트 추가
