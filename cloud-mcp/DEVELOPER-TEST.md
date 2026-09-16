# Kumiho Memory 개발자 테스트

2026-09-16 기준. 디렉터리 심사·공개 게시 전에 직접 연결할 수 있는 OAuth 보호 MCP입니다.

## 연결 정보

| 항목 | 값 |
| --- | --- |
| 이름 | Kumiho Memory — Developer Test |
| 설명 | Kumiho 워크스페이스의 기억 저장·검색 및 대화별 임시 버퍼 테스트 |
| MCP URL | `https://mcp.kumiho.cloud/mcp` |
| 전송 | Streamable HTTP / MCP 2.2.0 |
| 인증 | OAuth; 기존 Kumiho 계정으로 로그인하고 워크스페이스 선택 |
| 권한 | `memory offline_access` |
| OAuth 발급자 | `https://control.kumiho.cloud` |
| 도구 수 | 18개 |
| 아이콘 | `submission-assets/kumiho-fox.png` |

별도 OpenAI API 키나 채팅에 붙여 넣을 Kumiho 토큰은 필요하지 않습니다.
OAuth 클라이언트는 공개 클라이언트이며 서버가 CIMD와 동적 등록을 지원합니다.
수동 client secret을 만들지 마세요. 실제 연결 UI에서 인증 선택이 나오면 OAuth를 사용합니다.

## ChatGPT에서 연결

1. **Settings → Security and login → Developer mode**를 켭니다.
2. [**Plugins**](https://chatgpt.com/plugins)에서 **+**로 연결을 추가합니다. 위 이름·설명과 `/mcp`까지 포함한 URL을 입력합니다.
3. Kumiho 로그인 화면에서 본인 계정으로 로그인하고 테스트할 워크스페이스를 선택·승인합니다.
4. 연결된 도구가 18개인지 확인합니다. 새 대화의 도구 메뉴에서 이 연결을 선택합니다.
5. 아래 순서로 시험합니다. 도구·설명 변경 후에는 연결의 **Refresh**를 실행하고 새 대화를 시작합니다.

메뉴명과 개발자 모드 제공 여부는 계정·워크스페이스 정책에 따라 달라질 수 있습니다.
이 절차는 [공식 개발자 연결 안내](https://developers.openai.com/plugins/deploy/connect-chatgpt)에 근거합니다.
사업자 인증 완료와 디렉터리 앱 승인 여부는 개발자 연결 시험 결과와 별개입니다.

## 먼저 실행할 대화 시나리오

고유한 테스트 공간을 사용하고 기존 기억을 수정하지 않도록 프롬프트에 범위를 명시합니다.

1. **조회:** “Kumiho에서 접근 가능한 프로젝트를 보여줘.”
2. **저장:** “CognitiveMemory의 `developer-tests/manual-20260916` 공간에 테스트 결정 하나를 새 기억으로 저장해줘. 제목은 ‘서울 파일럿 개발자 테스트’, 내용은 ‘초기 한국 사용자의 응답 지연을 줄이기 위해 서울 리전을 선택했다’. 기존 기억에 쌓지 말고 새로 만들어줘.”
3. **재조회:** “방금 만든 테스트 기억의 내용과 참조를 확인하고, 서울을 선택한 이유를 알려줘.”
4. **대화 A 버퍼:** “현재 대화의 Kumiho 임시 버퍼에 ‘개발자 테스트 A의 표식은 청록색’이라는 짧은 응답을 기록해줘. 장기 기억 캡처는 하지 마.”
5. **새 대화 B:** 같은 연결을 선택하고 “현재 대화의 임시 버퍼만 확인해줘.” A 표식이 나오면 실패입니다. 이어서 B 표식을 별도로 기록합니다.
6. **A로 돌아와 정리:** “이 대화의 Kumiho 임시 버퍼만 비워줘.” 이후 B의 표식이 남아 있어야 합니다.
7. **퇴역·복원:** 2번에서 실제 반환된 참조의 테스트 기억만 퇴역시켰다가 복원합니다. 퇴역은 영구 삭제가 아닙니다.
8. **비호출:** 날씨 조회나 비밀번호 저장 요청에는 Kumiho 도구를 호출하지 않아야 합니다.

버퍼 도구의 첫 호출이 `session_required`와 ID를 반환하면 모델은 그 ID로 다시 호출하고 해당 대화 안에서만 재사용해야 합니다. 대화 B에 A의 ID를 복사하지 않습니다.
연결은 선택한 워크스페이스 권한을 가집니다. 테스트 공간은 정리용 분리이며 추가 접근 제어 경계는 아닙니다.

## 터미널에서 반복 검사

`cloud-mcp`의 기존 가상환경을 사용합니다. 새 체크아웃에서는 Python 3.11 이상으로 가상환경을 만들고 `requirements.lock` 및 패키지를 설치하세요.

```powershell
# 저장소 루트(.worktrees/plugins)에서 실행
cloud-mcp/.venv/Scripts/python.exe cloud-mcp/scripts/developer_test.py --output cloud-mcp/.local/devtest-public.json

# 브라우저 OAuth + 인증된 도구 목록과 프로젝트 조회
cloud-mcp/.venv/Scripts/python.exe cloud-mcp/scripts/developer_test.py --login --output cloud-mcp/.local/devtest-read.json

# 실제 쓰기까지: 새 샘플 기억 1개와 서로 다른 임시 버퍼 2개
cloud-mcp/.venv/Scripts/python.exe cloud-mcp/scripts/developer_test.py --login --writes --output cloud-mcp/.local/devtest-live.json
```

`--writes`는 실행마다 고유한 `CognitiveMemory/developer-tests/<실행 ID>` 공간을 사용합니다.
스태킹을 끄고 새 샘플 기억을 생성·조회·퇴역·복원하며, 두 새 대화 버퍼의 분리와 한쪽 버퍼 삭제를 검사합니다.
샘플 기억은 직접 ChatGPT에서 다시 찾을 수 있도록 남기고, 버퍼는 검사 후 정리합니다.
그 참조와 검사 결과만 지정한 로컬 보고서에 기록합니다. 실제 요약·통합과 모델의 도구 선택·확인 UI는 위 대화 시험 및 제출 시나리오로 별도 확인합니다.

로그인은 로컬 루프백 콜백과 PKCE S256을 사용하며, state와 issuer가 일치해야 코드를 교환합니다.
토큰·비밀번호·개인 프로젝트 목록은 파일이나 로그에 저장하지 않습니다.
검사 종료 시 테스트용 refresh token을 폐기합니다. 이미 발급된 access token은 원래 만료 시점까지 유효할 수 있습니다.
로그인 URL은 10분 안에 사용하세요. 만료되면 명령을 새로 실행합니다.

## 배포 및 확인 상태

- ECS: 기존 서울 서비스, 태스크 revision **21**, **1 vCPU / 2 GiB** 유지.
- MCP: PR #102의 검사 완료 커밋 `a1aca0e` 기반 개발 테스트 후보.
- 이미지 digest: `sha256:23dbdb0a2e0f9f91532e256f88a79ccedce7eae843cf6fa7753e4c471a4cd393`.
- Worker: `2439a8b1-6cf0-44f6-bcfa-b2e7128f11d5`, `mcp.kumiho.cloud`.
- 기존 Kumiho 대상 8080과 새 MCP 대상 8081의 정상 상태 확인.
- 공개 HTTPS health, OAuth resource/issuer/PKCE 발견, 무인증 401 검사 통과.
- 실제 브라우저 OAuth 및 인증된 읽기·쓰기 검사: **63개 확인 항목 모두 통과**. 코드 교환, refresh 회전, MCP 초기화, 18개 도구와 힌트, 저장·조회·퇴역·복원, 두 대화 버퍼 분리, 한쪽만 삭제, refresh 폐기와 재사용 거부를 확인했습니다.
- 위 63개 실제 검사는 `953bdb1`의 러너로 실행했습니다. 적대 리뷰에서 검색 결과의 정확한 참조, 갱신 응답의 필수 값, 폐기의 `invalid_grant`, 실패 시 나머지 버퍼 정리를 더 엄격히 검사하도록 보강했습니다. 보강된 러너의 실제 OAuth 재실행은 아직 하지 않았습니다.
- 자동 회귀 검사: **202 passed / 11 skipped**, Ruff 통과. 건너뛴 선택적 CE/Redis 검사를 실행한 것으로 간주하지 않습니다.
- ChatGPT 연결·사용: 사용자가 정상 작동을 확인했습니다. 제출용 8개 시나리오 전체와 확인 UI·새 대화 분리 스크린샷은 별도 검증이 필요합니다.
- 디렉터리 제출·공개 게시: 미완료. 개발자 시험 연결과 별도 절차.
- 이미지 검사: Critical 0 / High 1. 기존 상위 zlib `CVE-2026-85091`은 아직 미해결이며 공개 출시 전 재평가 대상입니다.

원상복구 자료는 서버 worktree의 `scripts/aws/.local/devtest-20260916/rollback-service.json`에 있습니다.
여기에는 이전 revision 20과 기존 대상 매핑이 함께 기록되어 있습니다.
