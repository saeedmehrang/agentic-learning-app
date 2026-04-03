# On-Device LLM: Planning Appendix
## Agentic Self-Paced Learning System — Architecture Addendum
*April 2026 — evaluated against MVP spec v1.0*

---

## 1. Why This Is Worth Planning Now (But Not Building Yet)

The current architecture (4-agent Gemini pipeline on Cloud Run) is the right MVP choice. On-device LLM orchestration is a **Phase 7+ feature** — a cost-saving, privacy-enhancing, opt-in layer that sits alongside the cloud path. It does not replace it.

The reason to plan it now:
- Architectural decisions made in the Flutter app (inference abstraction, session interface design) can either make this easy or painful to add later.
- The hardware is maturing faster than the tooling. By the time the trial launch has run and you have real usage data, the on-device ecosystem will be significantly more stable.
- A few cheap decisions now (an abstraction layer, a device capability check) prevent an expensive rewrite later.

**Rule:** Cloud-first, device-optional. Every session starts by checking user preference and device capability. The cloud pipeline is always the fallback.

---

## 2. What "On-Device" Means in This System

Not all agents are equal candidates for on-device execution. The system is already well-partitioned.

| Agent / Task | On-Device Feasibility | Rationale |
|---|---|---|
| **ContextAgent** — read Firestore, determine next concept | ❌ Keep cloud | Requires live Firestore read. No LLM value on-device. |
| **LessonAgent — teaching phase** | ⚠️ Possible, not recommended | Teaching narrative generation needs good quality output. 2B models risk degraded explanation quality. |
| **LessonAgent — quiz evaluation** | ✅ Best first target | Binary correct/incorrect + short explanation. Well within 1–2B model capability. Low stakes if quality dips slightly. |
| **HelpAgent (3-turn dialogue)** | ✅ Good candidate | Short conversational turns, constrained scope, capped at 3 exchanges. Low latency demand. |
| **SummaryAgent + FSRS** | ❌ Keep cloud | FSRS is deterministic Python. Summary writes to Firestore. No LLM inference needed on-device. |
| **`search_knowledge_base` tool** | ❌ Keep cloud (MVP) | Requires pgvector in Cloud SQL. A local SQLite vector store is a later option (Phase 8+). |

**Recommended first on-device scope:** Quiz evaluation and HelpAgent dialogue. These are the two highest-frequency, lowest-quality-risk tasks. Starting here gives you real performance and cost data before committing to a fuller on-device pipeline.

---

## 3. The Flutter Decision: What to Protect

Flutter remains the right frontend choice. The on-device feature does not change that. What matters is how you structure the session layer in Dart so the inference backend is swappable.

### 3.1 Design the Session Interface as an Abstraction

Define a Dart abstract class (or sealed class) for the inference provider early. Do not scatter direct `http` calls to Cloud Run across your session logic:

```dart
abstract class InferenceProvider {
  Future<QuizEvalResult> evaluateAnswer({
    required String lessonContext,
    required String question,
    required String learnerAnswer,
  });

  Future<HelpTurn> generateHelpTurn({
    required String concept,
    required int turnNumber,
    required String learnerMessage,
  });
}

class CloudInferenceProvider implements InferenceProvider { /* ADK on Cloud Run */ }
class OnDeviceInferenceProvider implements InferenceProvider { /* local LLM */ }
```

This is a small Dart design decision that costs almost nothing now and prevents a painful retrofit later. The `ProviderMode` can be a Riverpod state driven by a settings toggle + device capability check.

### 3.2 Device Capability Check at Session Start

On-device mode should only activate if the device can handle it. Run this check once at app init and cache the result in Riverpod:

```dart
class DeviceCapability {
  final bool hasNPU;
  final int ramMb;
  final bool isEligibleForOnDevice; // ramMb >= 6000 && hasNPU
}
```

Use `device_info_plus` for platform details. NPU detection is indirect — infer from chipset generation (Snapdragon 8 Gen 2+, A16+, Tensor G3+). Flag anything older as cloud-only.

### 3.3 Model Download UX

On-device models are large (1–4 GB). You must:
- Never bundle the model in the APK/IPA — ship it as an on-demand download.
- Show a clear download prompt with file size before initiating.
- Use Firebase Storage or a signed GCS URL as the distribution endpoint.
- Cache the model in the app's documents directory, not the cache directory (avoid OS eviction).
- Verify the model hash after download before first use.

---

## 4. The Tooling Landscape (as of April 2026)

### 4.1 Google LiteRT-LM + `flutter_gemma` *(Recommended starting point)*

**What it is:** Google's official on-device LLM runtime, replacing the MediaPipe LLM Inference API. Flutter support via the `flutter_gemma` pub.dev package.

**Strengths:**
- GCP-aligned: Gemma models, same Google ecosystem as the rest of your stack.
- NPU acceleration on Android (Hexagon) and Apple Neural Engine.
- Supports Gemma 3n E2B (~2B effective params) — well within modern phone capability.
- `flutter_gemma` handles model management, format detection, and cross-platform differences.

**Watch-outs:**
- **Tool calling is not production-stable.** As of late 2025, the Kotlin JNI wrapper for LiteRT-LM exposes only a subset of the C++ API. Structured output (JSON mode) is limited. For quiz evaluation, you will need to use prompt engineering to get structured responses rather than native tool calling.
- **Model format fragmentation.** `.task` files work on Android/iOS (MediaPipe-backed), `.litertlm` files work on Android NPU and desktop. These are different artifacts — manage them explicitly.
- **Desktop is different.** The desktop inference path uses a JVM gRPC server bundled alongside the Flutter app. More setup, different performance characteristics than mobile NPU paths.
- The MediaPipe LLM Inference API is now deprecated — do not build against it. Go directly to LiteRT-LM.

**Recommended model:** Gemma 3n E2B (effective 2B, optimised for mobile). Gemma 4 on-device is newly available but evaluate stability before committing.

### 4.2 `llama_cpp_dart` *(Mature fallback)*

**What it is:** Dart bindings for llama.cpp, the most widely used open-source on-device LLM runtime. GGUF model format, supports Gemma, Llama, Phi, Mistral, Qwen, and thousands of community fine-tunes.

**Strengths:**
- Most battle-tested on-device runtime available.
- Model-agnostic — not locked to Gemma.
- Quantization levels from 2-bit to FP32 give fine-grained size/quality control.
- Active community, good benchmark coverage across device types.

**Watch-outs:**
- Integration via Flutter method channels (C++ FFI bridge) — more plumbing than a native Flutter SDK.
- NPU acceleration weaker than Google's native stack on Android. Primarily GPU-backed.
- No built-in model management (download, caching, versioning) — you build that yourself.
- Larger binary size overhead.

**When to prefer llama.cpp over LiteRT-LM:** If you want model flexibility (non-Gemma models), or if LiteRT-LM's tool calling limitations prove blocking for your use case.

### 4.3 Cactus *(Watch closely)*

**What it is:** Y Combinator-backed cross-platform SDK with native Flutter bindings, sub-50ms TTFT, and built-in RAG fine-tuning in the Flutter SDK.

**Strengths:**
- Most Flutter-native of the three options — less FFI friction.
- Built-in RAG support aligns with your `search_knowledge_base` use case.
- Benchmarks: iPhone 17 Pro at 136 tok/s, Galaxy S25 Ultra at 91 tok/s on a ~450M model.
- OTA model updates and model versioning built in.

**Watch-outs:**
- Younger project, less production validation than llama.cpp.
- Native Swift support is minimal — iOS path uses Kotlin Multiplatform bindings.
- Company-backed SDK introduces dependency risk not present with open-source runtimes.

**When to prefer Cactus:** If the built-in RAG support is valuable and you want the cleanest Flutter integration. Re-evaluate at implementation time — this project is moving fast.

---

## 5. Agentic Orchestration on-Device: What Not To Do

**Do not run Google ADK on-device.** ADK is a cloud orchestration framework designed for server-side agent pipelines with managed state, Firestore integration, and Cloud Run infrastructure. It is not designed for mobile execution.

**Do not port the 4-agent pipeline to device.** The multi-agent pattern with tool calling is architecturally appropriate for the cloud backend, where models are large and tool calling is reliable. On a 2B model with limited context and unstable tool calling, it introduces complexity with no gain.

**What to do instead:** Write a thin, sequential Dart orchestrator directly in the Flutter app. The on-device path is not agentic in the ADK sense — it is a series of direct inference calls managed by Dart logic:

```
Session starts
  → ContextAgent response arrives from Cloud Run (always cloud)
  → Device path: Dart loads on-device model if not already loaded
  → Lesson text served from Cloud Run LessonAgent (or optionally on-device)
  → Quiz question arrives
  → Learner answers
  → Dart calls OnDeviceInferenceProvider.evaluateAnswer(...)
  → If wrong twice: Dart calls OnDeviceInferenceProvider.generateHelpTurn(...)
  → After session: SummaryAgent and FSRS run on Cloud Run (always cloud)
```

The Dart orchestrator replaces only the LLM inference calls. All data reads/writes (Firestore, Cloud SQL via Cloud Run) remain cloud-side.

---

## 6. Cost and Quality Trade-offs

| Dimension | Cloud (Gemini 2.0 Flash) | On-Device (Gemma 3n 2B) |
|---|---|---|
| Quiz evaluation quality | High — large model, reliable structured output | Good enough — binary eval with short explanation |
| Help dialogue quality | High | Acceptable — 3 turns, constrained scope |
| Latency (first token) | 300–800ms (network + inference) | 50–200ms (local NPU) |
| Cost per session | ~$0.00051 | $0 inference; ~$0.0002 Cloud Run for context + summary |
| Offline capability | ❌ Requires connectivity | ✅ Lesson + quiz + help work offline |
| Model size overhead | None (API) | 1–2 GB download, ~1 GB RAM |
| Output reliability | High | Moderate — prompt engineering required for structured output |

**The honest cost story:** On-device saves inference cost (~$0.00035/session), not infrastructure cost. At 1,000 active learners running one session/day, that is ~$10.50/month saved. Meaningful but not transformative at MVP scale. The more compelling argument is offline capability and privacy, not pure cost.

---

## 7. Implementation Checklist (When You Get Here)

### Pre-Implementation Gates
- [ ] Trial launch complete and real usage data collected
- [ ] Cloud pipeline stable with no critical bugs
- [ ] Decided on target scope: quiz eval only, or quiz + help dialogue
- [ ] Evaluated LiteRT-LM tool calling stability at implementation time — has it matured?

### Architecture Decisions to Lock In Now (Before Phase 5)
- [ ] Define `InferenceProvider` abstract interface in Dart before writing any session code
- [ ] Implement `DeviceCapabilityChecker` service — RAM threshold (≥6 GB), chipset generation check
- [ ] Agree on settings UX: where does the user toggle on-device mode?
- [ ] Decide model distribution endpoint: Firebase Storage vs signed GCS URL

### When Implementing (Phase 7+)
- [ ] Start with `flutter_gemma` + Gemma 3n E2B — validate on Android first, then iOS
- [ ] Implement model download flow: size disclosure, progress indicator, hash verification, cancellation
- [ ] Implement model warm-up: load model at app foreground, not at session start (hides latency)
- [ ] Write structured output prompts — do not assume tool calling works; use JSON-in-text prompting with a Dart parser
- [ ] A/B test on-device vs cloud output quality on quiz evaluation — flag sessions for review if on-device mode active
- [ ] Add telemetry: log `inference_mode` (cloud vs on_device) per session for quality monitoring
- [ ] Test on a physical low-end device (e.g. older Android with 6 GB RAM) — do not rely only on flagship benchmarks
- [ ] Confirm model format requirements per platform before download implementation (`.task` vs `.litertlm`)

---

## 8. Decision Log

| Decision | Choice | Rationale |
|---|---|---|
| Flutter: keep or replace? | ✅ Keep | Best cross-platform coverage for Android + iOS + desktop. Actively aligned with Google's on-device AI direction as of 2026. |
| On-device as default or opt-in? | Opt-in, capability-gated | Avoids degraded experience on older devices. Model download is user-initiated. |
| On-device scope (Phase 7) | Quiz eval + HelpAgent | Highest frequency, lowest quality risk. Content retrieval stays cloud. |
| Runtime preference | LiteRT-LM / `flutter_gemma` first | GCP-aligned; Gemma model consistency; NPU acceleration on both platforms. |
| ADK on-device? | ❌ No | ADK is a cloud framework. On-device orchestration is thin Dart logic. |
| Full pipeline on-device? | ❌ No | ContextAgent and SummaryAgent always run on Cloud Run (Firestore dependency). |
| When to implement? | Post-trial (Phase 7+) | Tool calling at the edge is not production-stable yet. Build after trial validation. |

---

*Appendix written April 2026. Evaluated against learning_system_spec.md v1.0 and development_roadmap.md (Phase 0–6 structure). Reassess tooling choices (LiteRT-LM stability, Cactus maturity) at Phase 7 planning — this space is moving fast.*
