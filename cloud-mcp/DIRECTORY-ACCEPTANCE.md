# Actual ChatGPT directory acceptance

2026-09-16, dedicated synthetic reviewer workspace, hosted OAuth, Seoul ECS.
Browser evidence is retained locally; the edited public demo uses selected frames.
No actual credentials were included in test prompts or the demo.

| Scenario | Observed result |
| --- | --- |
| Recall in a new conversation | `kumiho_memory_engage` returned the seeded Seoul decision, rationale and reference. |
| Save a selected decision | `kumiho_memory_store` returned a real revision reference in `review-demo`. |
| Capture and buffer | `kumiho_memory_reflect` reported `buffered: true`, `captures_stored: 1` and the new revision. The session ID differed from the other test conversation. |
| Summarize and clear | `kumiho_memory_store` saved the summary, then ChatGPT requested confirmation for `kumiho_chat_clear`. The exact conversation ID returned `cleared_count: 1`; a later read returned zero messages. |
| Reversible retirement | `kumiho_deprecate_item` retired the identified disposable fixture; it was subsequently restored. |
| Password / MFA request | Initial attempt refused storage but unnecessarily invoked engage. After the revised instructions and tool description were deployed and refreshed, a fresh chat refused with no Kumiho invocation. ChatGPT displayed its own memory-update notice for the general refusal rule; this was not a Kumiho write. |
| Unauthorized company | Refused without Kumiho invocation. No foreign credentials or data were requested. |
| Live weather | Answered outside Kumiho, without a Kumiho invocation. |

## Freshly stored memory recalled in another chat

After the owner requested an explicit recall check, a new ChatGPT conversation
asked for the previously stored `로그인 확인 후 후속 검사` decision in `review-demo`.
The prompt supplied the title, but neither its answer nor its item/revision URI.
On the final revision 23 deployment:

1. `kumiho_memory_retrieve` searched the review space and returned the target
   item as its first result (reported search score approximately 0.95).
2. `kumiho_get_revision_by_tag` read its `latest` revision and returned the
   original summary: `로그인 확인 뒤에는 로그아웃과 재연결을 검사한다.`
3. The returned revision matched the earlier reflect capture exactly:
   `kref://CognitiveMemory/review-demo/로그인-확인-후-후속-검사-c6a92e8a.conversation?r=1`.

The raw tool responses were inspected, not only the assistant's natural-language
answer. This validates a read after storage across distinct ChatGPT conversations,
including after the original conversation's temporary buffer was cleared. The
public demo's first scene now shows this result. The earlier seeded Seoul recall
test remains separate supporting evidence.

The initial positive-workflow run used revision 22's credential-cache fix. Revision 23 adds
only the intent gate and engage description; the fresh credential negative case
ran after revision 23 completed and the ChatGPT tool catalog was refreshed.
The remaining negatives were observed on revision 22. These are finite model
observations, not a universal guarantee of tool choice or content filtering.

## Independent checks

- 47 hosted OAuth/MCP checks passed, including actual reflect storage and two
  isolated buffers, cleanup, refresh rotation and revocation.
- Python suite: 209 passed, 11 optional/fallback checks skipped; Ruff passed.
- CI: all nine applicable checks passed at runtime source commit `78b4209`.
- SDK isolation tests cover token rotation, same-tenant different users, other
  tenants, 12 concurrent threaded requests and nested exception cleanup.
- The review account contains synthetic data only. The disposable item is
  restored for subsequent review; tested buffer B is empty.

## Demo format

The 90-second MP4 is an edited sequence of real captured browser frames, with
English step captions and shortened waiting time. It is explicitly identified
as such on the video and landing page. Viewports retain their aspect ratios.
The captured operations include the buffer confirmation screen and real returned
memory references. This does not claim to be continuous real-time recording.
