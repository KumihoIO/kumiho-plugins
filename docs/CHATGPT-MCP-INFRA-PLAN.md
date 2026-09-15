# Kumiho Memory 공개 플러그인: 기존 서울 인프라 활용안

상태: 기존 태스크 사이드카 방식 사용자 선택 완료. MCP 2.2.0 구현·로컬 검증 완료, 운영 미배포.
조사일: 2026-09-15.

## 목적

PR #80의 hosted MCP 서버를 ChatGPT/Codex 공개 플러그인으로 준비한다.
서울에서 시작하고 이후 리전을 확장한다. OpenAI 사업자 인증은 사용자에
따르면 심사 중이다. 인증 대기 중에도 구현, 로컬 검증, 개발자 모드 연결
테스트, 제출 자료 작성은 진행할 수 있다.

## 실제 확인한 인프라

AWS 프로필은 `kumiho-prod`, 리전은 `ap-northeast-2`다.
아래는 읽기 전용 조회 결과이며, 자원 생성이나 운영 설정 변경은 하지 않았다.

| 항목 | 확인 결과 |
| --- | --- |
| Kumiho 서비스 | ECS/Fargate, 서비스 `kumiho-server-ap-northeast-2`, 태스크 1개 |
| 태스크 크기 | Linux x86_64, 1 vCPU, 2 GiB |
| 현재 컨테이너 | `kumiho-server`, `aws-otel-collector` |
| CPU 사용률 | 최근 24시간 평균 0.205%, 보고된 최고 0.356% |
| 메모리 사용률 | 최근 24시간 평균 1.465%, 보고된 최고 1.489% |
| 측정 구간 | 2026-09-14 14:18:12 UTC ~ 2026-09-15 14:18:12 UTC, 5분 간격 288개 |
| 기존 로드밸런서 | Network Load Balancer, TLS 443 → Kumiho 컨테이너 8080 |
| 4 GiB Neo4j EC2 | 조회 시점 MemAvailable 2,040 MiB |
| 32 GiB Neo4j EC2 | 조회 시점 MemAvailable 29,680 MiB |

ECS 수치는 현재 서비스의 관측치이며 MCP 추가 부하를 측정한 결과는 아니다.
EC2 메모리는 순간값이다. 두 EC2의 기본 CPU 지표 조회에는 데이터가 없었고,
CWAgent 메모리 지표도 없었다. SSM에서 `free`, `df`, Java RSS 조회는 성공했다.
마지막 `docker stats`는 Docker 미설치로 실패하여 전체 명령 상태는 Failed였다.
어떤 패키지도 설치하지 않았다.

## A. 비용 최소화: 기존 ECS 태스크에 MCP 컨테이너 추가

현재 1 vCPU / 2 GiB 할당 안에서 MCP를 실행할 수 있는지 먼저 검증한다.
태스크의 요청 자원을 늘리지 않으면 정규 실행 시간에 대한 추가 Fargate
컴퓨팅 요금은 없다. 롤링 배포 중의 일시적 중복 태스크 실행은 별도다.

제안 경로:

```text
ChatGPT / Codex
  → mcp.kumiho.cloud (기존 Cloudflare 계정의 Worker)
  → 기존 NLB의 별도 TLS 리스너 (8443 후보)
  → MCP 전용 target group
  → 기존 Kumiho ECS 태스크 안의 MCP 컨테이너 (8081 후보)
  → 기존 Kumiho 인증 / graph discovery / Redis proxy
```

- NLB는 HTTP 경로나 호스트 이름으로 `/mcp`를 분기하는 ALB가 아니다.
  기존 443 리스너를 교체하지 않고 별도 포트로 분리하는 안이다.
- Worker → origin의 DNS, 인증서 이름, SNI, 8443 연결, 스트리밍 전달은
  배포 전 검증해야 한다. 공개 MCP 주소 자체는 표준 HTTPS 443을 유지한다.
- 기존 Kumiho 서버와 MCP는 태스크의 네트워크, IAM task role, 배포 수명주기를
  공유한다. 컨테이너 분리만으로 권한과 장애가 완전히 격리되지는 않는다.
- MCP 메모리 제한과 CPU share를 명시하고, 재시작 및 health check 정책을
  함께 설계한다. `essential=false`만으로 모든 장애 영향을 차단했다고
  간주하지 않는다. 로드밸런서 health check 실패에 따른 태스크 교체도 검증한다.
- 현재 Kumiho 배포 workflow 및 task-definition renderer에 반영해야 한다.
  콘솔에서만 추가하면 다음 Kumiho 배포 때 사라질 수 있다.
- 기존 graph 권한 검증을 유지한다. Neo4j에 직접 연결하는 우회 경로는 만들지 않는다.

추가 비용 목표: 낮은 트래픽에서 월 약 **$0–10**.
이는 추가 컴퓨팅 $0을 전제로 한 로그, 이미지, secret, Worker, NLB 용량,
전송량의 계획용 여유분이다. 현재 Cloudflare 요금제와 실제 사용량에 따라 달라진다.

## B. 배포 분리: 작은 ECS 서비스 + 기존 NLB 재사용

MCP 전용 task role과 배포 수명주기가 필요하면 동일 ECS 클러스터 안에
별도 서비스를 둔다. 클러스터를 공유하더라도 별도 Fargate 태스크에는 요금이 든다.
기존 NLB의 별도 리스너와 target group은 A와 동일하게 재사용한다.

서울 Linux/x86 단가, 월 730시간 기준:

| 항목 | 월 금액 |
| --- | ---: |
| 0.25 vCPU + 1 GiB | $12.23 |
| 공인 IPv4 1개 | $3.65 |
| 2 GiB로 조정할 경우 컴퓨팅 합계 | $15.96 |

로그·secret·이미지·Worker·NLB 용량·전송 여유분을 포함한 계획 범위는
월 약 **$20–30**이다. 0.25 vCPU / 1 GiB가 충분하다는 부하 검증은 아직 없다.
2 GiB 또는 더 많은 CPU가 필요하면 실제 설정으로 다시 계산한다.

## C. Neo4j EC2에 MCP 프로세스 추가

기술적으로 가능하고 현재 메모리 순간값에는 여유가 있다. 하지만 DB의
heap/page cache, 배포, 재부팅, 장애, 교체/종료 수명주기를 공유한다.
특히 계정/수요에 따라 관리되는 DB 노드가 전체 사용자의 공개 MCP endpoint를
책임지는 구조는 수명주기를 먼저 확인해야 한다. 현재 두 호스트에는 Docker가 없다.

현재는 애플리케이션 계층인 ECS에 여유가 확인되므로 A 또는 B를 먼저 검증한다.
EC2의 MemAvailable 값만으로 DB와의 동시 실행 안전성을 확정하지 않는다.

## 사업자 인증 심사 중 진행할 작업

1. PR #80과 최신 SDK 조합을 고정하고 로컬 실행 및 메모리 사용량 측정.
2. ChatGPT용 도구 auth metadata, OAuth 콜백/CIMD/DCR 및 오류 처리 검증.
3. Claude로 고정된 context 처리와 동시 대화 session 분리 검증.
4. control-plane OAuth PR을 최신 운영 기준에 맞춰 통합. SQL/Firebase/비밀 설정은
   변경안을 먼저 준비하고 실제 환경 반영은 검토된 배포 단계에서 수행.
5. 개발자 모드에서 로그인 → 저장 → 검색 → 연결 해제 시나리오 검증.
6. 로고, 설명, 지원/개인정보/약관 URL, 심사용 계정, 정상 5개·부정 3개 테스트 작성.

사업자 인증 완료는 공개 제출 요건이다. 인증 완료 자체가 앱 심사 통과나
게시 완료를 뜻하지는 않는다.

## 배포 전 통과해야 하는 검증

- MCP 기동, tool 계약, tenant 분리, OAuth/철회, 동시 세션 테스트.
- 예상 동시 사용자 부하에서 MCP RSS 및 CPU, 기존 Kumiho 지연 측정.
- Kumiho 재배포 후 MCP 보존 및 MCP 장애/재시작 영향 확인.
- Worker/NLB TLS·스트리밍·health check·도메인 인증 경로 확인.
- 이미지 버전 고정과 이전 태스크/리스너로의 복구 절차 준비.

## 근거

- [PR #80](https://github.com/KumihoIO/kumiho-plugins/pull/80)
- [OpenAI 개발자 모드 연결](https://developers.openai.com/plugins/quickstart)
- [공개 제출 요건](https://developers.openai.com/plugins/deploy/submission)
- [ECS 태스크 설정](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html)
- [ECS task role](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html)
- [NLB 리스너](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-listeners.html)
- [서울 Fargate 가격표](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/ap-northeast-2/index.json)
- [공인 IPv4 요금](https://aws.amazon.com/vpc/pricing/)
- [Cloudflare Workers 요금](https://developers.cloudflare.com/workers/platform/pricing/)

모든 추가 비용 추정은 기존 graph/auth/Redis 용량 재사용, 낮은 트래픽,
새 ALB/NLB/NAT Gateway 미생성, 세금 제외 기준이다. 아직 예산 합의는 없다.


## 2026-09-15 구현 결과

사용자는 기존 서울 Kumiho ECS 태스크에 MCP 컨테이너를 추가하는 방식을 선택했다.
또한 MCP 1.x 버전 제한 대신 **2.x에서 작동하도록 전환**하라고 명시했다.

- MCP 2.2.0의 공개 핸들러·미들웨어 API로 호스팅 계층 전환.
- Windows 및 고정된 Linux 이미지에서 각각 **148 passed, 11 skipped**.
- 실제 2.x HTTP 클라이언트의 기존 연결·2026-07-28 프로토콜 모두 검증.
- Worker 타입 검사와 3개 테스트, ECS 배포·보존·복구 테스트 7개 통과.
- 512 MiB / 로컬 CPU 0.125 제한에서 컨테이너 기동, 도구 18개 확인.
- 가벼운 요청 100개(동시 8개) 처리 중 Docker 메모리 표본 89.27 MiB,
  cgroup 최고치 121.94 MiB. 실제 그래프 작업과 기존 서버 동시 부하는 미검증.
- 현재 revision 20 기반 태스크 미리보기·복구 스냅샷 생성.
- AWS CloudFormation 문법 검증만 실행. 운영 리소스 생성·태스크 변경 없음.

구현은 두 저장소의 `codex/chatgpt-mcp-sidecar` 브랜치에 분리되어 있다.
원래 작업 중이던 플러그인·서버 체크아웃은 유지했다.

자세한 결과: `cloud-mcp/CHATGPT.md`.
서버 배포 절차: kumiho-server `scripts/aws/MCP-SIDECAR.md`.

다음 운영 선행 조건은 통합된 control-plane OAuth의 DB/Firebase 설정 및 배포, ECR 이미지 게시,
오리진 DNS·인증서 확인, NLB 8443/8081 및 Cloudflare 연결이다.
공개 등록 전 동일 사용자의 동시 대화 버퍼 분리와 실제 OAuth 로그인도 검증해야 한다.


## 2026-09-16 기존 control OAuth 통합

사용자 요청에 따라 별도 OAuth 서비스를 만들지 않고 기존 `kumiho-control`에
추가하는 구조를 적용했다. 기존 Firebase, Supabase, 서명 키, 도메인을 재사용한다.
기존 control 리전은 유지하고 MCP 사이드카를 서울에서 시작한다.

- control 최신 main `c9d7639` + OAuth PR #3 통합: `codex/chatgpt-oauth`, `6c620f3`.
- ChatGPT CIMD 지원 방식 선택, 성공/실패 콜백 `iss`, resource 검증 및 기존 유료
  플랜 만료 정책 적용. 운영 배포의 수동 승인 게이트와 서버 빌드 비밀 분리 보존.
- control 256개, Worker 50개, Linux 배포 보호 14개 테스트 통과.
- 새로 생성한 OAuth 토큰을 MCP 2.2에서 검증하는 계약 테스트 25개 통과.
- 운영용 Linux 이미지 빌드·로컬 기동 및 임시 PostgreSQL migration/RLS 검사 통과.
- 별도 OAuth 컴퓨팅 리소스는 추가하지 않음. 기존 요청량/DB/자동 확장 비용은
  증가할 수 있다. 운영 migration·배포·원격 push/merge는 실행하지 않았다.

다음 단계는 control `docs/CHATGPT-OAUTH.md`에 정리했다. OpenAI 심사 대기 중에도
여기까지의 구현·검증은 완료할 수 있으며, 실제 Firebase/ChatGPT 연결 및 공개 등록은 남아 있다.
