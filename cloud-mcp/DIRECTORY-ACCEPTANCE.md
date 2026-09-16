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

Positive workflows ran on revision 22's credential-cache fix. Revision 23 adds
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
